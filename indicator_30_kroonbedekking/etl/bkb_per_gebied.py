"""
bkb_per_gebied.py — Crown area per area (buurt, wijk, ...) from the BKB 2024
canopy raster (boomkroonbedekking 2024, Friedenau Society, from lidar).

The raster covers the Netherlands at 0.25 m (1 = crown, 0 = no crown,
1.2 M x 1.6 M pixels, 1.9 TB if read at once), so it is never read whole:
  1. The extent of the selected areas is cut into tiles of TILE_PX x TILE_PX
     pixels (4000 = 1 km, 16 MB of raster per tile).
  2. Per tile: read the raster window, burn the areas that touch the tile
     into a grid of the same pixels (a pixel belongs to the area that holds
     its centre, so every pixel counts for exactly one area: no double
     counting on boundaries), and count per area the crown pixels and all
     pixels.
  3. The counts are added over all tiles. Tiles run in parallel processes;
     each holds about 100 MB.

Output: a CSV with per area its code, crown pixels, all pixels, and both as
m2 (pixel = 0.0625 m2). The area from pixels is the polygon's area within
the raster; the canopy percentage is computed from these counts later (with
the denominator of choice, e.g. CBS land area).

Usage (from the repository root):
    python indicator_30_kroonbedekking/etl/bkb_per_gebied.py AREAS LAYER CODE_FIELD OUT_CSV
           [--where "gemeentecode = 'GM0503'"] [--bkb TIF] [--workers 5]
e.g. the buurten of Delft (CBS 2025):
    ... data/interim/buurten.gpkg buurten buurtcode data/processed/bkb_delft.csv --where "gemeentecode = 'GM0503'"
"""

import argparse
import csv
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "indicator_3_bomen" / "etl"))
import config  # noqa: E402

from osgeo import gdal, ogr, osr  # noqa: E402
gdal.UseExceptions()
ogr.UseExceptions()

TILE_PX = 4000            # tile side in raster pixels (4000 x 0.25 m = 1 km)


def load_areas(path, layer, code_field, where):
    """The selected areas as (code, WKB) pairs, in the raster's CRS (RD New)."""
    ds = ogr.Open(str(path))
    lyr = ds.GetLayerByName(layer)
    if where:
        lyr.SetAttributeFilter(where)
    rd = osr.SpatialReference()
    rd.ImportFromEPSG(28992)
    src = lyr.GetSpatialRef()
    ct = None if src is None or src.IsSame(rd) else osr.CoordinateTransformation(src, rd)
    areas = []
    for f in lyr:
        g = f.GetGeometryRef().Clone()
        g.FlattenTo2D()
        if ct:
            g.Transform(ct)
        if not g.IsValid():
            g = g.MakeValid()
        areas.append((str(f.GetField(code_field)), g.ExportToWkb()))
    return areas


_worker = {}   # per process: raster band, geotransform, in-memory area layer


def _init(bkb, areas):
    """Per worker process: open the raster once and build an in-memory layer
    of the areas with an integer id (1..n) to burn."""
    ds = gdal.Open(str(bkb))
    mem = ogr.GetDriverByName("MEM").CreateDataSource("")
    rd = osr.SpatialReference()
    rd.ImportFromEPSG(28992)
    lyr = mem.CreateLayer("areas", rd, ogr.wkbMultiPolygon)
    lyr.CreateField(ogr.FieldDefn("id", ogr.OFTInteger))
    for i, (_, wkb) in enumerate(areas, start=1):
        f = ogr.Feature(lyr.GetLayerDefn())
        f.SetField("id", i)
        f.SetGeometry(ogr.CreateGeometryFromWkb(wkb))
        lyr.CreateFeature(f)
    _worker.update(ds=ds, band=ds.GetRasterBand(1), gt=ds.GetGeoTransform(), mem=mem, lyr=lyr, n=len(areas))


def _tile(window):
    """Crown pixels and all pixels per area id within one raster window."""
    col, row, w, h = window
    gt, lyr, n = _worker["gt"], _worker["lyr"], _worker["n"]
    x0, y0 = gt[0] + col * gt[1], gt[3] + row * gt[5]
    x1, y1 = x0 + w * gt[1], y0 + h * gt[5]
    lyr.SetSpatialFilterRect(x0, y1, x1, y0)
    if lyr.GetFeatureCount() == 0:          # no area in this tile: skip the read
        return None
    # Burn the area ids: a pixel gets the area that holds its centre
    ids = gdal.GetDriverByName("MEM").Create("", w, h, 1, gdal.GDT_UInt32)
    ids.SetGeoTransform((x0, gt[1], 0, y0, 0, gt[5]))
    gdal.RasterizeLayer(ids, [1], lyr, options=["ATTRIBUTE=id"])
    idarr = ids.GetRasterBand(1).ReadAsArray()
    crown = _worker["band"].ReadAsArray(col, row, w, h) == 1
    every = np.bincount(idarr.ravel(), minlength=n + 1)
    crowns = np.bincount(idarr[crown], minlength=n + 1)
    return crowns, every


def count(areas, bkb, workers):
    """Crown pixels and all pixels per area: two arrays indexed like `areas`
    (code, WKB), plus the pixel size in m2."""
    t0 = time.perf_counter()
    ds = gdal.Open(str(bkb))
    gt = ds.GetGeoTransform()
    px = gt[1]
    # Extent of the areas, snapped outward to the raster's pixel grid
    env = [ogr.CreateGeometryFromWkb(w).GetEnvelope() for _, w in areas]
    xmin, xmax = min(e[0] for e in env), max(e[1] for e in env)
    ymin, ymax = min(e[2] for e in env), max(e[3] for e in env)
    c0, c1 = int((xmin - gt[0]) // px), int(-(-(xmax - gt[0]) // px))
    r0, r1 = int((gt[3] - ymax) // px), int(-(-(gt[3] - ymin) // px))
    windows = [(c, r, min(TILE_PX, c1 - c), min(TILE_PX, r1 - r))
               for r in range(r0, r1, TILE_PX) for c in range(c0, c1, TILE_PX)]
    print(f"{len(areas)} areas, {len(windows)} tiles of up to {TILE_PX * px:.0f} m, {workers} workers")

    n = len(areas)
    crowns = np.zeros(n + 1, dtype=np.int64)
    every = np.zeros(n + 1, dtype=np.int64)
    done = 0
    with ProcessPoolExecutor(workers, initializer=_init, initargs=(bkb, areas)) as pool:
        for res in pool.map(_tile, windows, chunksize=4):
            done += 1
            if res is not None:
                crowns += res[0]
                every += res[1]
            if done % 500 == 0 or done == len(windows):
                print(f"  {done}/{len(windows)} tiles, {time.perf_counter() - t0:.0f} s", flush=True)
    return crowns[1:], every[1:], px * px


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("areas", type=Path)
    p.add_argument("layer")
    p.add_argument("code_field")
    p.add_argument("out", type=Path)
    p.add_argument("--where", default=None, help="OGR SQL filter on the areas")
    p.add_argument("--bkb", type=Path, default=config.BKB_TIF, help="the BKB raster")
    p.add_argument("--workers", type=int, default=5)
    a = p.parse_args()

    t0 = time.perf_counter()
    areas = load_areas(a.areas, a.layer, a.code_field, a.where)
    if not areas:
        sys.exit("No areas selected.")
    crowns, every, pixel_m2 = count(areas, a.bkb, a.workers)
    n = len(areas)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["code", "kroon_px", "gebied_px", "kroon_m2", "gebied_m2"])
        for i, (code, _) in enumerate(areas):
            w.writerow([code, crowns[i], every[i], round(crowns[i] * pixel_m2, 2), round(every[i] * pixel_m2, 2)])
    print(f"Wrote {a.out} ({n} areas) in {time.perf_counter() - t0:.0f} s")


if __name__ == "__main__":
    main()
