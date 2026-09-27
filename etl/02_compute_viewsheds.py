"""
02_compute_viewsheds.py — Compute accumulated viewshed counts for millions of trees.

Algorithm
---------
For each municipality (config.municipality_pairs()):
  For every DEM tile (from that municipality's tile_index.json):
    1. Query config.PROVINCE_TREES_GPKG (all municipalities combined) for
       every tree within the tile's *buffered* extent — not just the inner
       extent, and not restricted to this municipality's own trees — since a
       tree just across a tile OR municipality boundary can still be within
       MAX_DISTANCE of an inner pixel on this side. (The same tree gets
       queried again by the neighbouring tile too; that's fine, see step 4.)
    2. For each tree, cut the DEM within MAX_DISTANCE into a small in-memory
       raster with the tree's own crown flattened (building pixels kept),
       place the observer at the canopy top (see _prepare_tree()), and call
       gdal.ViewshedGenerate() against that window
       buffered tile DEM with MAX_DISTANCE = 30 m, pasting the (small,
       observer-centred) result into a tile-sized accumulator at the right
       pixel offset.
    3. Accumulate visible-pixel counts in a uint32 numpy array.
    4. Crop the accumulator down to the tile's *inner* window and write only
       that to data/interim/viewshed_tiles/<name>/<tile_id>.tif. Cropping to
       the non-overlapping inner window (rather than writing the full
       buffered tile) is what makes tile outputs tile edge-to-edge with no
       overlap, so 03_merge_tiles.py's mosaic has no seam to get wrong.

Tiles with zero trees in their buffered extent are skipped.
Tiles are processed in parallel (NUM_WORKERS processes).

Usage:
    python etl/02_compute_viewsheds.py [--workers N] [--resume]

    --workers N   override NUM_WORKERS from config
    --resume      skip tiles whose output file already exists
"""

import sys
import json
import logging
import argparse
import time
import os
import subprocess
import tempfile
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np

import config

try:
    from osgeo import gdal, ogr, osr
except ImportError as exc:
    sys.exit(f"ERROR: cannot import osgeo — {exc}")

gdal.UseExceptions()
ogr.UseExceptions()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tree reading
# ---------------------------------------------------------------------------

def _open_tree_layer(db_path: Path, layer_name=None):
    """Open the OGR data source and return (ds, layer)."""
    ds = ogr.Open(str(db_path), 0)  # read-only
    if ds is None:
        raise RuntimeError(f"OGR cannot open {db_path}")
    layer = ds.GetLayerByName(layer_name) if layer_name else ds.GetLayer(0)
    if layer is None:
        raise RuntimeError(f"Layer '{layer_name}' not found in {db_path}")
    return ds, layer


def iter_tree_points_in_bbox(db_path: Path, xmin: float, ymin: float,
                              xmax: float, ymax: float, layer_name=None):
    """
    Generator that yields (x, y) for every tree whose geometry falls within
    [xmin,xmax] x [ymin,ymax].

    Uses OGR SetSpatialFilter for server-side (or index-assisted) filtering,
    so it is safe even for multi-million-row databases. Height is not
    determined here — see _prepare_tree(), which reads it from the DEM
    once the tile raster is available.
    """
    ds, layer = _open_tree_layer(db_path, layer_name)

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
        # Flatten to 2-D centroid (handles MultiPoint, etc.); wkbPoint25D is
        # a plain point with a Z, not a multi-part geometry, so skip it too.
        geom_type = geom.GetGeometryType()
        if geom_type not in (ogr.wkbPoint, ogr.wkbPoint25D):
            geom = geom.Centroid()
        yield geom.GetX(), geom.GetY()

    layer.SetSpatialFilter(None)
    ds = None  # closes file


def _building_mask(gt, nx, ny, srs_wkt):
    """
    Rasterize the building footprints (config.PROVINCE_BUILDINGS_GPKG) onto a
    raster grid (gt, nx, ny); returns a bool array, True = building pixel.
    Only footprints intersecting the grid are read (spatial index). Without
    a footprint file (allowed when OWN_CROWN_RADIUS is 0) the mask is empty.
    """
    if not config.PROVINCE_BUILDINGS_GPKG.exists():
        return np.zeros((ny, nx), dtype=bool)
    mem = gdal.GetDriverByName("MEM").Create("", nx, ny, 1, gdal.GDT_Byte)
    mem.SetGeoTransform(gt)
    mem.SetProjection(srs_wkt)
    ds = ogr.Open(str(config.PROVINCE_BUILDINGS_GPKG), 0)
    layer = ds.GetLayer(0)
    layer.SetSpatialFilterRect(gt[0], gt[3] + ny * gt[5], gt[0] + nx * gt[1], gt[3])
    gdal.RasterizeLayer(mem, [1], layer, burn_values=[1])
    return mem.GetRasterBand(1).ReadAsArray().astype(bool)


def _prepare_tree(dem, buildings, gt, x, y, nodata=None):
    """
    Prepare one tree's viewshed input from a DEM array and its building mask
    (same grid). Returns None if (x, y) is outside the array, else
    (window, window_gt, observer_offset, info):

    window           the DEM within MAX_DISTANCE of the tree (a copy), with
                     the tree's OWN crown flattened to local ground: every
                     non-building pixel within OWN_CROWN_RADIUS is lowered
                     to the ground estimate. Without this, the observer sits
                     on top of / inside its own crown in the surface model
                     and that crown blocks most of its own lines of sight
                     (self-occlusion). Building pixels are never flattened —
                     a tree next to a facade must not open a hole in it.
    observer_offset  observerHeight for ViewshedGenerate, which adds it to
                     the DEM value at the observer's own pixel (verified:
                     GDAL docs + synthetic test, ARCHITECTURE.md §6) — so
                     canopy top minus that (usually flattened) pixel.
    info             dict(canopy_top, ground, plausible) for reporting.

    canopy_top = max over non-building pixels within TREE_HEIGHT_BUFFER_RADIUS
    (a roof edge next to a tree is not its crown); ground = min within
    TREE_GROUND_SEARCH_RADIUS. A tree is plausible if 0 < canopy_top - ground
    <= TREE_HEIGHT_MAX_PLAUSIBLE (height above ground, not NAP: the province
    spans ~-6 to ~+40 m NAP). Implausible trees keep the unmodified window
    and get OBSERVER_HEIGHT.
    """
    ny, nx = dem.shape
    pc = int(np.floor((x - gt[0]) / gt[1]))   # observer pixel, as GDAL picks it
    pr = int(np.floor((y - gt[3]) / gt[5]))
    if not (0 <= pc < nx and 0 <= pr < ny):
        return None

    half = int(np.ceil(config.MAX_DISTANCE / abs(gt[1]))) + 2
    c0, r0 = max(0, pc - half), max(0, pr - half)
    c1, r1 = min(nx, pc + half + 1), min(ny, pr + half + 1)
    window = dem[r0:r1, c0:c1].copy()
    bld = buildings[r0:r1, c0:c1]
    window_gt = (gt[0] + c0 * gt[1], gt[1], 0.0, gt[3] + r0 * gt[5], 0.0, gt[5])

    rows_idx, cols_idx = np.indices(window.shape)
    dist = np.hypot((cols_idx + c0 + 0.5) * gt[1] + gt[0] - x,
                    (rows_idx + r0 + 0.5) * gt[5] + gt[3] - y)
    valid = np.isfinite(window)
    if nodata is not None:
        valid &= window != nodata

    info = {"canopy_top": None, "ground": None, "plausible": False}
    crown = valid & ~bld & (dist <= config.TREE_HEIGHT_BUFFER_RADIUS)
    around = valid & (dist <= config.TREE_GROUND_SEARCH_RADIUS)
    if crown.any() and around.any():
        canopy_top = float(window[crown].max())
        ground = float(window[around].min())
        info.update(canopy_top=canopy_top, ground=ground)
        if 0 < canopy_top - ground <= config.TREE_HEIGHT_MAX_PLAUSIBLE:
            info["plausible"] = True
            if config.OWN_CROWN_RADIUS > 0:   # (dist <= 0 would still hit a tree on a pixel centre)
                own = valid & ~bld & (dist <= config.OWN_CROWN_RADIUS)
                window[own] = np.minimum(window[own], ground)
            base = float(window[pr - r0, pc - c0])
            # base can exceed canopy_top only if the tree point itself lies
            # on a (never flattened) roof; then the observer stays on it.
            return window, window_gt, max(0.0, canopy_top - base), info

    return window, window_gt, config.OBSERVER_HEIGHT, info


# ---------------------------------------------------------------------------
# Viewshed computation
# ---------------------------------------------------------------------------

def _viewshed_python_api(dem_band, obs_x, obs_y, obs_h):
    """
    Use gdal.ViewshedGenerate() (GDAL >= 3.1) with MEM driver.

    GDAL clips the output to a window around the observer (sized by
    maxDistance), NOT the full extent of the source raster — so this
    returns (arr, geotransform) and the caller must paste arr into the
    tile-sized accumulator at the offset implied by that geotransform.
    Returns (None, None) on failure.
    """
    try:
        out_ds = gdal.ViewshedGenerate(
            srcBand=dem_band,
            driverName="MEM",
            targetRasterName="",
            creationOptions=[],
            observerX=obs_x,
            observerY=obs_y,
            observerHeight=obs_h,
            targetHeight=config.TARGET_HEIGHT,
            visibleVal=1.0,
            invisibleVal=0.0,
            outOfRangeVal=0.0,
            noDataVal=0.0,
            dfCurvCoeff=config.CURVATURE_COEFF,
            mode=gdal.GVM_Edge,
            maxDistance=config.MAX_DISTANCE,
        )
        if out_ds is None:
            return None, None
        arr = out_ds.GetRasterBand(1).ReadAsArray()
        gt = out_ds.GetGeoTransform()
        out_ds = None
        return arr, gt
    except Exception as exc:
        log.debug(f"ViewshedGenerate failed for observer ({obs_x}, {obs_y}): {exc}")
        return None, None


def _viewshed_subprocess(dem_tile_path: Path, obs_x, obs_y, obs_h):
    """
    Fallback: call gdal_viewshed.exe as a subprocess and return
    (arr, geotransform), or (None, None) on failure. Uses a temp file for
    the output. Like the Python API, the output window is clipped to
    maxDistance around the observer, not the full tile extent.
    """
    exe = os.path.join(config.GDAL_BIN, "gdal_viewshed.exe")
    if not os.path.isfile(exe):
        exe = os.path.join(config.GDAL_BIN, "gdal_viewshed")  # Linux / Mac

    fd, tmp_path = tempfile.mkstemp(suffix=".tif")
    os.close(fd)
    try:
        cmd = [
            exe,
            "-b", "1",
            "-ox", str(obs_x),
            "-oy", str(obs_y),
            "-oz", str(obs_h),
            "-tz", str(config.TARGET_HEIGHT),
            "-md", str(config.MAX_DISTANCE),
            "-cc", str(config.CURVATURE_COEFF),
            "-f", "GTiff",
            str(dem_tile_path),
            tmp_path,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        if result.returncode != 0:
            return None, None

        ds = gdal.Open(tmp_path)
        if ds is None:
            return None, None
        arr = ds.GetRasterBand(1).ReadAsArray()
        gt = ds.GetGeoTransform()
        ds = None
        return arr, gt
    except Exception as exc:
        log.debug(f"gdal_viewshed subprocess failed for observer ({obs_x}, {obs_y}): {exc}")
        return None, None
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _paste_into_accumulator(accumulator, arr, arr_gt, tile_gt):
    """
    Add arr (a small viewshed window) into accumulator (the full tile-sized
    array) at the pixel offset implied by the two geotransforms, clipping to
    accumulator bounds in case the window straddles the tile edge.
    """
    col_off = round((arr_gt[0] - tile_gt[0]) / tile_gt[1])
    row_off = round((arr_gt[3] - tile_gt[3]) / tile_gt[5])

    h, w = arr.shape
    H, W = accumulator.shape

    r0, c0 = max(0, row_off), max(0, col_off)
    r1, c1 = min(H, row_off + h), min(W, col_off + w)
    if r0 >= r1 or c0 >= c1:
        return

    ar0, ac0 = r0 - row_off, c0 - col_off
    ar1, ac1 = ar0 + (r1 - r0), ac0 + (c1 - c0)
    accumulator[r0:r1, c0:c1] += arr[ar0:ar1, ac0:ac1].astype(np.uint32)


# ---------------------------------------------------------------------------
# Per-tile worker
# ---------------------------------------------------------------------------

def process_tile(args):
    """
    Worker function executed in a child process.

    args = (tile_id, tile_info, trees_db_path, output_dir, resume)

    Returns (tile_id, n_trees, status_message)
    """
    tile_id, tile_info, trees_db_path, output_dir_str, resume = args

    # Re-import config in child process (env vars already set by fork/spawn)
    output_dir = Path(output_dir_str)
    out_path   = output_dir / f"{tile_id}.tif"

    if resume and out_path.exists():
        return tile_id, 0, "skipped (already exists)"

    tile_path = Path(tile_info["path"])
    if not tile_path.exists():
        return tile_id, 0, f"ERROR: tile file missing {tile_path}"

    # Query trees over the *buffered* extent, not just the inner extent: a
    # tree owned by the neighbouring tile can still be within MAX_DISTANCE of
    # a pixel on this side of the boundary. The same tree gets queried again
    # by that neighbour too — that's fine, since only the inner (non-
    # overlapping) window of each tile's result ever gets written to disk.
    xmin = tile_info["buf_xmin"]
    xmax = tile_info["buf_xmax"]
    ymin = tile_info["buf_ymin"]
    ymax = tile_info["buf_ymax"]

    # Open DEM tile once for the whole tile
    dem_ds = gdal.Open(str(tile_path))
    if dem_ds is None:
        return tile_id, 0, f"ERROR: cannot open DEM tile {tile_path}"

    dem_band = dem_ds.GetRasterBand(1)
    gt       = dem_ds.GetGeoTransform()
    proj     = dem_ds.GetProjection()
    nx       = dem_ds.RasterXSize
    ny       = dem_ds.RasterYSize

    dem = dem_band.ReadAsArray()
    nodata = dem_band.GetNoDataValue()
    buildings = _building_mask(gt, nx, ny, proj)
    mem = gdal.GetDriverByName("MEM")

    accumulator = np.zeros((ny, nx), dtype=np.uint32)
    n_trees = 0

    db_path = Path(trees_db_path)
    try:
        for x, y in iter_tree_points_in_bbox(
            db_path, xmin, ymin, xmax, ymax,
            layer_name=config.TREES_LAYER,
        ):
            n_trees += 1
            prepared = _prepare_tree(dem, buildings, gt, x, y, nodata)
            if prepared is None:          # bbox query can return a point on the edge
                continue
            window, window_gt, h, _info = prepared
            # Each tree gets its own small in-memory DEM (its own crown removed)
            win_ds = mem.Create("", window.shape[1], window.shape[0], 1, gdal.GDT_Float32)
            win_ds.SetGeoTransform(window_gt)
            win_ds.GetRasterBand(1).WriteArray(window)
            if nodata is not None:
                win_ds.GetRasterBand(1).SetNoDataValue(nodata)
            arr, arr_gt = _viewshed_python_api(win_ds.GetRasterBand(1), x, y, h)
            win_ds = None

            if arr is not None:
                _paste_into_accumulator(accumulator, arr, arr_gt, gt)

            if n_trees % config.LOG_EVERY == 0:
                log.info(f"    [{tile_id}] {n_trees} trees processed so far…")

    except Exception as exc:
        dem_ds = None
        return tile_id, n_trees, f"ERROR during tree iteration: {exc}"

    dem_ds = None  # close DEM

    if n_trees == 0:
        return tile_id, 0, "no trees in buffered extent — skipped"

    # Crop to the inner (non-overlapping) window before writing. This is
    # what keeps adjacent tiles' outputs from overlapping — the buffered
    # halo was only needed as context for computing accumulator values near
    # the inner edge, not for the output itself.
    col0 = tile_info["inner_col_off"]
    row0 = tile_info["inner_row_off"]
    inner_w = tile_info["inner_w_px"]
    inner_h = tile_info["inner_h_px"]
    inner_arr = accumulator[row0:row0 + inner_h, col0:col0 + inner_w]
    inner_gt = (
        gt[0] + col0 * gt[1], gt[1], gt[2],
        gt[3] + row0 * gt[5], gt[4], gt[5],
    )

    # Write accumulated result. No NoData value: 0 is a legitimate, common
    # count (no trees within range of that pixel), not missing data — a
    # NoData=0 flag would make GIS tools mask genuinely-zero areas as blank.
    output_dir.mkdir(parents=True, exist_ok=True)
    driver = gdal.GetDriverByName("GTiff")
    out_ds = driver.Create(
        str(out_path),
        inner_w, inner_h, 1,
        gdal.GDT_UInt32,
        options=["COMPRESS=LZW", "TILED=YES", "BIGTIFF=IF_SAFER"],
    )
    out_ds.SetGeoTransform(inner_gt)
    out_ds.SetProjection(proj)
    out_ds.GetRasterBand(1).WriteArray(inner_arr)
    out_ds.FlushCache()
    out_ds = None

    return tile_id, n_trees, f"OK — {n_trees} trees"


def _check_crs_match(name: str, dem_path: Path, trees_path: Path) -> bool:
    """
    Verify the DEM and tree layer share a CRS. A silent mismatch (or a
    shapefile with no .prj) makes OGR's SetSpatialFilter match nothing —
    no error, just an all-zero result that looks identical to "no trees
    here", which would go unnoticed in an unattended multi-municipality run.
    """
    dem_ds = gdal.Open(str(dem_path))
    dem_srs = osr.SpatialReference()
    dem_srs.ImportFromWkt(dem_ds.GetProjection())
    dem_ds = None

    trees_ds = ogr.Open(str(trees_path), 0)
    trees_srs = trees_ds.GetLayer(0).GetSpatialRef()
    trees_ds = None

    if trees_srs is None:
        log.error(f"[{name}] tree layer {trees_path} has no defined CRS — skipping.")
        return False
    if not dem_srs.IsSame(trees_srs):
        log.error(
            f"[{name}] CRS mismatch: DEM is '{dem_srs.GetName()}' "
            f"but trees layer is '{trees_srs.GetName()}' — skipping."
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Per-municipality driver
# ---------------------------------------------------------------------------

def process_municipality(name: str, dem_path: Path, workers: int, resume: bool):
    """Returns (completed, errors, total_trees), or None if skipped before processing."""
    tile_index_path = config.tile_index_path(name)
    viewshed_dir     = config.viewshed_tiles_dir(name)

    if not tile_index_path.exists():
        log.error(
            f"[{name}] tile index not found at {tile_index_path} — "
            "run 01_tile_dem.py first. Skipping."
        )
        return None

    if not config.PROVINCE_TREES_GPKG.exists():
        log.error(
            f"[{name}] {config.PROVINCE_TREES_GPKG} not found — build it once "
            "(see config.py) before running this script. Skipping."
        )
        return None

    if not _check_crs_match(name, dem_path, config.PROVINCE_TREES_GPKG):
        return None

    with open(tile_index_path) as fh:
        tile_index = json.load(fh)

    viewshed_dir.mkdir(parents=True, exist_ok=True)

    total_tiles = len(tile_index)
    log.info(f"[{name}] Tile index: {total_tiles} tiles")
    log.info(f"[{name}] Tree layer: {config.PROVINCE_TREES_GPKG} (province-wide, for cross-boundary context)")
    log.info(f"[{name}] Workers: {workers}  |  Resume: {resume}")
    log.info(f"[{name}] Output: {viewshed_dir}")

    work_items = [
        (tile_id, tile_info, str(config.PROVINCE_TREES_GPKG), str(viewshed_dir), resume)
        for tile_id, tile_info in tile_index.items()
    ]

    completed = 0
    errors    = 0
    total_trees = 0

    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(process_tile, item): item[0] for item in work_items}

        for future in as_completed(futures):
            tile_id = futures[future]
            try:
                tid, n_trees, msg = future.result()
            except Exception as exc:
                log.error(f"  [{name}] {tile_id}: UNHANDLED EXCEPTION — {exc}")
                errors += 1
                completed += 1
                continue

            total_trees += n_trees
            completed   += 1

            is_error = msg.startswith("ERROR")
            if is_error:
                errors += 1
            log.log(logging.ERROR if is_error else logging.INFO,
                     f"  [{name}] [{completed:>5}/{total_tiles}] {tid}: {msg}")

            if errors > 0 and completed % 100 == 0:
                log.warning(f"[{name}] Running error count: {errors}")

    log.info(
        f"[{name}] Finished. {completed} tiles processed, "
        f"{errors} errors, ~{total_trees:,} tree viewsheds computed."
    )
    if total_trees == 0:
        log.warning(
            f"[{name}] Zero trees processed across all tiles — this usually "
            "means broken/mismatched input data, not an empty municipality. "
            "Check the tree shapefile."
        )

    return completed, errors, total_trees


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Run viewshed analysis on tree layers.")
    parser.add_argument("--workers", type=int, default=config.NUM_WORKERS,
                        help="Number of parallel worker processes")
    parser.add_argument("--resume", action="store_true",
                        help="Skip tiles that already have output files")
    args = parser.parse_args()

    if not hasattr(gdal, "ViewshedGenerate"):
        sys.exit("ERROR: gdal.ViewshedGenerate not found — this pipeline needs GDAL >= 3.1.")
    if config.OWN_CROWN_RADIUS > 0 and not config.PROVINCE_BUILDINGS_GPKG.exists():
        sys.exit(
            f"ERROR: building footprints not found: {config.PROVINCE_BUILDINGS_GPKG}\n"
            "Own-crown removal needs them, or it would flatten buildings next to trees "
            "(see README, 'Per-tree height')."
        )

    pairs = list(config.municipality_pairs())
    if not pairs:
        sys.exit(
            f"ERROR: no tif+shp pairs found in {config.VIEWANALYSE_DIR}\n"
            "Check config.VIEWANALYSE_DIR and config.MUNICIPALITIES."
        )

    log.info(f"Municipalities to process: {[name for name, _, _ in pairs]}")

    for name, dem_path, _trees_path in pairs:
        log.info(f"=== {name} ===")
        t0 = time.perf_counter()
        result = process_municipality(name, dem_path, args.workers, args.resume)
        elapsed = time.perf_counter() - t0
        if result is not None:
            completed, errors, total_trees = result
            config.log_benchmark(name, "compute_viewsheds", elapsed,
                                  tiles=completed, trees=total_trees, errors=errors)

    log.info("Next step: run 03_merge_tiles.py")


if __name__ == "__main__":
    main()
