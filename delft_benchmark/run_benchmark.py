"""
run_benchmark.py — Delft viewshed run using attribute-based tree heights
(bomen_delft.shp's RASTERVALU field) instead of this pipeline's normal
DEM-sampled height, for comparison against the main pipeline's output.

RASTERVALU is an absolute elevation (ExtractValuesToPoints on the AHN5
raw DSM, m NAP — see bomen_delft.shp.xml), while ViewshedGenerate adds
observerHeight on top of the DEM at the observer's pixel. So, as in the
main pipeline's _observer_offset(), the value passed is RASTERVALU minus
the DEM at that pixel, placing the observer at the RASTERVALU elevation.
(Before 2026-09-27 RASTERVALU was passed directly, double-counting.)

Reuses the same tiling and viewshed-accumulation logic as the main
pipeline (etl/01_tile_dem.py, etl/02_compute_viewsheds.py) so the only
methodological difference from the "real" Delft run is the height source.

Self-contained: reads DELFT_05_RUW + bomen_delft.gpkg from this folder,
writes tiles + final output into this folder. Does not touch the main
pipeline's data/ directories.

Usage:
    python delft_benchmark/run_benchmark.py
"""

import sys
import json
import time
from pathlib import Path

BENCH_DIR = Path(__file__).parent
sys.path.insert(0, str(BENCH_DIR.parent / "etl"))

import config  # noqa: E402 — sets up GDAL/OSGeo4W env

try:
    from osgeo import gdal, ogr
except ImportError as exc:
    sys.exit(f"ERROR: cannot import osgeo — {exc}")

gdal.UseExceptions()
ogr.UseExceptions()

import numpy as np  # noqa: E402

tile_dem_mod = __import__("01_tile_dem")
viewsheds_mod = __import__("02_compute_viewsheds")

DEM_PATH = BENCH_DIR / "DELFT_05_RUW"
TREES_PATH = BENCH_DIR / "bomen_delft.gpkg"
TILES_DEM_DIR = BENCH_DIR / "tiles_dem"
TILES_VIEWSHED_DIR = BENCH_DIR / "tiles_viewshed"
TILE_INDEX_PATH = TILES_DEM_DIR / "tile_index.json"
OUTPUT_PATH = BENCH_DIR / "Delft_benchmark_viewshed.tif"

HEIGHT_FIELD = "RASTERVALU"


def _attribute_offset(dem_band, gt, nx, ny, x, y, raw_h):
    """observerHeight that puts the observer at the absolute elevation
    raw_h (RASTERVALU): raw_h minus the DEM at the observer's pixel, found
    with floor() as GDAL does. Falls back to OBSERVER_HEIGHT if raw_h or
    that pixel is missing."""
    try:
        target = float(raw_h)
    except (TypeError, ValueError):
        return config.OBSERVER_HEIGHT
    col = int(np.floor((x - gt[0]) / gt[1]))
    row = int(np.floor((y - gt[3]) / gt[5]))
    if not np.isfinite(target) or not (0 <= col < nx and 0 <= row < ny):
        return config.OBSERVER_HEIGHT
    base = float(dem_band.ReadAsArray(col, row, 1, 1)[0, 0])
    if not np.isfinite(base) or base == dem_band.GetNoDataValue():
        return config.OBSERVER_HEIGHT
    # RASTERVALU is bilinearly interpolated, so it can sit slightly below
    # the pixel's own value; never go below the surface.
    return max(0.0, target - base)


def process_tile_with_attribute_height(args):
    """Same as 02_compute_viewsheds.process_tile, except tree height comes
    from HEIGHT_FIELD on the tree layer instead of DEM sampling."""
    tile_id, tile_info, trees_db_path, output_dir_str, resume = args
    output_dir = Path(output_dir_str)
    out_path = output_dir / f"{tile_id}.tif"

    if resume and out_path.exists():
        return tile_id, 0, "skipped (already exists)"

    tile_path = Path(tile_info["path"])
    if not tile_path.exists():
        return tile_id, 0, f"ERROR: tile file missing {tile_path}"

    xmin = tile_info["buf_xmin"]
    xmax = tile_info["buf_xmax"]
    ymin = tile_info["buf_ymin"]
    ymax = tile_info["buf_ymax"]

    dem_ds = gdal.Open(str(tile_path))
    if dem_ds is None:
        return tile_id, 0, f"ERROR: cannot open DEM tile {tile_path}"

    dem_band = dem_ds.GetRasterBand(1)
    gt = dem_ds.GetGeoTransform()
    proj = dem_ds.GetProjection()
    nx = dem_ds.RasterXSize
    ny = dem_ds.RasterYSize

    _use_python_api = hasattr(gdal, "ViewshedGenerate")

    accumulator = np.zeros((ny, nx), dtype=np.uint32)
    n_trees = 0

    db_path = Path(trees_db_path)
    try:
        trees_ds = ogr.Open(str(db_path), 0)
        layer = trees_ds.GetLayer(0)
        ring = ogr.Geometry(ogr.wkbLinearRing)
        ring.AddPoint(xmin, ymin)
        ring.AddPoint(xmax, ymin)
        ring.AddPoint(xmax, ymax)
        ring.AddPoint(xmin, ymax)
        ring.AddPoint(xmin, ymin)
        bbox_poly = ogr.Geometry(ogr.wkbPolygon)
        bbox_poly.AddGeometry(ring)
        layer.SetSpatialFilter(bbox_poly)

        for feat in layer:
            geom = feat.GetGeometryRef()
            if geom is None:
                continue
            geom_type = geom.GetGeometryType()
            pt = geom.Centroid() if geom_type not in (ogr.wkbPoint, ogr.wkbPoint25D) else geom
            x, y = pt.GetX(), pt.GetY()

            h = _attribute_offset(dem_band, gt, nx, ny, x, y, feat.GetField(HEIGHT_FIELD))

            if _use_python_api:
                arr, arr_gt = viewsheds_mod._viewshed_python_api(dem_band, x, y, h)
            else:
                arr, arr_gt = viewsheds_mod._viewshed_subprocess(tile_path, x, y, h)

            if arr is not None:
                viewsheds_mod._paste_into_accumulator(accumulator, arr, arr_gt, gt)
            n_trees += 1

        layer.SetSpatialFilter(None)
        trees_ds = None
    except Exception as exc:
        dem_ds = None
        return tile_id, n_trees, f"ERROR during tree iteration: {exc}"

    dem_ds = None

    if n_trees == 0:
        return tile_id, 0, "no trees in buffered extent — skipped"

    col0 = tile_info["inner_col_off"]
    row0 = tile_info["inner_row_off"]
    inner_w = tile_info["inner_w_px"]
    inner_h = tile_info["inner_h_px"]
    inner_arr = accumulator[row0:row0 + inner_h, col0:col0 + inner_w]
    inner_gt = (
        gt[0] + col0 * gt[1], gt[1], gt[2],
        gt[3] + row0 * gt[5], gt[4], gt[5],
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    driver = gdal.GetDriverByName("GTiff")
    out_ds = driver.Create(
        str(out_path), inner_w, inner_h, 1, gdal.GDT_UInt32,
        options=["COMPRESS=LZW", "TILED=YES", "BIGTIFF=IF_SAFER"],
    )
    out_ds.SetGeoTransform(inner_gt)
    out_ds.SetProjection(proj)
    out_ds.GetRasterBand(1).WriteArray(inner_arr)
    out_ds.FlushCache()
    out_ds = None

    return tile_id, n_trees, f"OK — {n_trees} trees"


def main():
    print(f"DEM: {DEM_PATH}")
    print(f"Trees: {TREES_PATH}  (height field: {HEIGHT_FIELD})")

    # Stage 1: tile the DEM (reusing the main pipeline's tiling function;
    # pass DEM_PATH as its own "province" source since there's no
    # cross-municipality context needed for this standalone benchmark).
    t0 = time.perf_counter()
    dem_ds = gdal.Open(str(DEM_PATH))
    n_tiles = tile_dem_mod.tile_dem(DEM_PATH, TILES_DEM_DIR, TILE_INDEX_PATH, dem_ds)
    dem_ds = None
    print(f"Stage 1: {n_tiles} tiles in {time.perf_counter()-t0:.1f}s")

    # Stage 2: compute viewsheds per tile using the attribute-based height
    t0 = time.perf_counter()
    with open(TILE_INDEX_PATH) as fh:
        tile_index = json.load(fh)
    TILES_VIEWSHED_DIR.mkdir(parents=True, exist_ok=True)

    completed = 0
    errors = 0
    total_trees = 0
    for tile_id, tile_info in tile_index.items():
        tid, n_trees, msg = process_tile_with_attribute_height(
            (tile_id, tile_info, str(TREES_PATH), str(TILES_VIEWSHED_DIR), False)
        )
        completed += 1
        total_trees += n_trees
        if msg.startswith("ERROR"):
            errors += 1
            print(f"  [{completed}/{n_tiles}] {tid}: {msg}")
        if completed % 20 == 0 or completed == n_tiles:
            print(f"  {completed}/{n_tiles} tiles done, {total_trees:,} tree viewsheds so far")

    print(f"Stage 2: {completed} tiles, {errors} errors, {total_trees:,} tree viewsheds in {time.perf_counter()-t0:.1f}s")

    # Stage 3: merge
    t0 = time.perf_counter()
    tile_files = sorted(TILES_VIEWSHED_DIR.glob("tile_*.tif"))
    if not tile_files:
        sys.exit("ERROR: no viewshed tiles produced — nothing to merge.")

    vrt_path = str(BENCH_DIR / "benchmark_mosaic.vrt")
    gdal.BuildVRT(vrt_path, [str(p) for p in tile_files],
                  options=gdal.BuildVRTOptions(resolution="highest", resampleAlg="nearest", addAlpha=False))

    gdal.Translate(
        str(OUTPUT_PATH), vrt_path,
        options=gdal.TranslateOptions(
            format="COG", outputType=gdal.GDT_UInt32,
            creationOptions=["COMPRESS=DEFLATE", "PREDICTOR=STANDARD", "BLOCKSIZE=512",
                             "BIGTIFF=YES", "OVERVIEWS=AUTO", "RESAMPLING=AVERAGE"],
            callback=gdal.TermProgress_nocb,
        ),
    )
    print(f"Stage 3: merged in {time.perf_counter()-t0:.1f}s")

    ds = gdal.Open(str(OUTPUT_PATH))
    band = ds.GetRasterBand(1)
    stats = band.ComputeStatistics(False)
    print(f"Result stats — min: {stats[0]:.0f}  max: {stats[1]:.0f}  mean: {stats[2]:.2f}  stddev: {stats[3]:.2f}")
    ds = None

    print(f"\nFinal output: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
