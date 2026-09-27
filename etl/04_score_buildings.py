"""
04_score_buildings.py — Score every residential building by the number of
trees visible from just outside its facade.

For each municipality with a merged viewshed (data/processed/<name>_viewshed.tif):
  1. Residential buildings: BAG footprints (config.PROVINCE_BUILDINGS_GPKG)
     with at least one address (config.PROVINCE_ADDRESSES_GPKG) in use whose
     uses include "woonfunctie" — mixed buildings (shop + homes) count too.
  2. Facade ring: the area within FACADE_RING_M outside the footprint,
     minus every footprint (its own and its neighbours' — shared walls in
     terraced housing give roof pixels, not street-level ones).
  3. Score = max viewshed value in that ring ("if one spot in front of the
     facade sees N trees, the building gets N"). Why the ring and not the
     footprint: the viewshed target height (1.8 m) is added to the surface
     model, which inside a footprint is the roof — 1.8 m above the roof is
     not what anyone living there sees.
  Buildings with no ring pixel at all (fully enclosed) get score NULL.

Buildings are those whose centroid lies inside the viewshed raster, so a
building near a municipality edge may appear in both municipalities' output
(with the same score — the viewsheds are computed with cross-boundary
context).

Output: data/processed/<name>_woningen.gpkg, footprint polygons with
  pand_id, n_woningen (addresses with woonfunctie), bomen_zichtbaar
  (score), klasse (0 | 1-2 | 3-5 | 6-7 | 8+, as in the QGIS styling).

Usage:
    python etl/04_score_buildings.py
"""

import sys
import time
import logging
from collections import defaultdict

import numpy as np

import config

try:
    from osgeo import gdal, ogr
except ImportError as exc:
    sys.exit(f"ERROR: cannot import osgeo — {exc}")

gdal.UseExceptions()
ogr.UseExceptions()

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

CLASSES = [(0, "0"), (2, "1-2"), (5, "3-5"), (7, "6-7"), (float("inf"), "8+")]


def klasse(n):
    if n is None:
        return None
    return next(label for upper, label in CLASSES if n <= upper)


def residential_building_ids(xmin, ymin, xmax, ymax):
    """{building id: number of residential addresses} within the extent."""
    ds = ogr.Open(str(config.PROVINCE_ADDRESSES_GPKG), 0)
    layer = ds.GetLayer(0)
    layer.SetSpatialFilterRect(xmin, ymin, xmax, ymax)
    counts = defaultdict(int)
    for f in layer:
        uses = (f.GetField(config.BAG_ADDRESS_USE) or "").lower()
        status = (f.GetField(config.BAG_ADDRESS_STATUS) or "").lower()
        if "woonfunctie" not in uses or "niet" in status or "ingetrokken" in status:
            continue
        for bid in (f.GetField(config.BAG_ADDRESS_BUILDING_ID) or "").split(","):
            if bid.strip():
                counts[bid.strip()] += 1
    return counts


def score_municipality(name):
    vs_path = config.PROCESSED_DIR / f"{name}_viewshed.tif"
    if not vs_path.exists():
        log.warning(f"[{name}] no viewshed output ({vs_path.name}) — skipped")
        return None
    vs_ds = gdal.Open(str(vs_path))
    vs_band = vs_ds.GetRasterBand(1)
    gt = vs_ds.GetGeoTransform()
    nx, ny = vs_ds.RasterXSize, vs_ds.RasterYSize
    xmin, ymax = gt[0], gt[3]
    xmax, ymin = xmin + nx * gt[1], ymax + ny * gt[5]

    homes = residential_building_ids(xmin, ymin, xmax, ymax)
    log.info(f"[{name}] {sum(homes.values()):,} residential addresses in {len(homes):,} buildings")

    bld_ds = ogr.Open(str(config.PROVINCE_BUILDINGS_GPKG), 0)
    bld_layer = bld_ds.GetLayer(0)
    srs = bld_layer.GetSpatialRef()
    # An in-memory copy of all footprints around the extent, for masking
    # neighbours out of each ring (needs its own spatial filter per building)
    margin = config.FACADE_RING_M + 1
    bld_layer.SetSpatialFilterRect(xmin - margin, ymin - margin, xmax + margin, ymax + margin)
    all_ds = ogr.GetDriverByName("Memory").CreateDataSource("")
    all_layer = all_ds.CopyLayer(bld_layer, "footprints")

    out_path = config.PROCESSED_DIR / f"{name}_woningen.gpkg"
    if out_path.exists():
        out_path.unlink()
    out_ds = ogr.GetDriverByName("GPKG").CreateDataSource(str(out_path))
    out_layer = out_ds.CreateLayer(f"{name}_woningen", srs, ogr.wkbMultiPolygon)
    for fname, ftype in (("pand_id", ogr.OFTString), ("n_woningen", ogr.OFTInteger),
                         ("bomen_zichtbaar", ogr.OFTInteger), ("klasse", ogr.OFTString)):
        out_layer.CreateField(ogr.FieldDefn(fname, ftype))
    defn = out_layer.GetLayerDefn()

    mem = gdal.GetDriverByName("MEM")
    px = abs(gt[1])
    n_scored = n_enclosed = 0
    candidates, seen = [], set()
    for feat in all_layer:
        bid = feat.GetField(config.BAG_BUILDING_ID)
        if bid in homes and bid not in seen:     # a source can hold duplicates
            seen.add(bid)
            geom = feat.GetGeometryRef().Clone()
            c = geom.Centroid()
            if xmin <= c.GetX() < xmax and ymin <= c.GetY() < ymax:
                candidates.append((bid, geom))

    out_layer.StartTransaction()
    for bid, geom in candidates:

        # Pixel window around the building + ring
        gx0, gx1, gy0, gy1 = geom.GetEnvelope()
        c0 = max(0, int(np.floor((gx0 - margin - xmin) / px)))
        c1 = min(nx, int(np.ceil((gx1 + margin - xmin) / px)))
        r0 = max(0, int(np.floor((ymax - gy1 - margin) / px)))
        r1 = min(ny, int(np.ceil((ymax - gy0 + margin) / px)))
        score = None
        if c1 > c0 and r1 > r0:
            wgt = (xmin + c0 * px, px, 0.0, ymax - r0 * px, 0.0, -px)
            mask_ds = mem.Create("", c1 - c0, r1 - r0, 1, gdal.GDT_Byte)
            mask_ds.SetGeoTransform(wgt)
            ring_ds = ogr.GetDriverByName("Memory").CreateDataSource("")
            ring_layer = ring_ds.CreateLayer("ring", srs, ogr.wkbPolygon)
            ring_feat = ogr.Feature(ring_layer.GetLayerDefn())
            ring_feat.SetGeometry(geom.Buffer(config.FACADE_RING_M))
            ring_layer.CreateFeature(ring_feat)
            gdal.RasterizeLayer(mask_ds, [1], ring_layer, burn_values=[1])
            # every footprint in the window (own + neighbours) back to 0
            wx0, wy1 = wgt[0], wgt[3]
            all_layer.SetSpatialFilterRect(wx0, wy1 - (r1 - r0) * px, wx0 + (c1 - c0) * px, wy1)
            gdal.RasterizeLayer(mask_ds, [1], all_layer, burn_values=[0])
            ring_px = mask_ds.GetRasterBand(1).ReadAsArray().astype(bool)
            if ring_px.any():
                vals = vs_band.ReadAsArray(c0, r0, c1 - c0, r1 - r0)
                score = int(vals[ring_px].max())
        if score is None:
            n_enclosed += 1
        else:
            n_scored += 1

        out = ogr.Feature(defn)
        out.SetField("pand_id", bid)
        out.SetField("n_woningen", homes[bid])
        if score is not None:
            out.SetField("bomen_zichtbaar", score)
            out.SetField("klasse", klasse(score))
        out.SetGeometry(ogr.ForceToMultiPolygon(geom))
        out_layer.CreateFeature(out)
    out_layer.CommitTransaction()
    out_ds = None
    log.info(f"[{name}] {n_scored:,} buildings scored, {n_enclosed:,} without facade ring -> {out_path.name}")
    return n_scored


def main():
    for path in (config.PROVINCE_BUILDINGS_GPKG, config.PROVINCE_ADDRESSES_GPKG):
        if not path.exists():
            sys.exit(f"ERROR: not found: {path} (BAG footprints / addresses, see README)")
    for name, _dem, _trees in config.municipality_pairs():
        t0 = time.perf_counter()
        n = score_municipality(name)
        if n is not None:
            config.log_benchmark(name, "score_buildings", time.perf_counter() - t0, trees="")


if __name__ == "__main__":
    main()
