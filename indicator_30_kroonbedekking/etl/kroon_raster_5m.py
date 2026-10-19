"""
kroon_raster_5m.py — Crown and land counted per 5 m cell, for the 30 per home.

BKB 2024 (1 = crown) and land cover 2024 share one 0.25 m grid. Every 5 m
cell holds 20 x 20 of those pixels; this script counts per cell
  band 1  crown pixels on land (crown over water is left out, so a cell's
          crown never exceeds its land)
  band 2  land pixels (land cover in config.LAND_CLASSES: not water, not a
          bridge over water, not the sea outside the raster)
as UInt16 (0-400). kroon_500m_woningen.py adds these up within 500 m of
every home.

The extent is that of the homes (<Province>_woningen.gpkg) plus the buffer
and a margin, in 1 km blocks (4000 x 4000 pixels, 16 MB per raster) that run
in parallel processes.

Output: config.KROON_GRID_TIF (data/interim/kroon_land_5m.tif)

Usage (from the repository root, with the OSGeo4W Python):
    python indicator_30_kroonbedekking/etl/kroon_raster_5m.py [--workers 5]
"""

import argparse
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "indicator_3_bomen" / "etl"))
import config  # noqa: E402

from osgeo import gdal, ogr  # noqa: E402
gdal.UseExceptions()
ogr.UseExceptions()

BLOCK_M = 1000
PX = 0.25                                   # source pixel (both rasters)
F = int(round(config.KROON_GRID_RES / PX))  # source pixels per cell side (20)

_w = {}


def _init():
    bkb = gdal.Open(str(config.BKB_TIF))
    land = gdal.Open(str(config.LANDBEDEKKING_TIF))
    _w.update(bkb=bkb, land=land, bkb_gt=bkb.GetGeoTransform(), land_gt=land.GetGeoTransform(),
              classes=np.array(config.LAND_CLASSES, dtype=np.uint8))


def _read(ds, gt, x0, y1, n):
    """n x n source pixels with top-left corner (x0, y1); zeros outside the raster."""
    col, row = int(round((x0 - gt[0]) / PX)), int(round((gt[3] - y1) / PX))
    out = np.zeros((n, n), dtype=np.uint8)
    c0, r0 = max(col, 0), max(row, 0)
    c1, r1 = min(col + n, ds.RasterXSize), min(row + n, ds.RasterYSize)
    if c1 > c0 and r1 > r0:
        out[r0 - row:r1 - row, c0 - col:c1 - col] = ds.GetRasterBand(1).ReadAsArray(c0, r0, c1 - c0, r1 - r0)
    return out


def _block(xy):
    """Crown-on-land and land counts per cell for the block with lower-left corner xy."""
    x0, y0 = xy
    n = int(BLOCK_M / PX)
    land = np.isin(_read(_w["land"], _w["land_gt"], x0, y0 + BLOCK_M, n), _w["classes"])
    if not land.any():
        return xy, None
    crown = (_read(_w["bkb"], _w["bkb_gt"], x0, y0 + BLOCK_M, n) == 1) & land
    m = n // F
    return xy, (crown.reshape(m, F, m, F).sum(axis=(1, 3), dtype=np.uint16),
                land.reshape(m, F, m, F).sum(axis=(1, 3), dtype=np.uint16))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", type=int, default=5)
    args = ap.parse_args()
    t0 = time.perf_counter()

    homes_ds = ogr.Open(str(config.PROCESSED_DIR / f"{config.PROVINCE_SLUG}_woningen.gpkg"))
    homes = homes_ds.GetLayer(0)
    xmin, xmax, ymin, ymax = homes.GetExtent()
    pad = config.KROON_BUFFER_M + 100
    xmin, ymin = (np.floor((v - pad) / BLOCK_M) * BLOCK_M for v in (xmin, ymin))
    xmax, ymax = (np.ceil((v + pad) / BLOCK_M) * BLOCK_M for v in (xmax, ymax))
    blocks = [(x, y) for y in np.arange(ymin, ymax, BLOCK_M) for x in np.arange(xmin, xmax, BLOCK_M)]
    res = config.KROON_GRID_RES
    nx, ny = int((xmax - xmin) / res), int((ymax - ymin) / res)
    m = int(BLOCK_M / res)
    print(f"{len(blocks)} blocks of {BLOCK_M} m, grid {nx} x {ny} cells of {res:g} m, {args.workers} workers", flush=True)

    crown = np.zeros((ny, nx), dtype=np.uint16)
    land = np.zeros((ny, nx), dtype=np.uint16)
    with ProcessPoolExecutor(args.workers, initializer=_init) as pool:
        for i, ((x, y), r) in enumerate(pool.map(_block, blocks, chunksize=8), start=1):
            if r is not None:
                col, row = int((x - xmin) / res), int((ymax - y - BLOCK_M) / res)
                crown[row:row + m, col:col + m], land[row:row + m, col:col + m] = r
            if i % 1000 == 0 or i == len(blocks):
                print(f"  {i}/{len(blocks)} blocks, {time.perf_counter() - t0:.0f} s", flush=True)

    out = config.KROON_GRID_TIF
    tmp = out.with_suffix(".partial.tif")
    ds = gdal.GetDriverByName("GTiff").Create(str(tmp), nx, ny, 2, gdal.GDT_UInt16,
                                              options=["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=2", "BIGTIFF=IF_SAFER"])
    ds.SetGeoTransform((xmin, res, 0, ymax, 0, -res))
    ds.SetProjection(homes.GetSpatialRef().ExportToWkt())
    for b, (arr, desc) in enumerate(((crown, "crown pixels on land"), (land, "land pixels")), start=1):
        band = ds.GetRasterBand(b)
        band.WriteArray(arr)
        band.SetDescription(f"{desc} (of {F * F} per cell)")
    ds = None
    tmp.replace(out)
    px = PX * PX / 10_000
    print(f"Wrote {out} in {time.perf_counter() - t0:.0f} s: land {land.sum(dtype=np.int64) * px:,.0f} ha, "
          f"crown on land {crown.sum(dtype=np.int64) * px:,.0f} ha")


if __name__ == "__main__":
    main()
