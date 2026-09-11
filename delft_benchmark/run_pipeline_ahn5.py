"""
run_pipeline_ahn5.py — Delft-only run of the REAL pipeline (01_tile_dem's
tile_dem, including the NoData-fill fix, and 02_compute_viewsheds's real
process_tile with DEM-sampled tree height) against the delft_benchmark
folder's AHN5 source data instead of the main pipeline's AHN4 data or
config.PROVINCE_DEM_VRT / config.PROVINCE_TREES_GPKG.

Self-contained: no cross-municipality context (the DEM tiling uses this
municipality's own raster as its own "province" source), no mixing with
AHN4 data. Reuses the pipeline's actual tile_dem() and process_tile()
functions unmodified — the only differences from a normal pipeline run are
the data source and (via etl/config.py) TARGET_HEIGHT=1.8.

Usage:
    python delft_benchmark/run_pipeline_ahn5.py
"""

import sys
import json
import time
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

BENCH_DIR = Path(__file__).parent
sys.path.insert(0, str(BENCH_DIR.parent / "etl"))

import config  # noqa: E402

try:
    from osgeo import gdal
except ImportError as exc:
    sys.exit(f"ERROR: cannot import osgeo — {exc}")

gdal.UseExceptions()

tile_dem_mod = __import__("01_tile_dem")
viewsheds_mod = __import__("02_compute_viewsheds")

DEM_PATH = BENCH_DIR / "DELFT_05_RUW"
TREES_PATH = BENCH_DIR / "bomen_delft.gpkg"
TILES_DEM_DIR = BENCH_DIR / "tiles_dem"
TILES_VIEWSHED_DIR = BENCH_DIR / "tiles_viewshed_v2"
TILE_INDEX_PATH = TILES_DEM_DIR / "tile_index.json"
OUTPUT_PATH = BENCH_DIR / "Delft_ahn5_pipeline_viewshed.tif"


def main():
    print(f"DEM: {DEM_PATH}")
    print(f"Trees: {TREES_PATH}  (real pipeline DEM-sampled height, TARGET_HEIGHT={config.TARGET_HEIGHT})")

    # Stage 1: tile the DEM — real tile_dem(), NoData-fill included, own
    # raster as its own province source (no AHN4 cross-municipality mixing).
    t0 = time.perf_counter()
    dem_ds = gdal.Open(str(DEM_PATH))
    n_tiles = tile_dem_mod.tile_dem(DEM_PATH, TILES_DEM_DIR, TILE_INDEX_PATH, dem_ds)
    dem_ds = None
    print(f"Stage 1: {n_tiles} tiles in {time.perf_counter()-t0:.1f}s")

    # Stage 2: real process_tile(), pointed at this folder's tree layer
    # instead of config.PROVINCE_TREES_GPKG.
    t0 = time.perf_counter()
    with open(TILE_INDEX_PATH) as fh:
        tile_index = json.load(fh)
    TILES_VIEWSHED_DIR.mkdir(parents=True, exist_ok=True)

    work_items = [
        (tile_id, tile_info, str(TREES_PATH), str(TILES_VIEWSHED_DIR), False)
        for tile_id, tile_info in tile_index.items()
    ]

    completed = 0
    errors = 0
    total_trees = 0
    with ProcessPoolExecutor(max_workers=config.NUM_WORKERS) as pool:
        futures = {pool.submit(viewsheds_mod.process_tile, item): item[0] for item in work_items}
        for future in as_completed(futures):
            tile_id = futures[future]
            try:
                tid, n_trees, msg = future.result()
            except Exception as exc:
                print(f"  {tile_id}: UNHANDLED EXCEPTION — {exc}")
                errors += 1
                completed += 1
                continue
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

    vrt_path = str(BENCH_DIR / "pipeline_ahn5_mosaic.vrt")
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
