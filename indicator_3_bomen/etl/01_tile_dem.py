"""
01_tile_dem.py — Split each municipality's DEM into overlapping tiles.

Source data is one DEM (.tif) + tree layer (.gpkg) pair per municipality in
config.VIEWANALYSE_DIR. This script only touches the DEM half; trees are
read directly by 02_compute_viewsheds.py.

Usage:
    python indicator_3_bomen/etl/01_tile_dem.py

Processes every municipality in config.MUNICIPALITIES (or all pairs found in
VIEWANALYSE_DIR if that list is empty).

Output, per municipality:
    data/interim/dem_tiles/<name>/tile_RRRR_CCCC.tif   — one tile per row/column index
    data/interim/dem_tiles/<name>/tile_index.json       — JSON manifest with spatial extents

The buffer on every tile is TILE_BUFFER_PX pixels (>= MAX_DISTANCE / pixel_size).
This ensures that a viewshed at any point inside the inner tile can see the full
30-metre radius without hitting a NoData edge, AND that trees owned by a
neighbouring tile within that radius are still visible to this tile's queries
(02_compute_viewsheds.py queries trees using the buffered extent, then crops
its output back down to the inner extent — this halo-then-crop approach is
what keeps tile seams artefact-free in the final mosaic).

Pixel data for every tile comes from config.PROVINCE_DEM_VRT (a VRT mosaic
of every municipality's DEM), not the individual municipality .tif — so a
tile whose buffer extends past this municipality's own raster edge still
gets real elevation data from the neighbouring municipality, rather than
stopping dead at a hard edge. The municipality's own .tif is only used to
define the tiling grid (extent, pixel size), so each municipality still
gets exactly the same tile coverage as before.
"""

import sys
import json
import logging
import time
from pathlib import Path

import config  # sets env vars, adds OSGeo4W to PATH

try:
    from osgeo import gdal
except ImportError as exc:
    sys.exit(f"ERROR: cannot import osgeo.gdal — {exc}\n"
             f"Activate the OSGeo4W Python environment or run from the OSGeo4W shell.")

gdal.UseExceptions()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
FILL_SEARCH_PX = 50   # 25 m at 0.5 m: interpolation reach for small gaps


def _fill_nodata(tile_path: Path) -> None:
    """
    Fill any NoData cells in this tile via interpolation from surrounding
    valid pixels, then clear the NoData flag (no missing data remains).

    gdal.ViewshedGenerate() does not special-case NoData in its input DEM —
    per GDAL's own documentation, "no special processing of input cells at
    a nodata value is done (which may result in erroneous results)": a
    NoData sentinel (e.g. -9999) gets treated as literal terrain elevation,
    which can silently create a phantom cliff or (for a large positive
    sentinel) a phantom mountain wherever a tree's viewshed radius happens
    to touch a gap — most commonly water. Filling before any viewshed call
    ever sees the tile removes this risk at the source instead of relying
    on no tree ever landing unlucky.
    """
    ds = gdal.Open(str(tile_path), gdal.GA_Update)
    band = ds.GetRasterBand(1)
    nodata = band.GetNoDataValue()
    if nodata is None:
        ds = None
        return

    arr = band.ReadAsArray()
    missing = arr == nodata
    if not missing.any():
        band.DeleteNoDataValue()
        ds = None
        return
    if missing.all():
        # Wholly outside real coverage (open sea, beyond the province): no
        # valid pixel to fill from. Keep the flag — never let the raw
        # sentinel masquerade as elevation. Common on coastal municipalities,
        # so no warning per tile.
        ds = None
        return

    # 1. Small gaps (under bridges, narrow water, data holes): interpolate
    #    from the surrounding surface — only up to FILL_SEARCH_PX away.
    # 2. Whatever is left is a large gap — open water, wide rivers: set it
    #    to the lowest valid surface value in the tile, a flat low "water
    #    level" that can never block a line of sight.
    # (Until 2026-09-27 step 1 used maxSearchDist=2000 for everything, so a
    # tile that was mostly water spent ~30 s interpolating across it —
    # hours in total for coastal municipalities.)
    gdal.FillNodata(targetBand=band, maskBand=None, maxSearchDist=FILL_SEARCH_PX,
                    smoothingIterations=1)
    arr = band.ReadAsArray()
    rest = arr == nodata
    if rest.any():
        arr[rest] = arr[~rest].min()
        band.WriteArray(arr)
    band.DeleteNoDataValue()
    ds.FlushCache()
    ds = None


def check_float_dems(province_vrt) -> list:
    """
    Return a problem description for every DEM stage 1 would read that is
    not stored as floating point (or carries a scale/offset the pipeline
    doesn't apply). Checks the province VRT and every source file behind it
    — a tile's halo reads neighbouring municipalities too.

    Why: an integer DEM silently truncates heights to whole metres (found
    2026-09-28 in an AHN5 export: Int32, -11..108 m). The viewshed then sees
    1 m terraces and false walls, with no error anywhere. A scaled integer
    (e.g. centimetres with scale 0.01) would be read as raw values, i.e.
    100x too high, since nothing downstream applies the scale.
    """
    problems = []
    sources = [f for f in province_vrt.GetFileList() if f.lower().endswith((".tif", ".tiff"))]
    for path in [config.PROVINCE_DEM_VRT, *sources]:
        ds = gdal.Open(str(path))
        band = ds.GetRasterBand(1)
        dtype = gdal.GetDataTypeName(band.DataType)
        if not dtype.startswith("Float"):
            problems.append(f"{path}: stored as {dtype} (heights in whole units)")
        scale, offset = band.GetScale(), band.GetOffset()
        if (scale not in (None, 1.0)) or (offset not in (None, 0.0)):
            problems.append(f"{path}: scale {scale} / offset {offset} set — not applied by the pipeline")
        ds = None
    return problems


# ---------------------------------------------------------------------------
def tile_dem(dem_path: Path, tiles_dir: Path, tile_index_path: Path, province_vrt) -> int:
    """Returns the total number of tiles written.

    province_vrt is an open GDAL dataset (config.PROVINCE_DEM_VRT) used as
    the actual pixel source for every tile — see module docstring.
    """
    tiles_dir.mkdir(parents=True, exist_ok=True)

    log.info(f"Opening DEM: {dem_path}")
    ds = gdal.Open(str(dem_path))
    if ds is None:
        sys.exit(f"ERROR: GDAL could not open {dem_path}")

    gt     = ds.GetGeoTransform()
    proj   = ds.GetProjection()
    xsize  = ds.RasterXSize
    ysize  = ds.RasterYSize
    px     = abs(gt[1])   # pixel width in map units (metres for RD New)
    py     = abs(gt[5])   # pixel height
    ds = None              # only needed the grid parameters above

    buf    = config.TILE_BUFFER_PX
    tpx    = config.TILE_PIXELS

    log.info(f"DEM size  : {xsize} x {ysize} pixels")
    log.info(f"Pixel size: {px:.4f} x {py:.4f} m")
    log.info(f"Tile size : {tpx} inner pixels + {buf} buffer = {tpx + 2*buf} total")
    log.info(f"Buffer covers: {buf * px:.1f} m  (MAX_DISTANCE = {config.MAX_DISTANCE} m)")

    if buf * px < config.MAX_DISTANCE:
        log.warning(
            f"Buffer ({buf * px:.1f} m) is smaller than MAX_DISTANCE "
            f"({config.MAX_DISTANCE} m). Trees near tile edges may be cut short. "
            f"Increase TILE_BUFFER_PX in config.py."
        )

    n_cols = max(1, -(-xsize // tpx))  # ceiling division
    n_rows = max(1, -(-ysize // tpx))
    total  = n_rows * n_cols
    log.info(f"Creating {n_rows} rows × {n_cols} cols = {total} tiles …")

    index = {}  # tile_id → {path, x_off, y_off, win_xsize, win_ysize, inner_*}

    done = 0
    for row in range(n_rows):
        for col in range(n_cols):
            # Inner (un-buffered) pixel window, in this municipality's own grid
            inner_x0 = col * tpx
            inner_y0 = row * tpx
            inner_x1 = min(xsize, inner_x0 + tpx)
            inner_y1 = min(ysize, inner_y0 + tpx)

            # Buffered window — deliberately NOT clamped to this
            # municipality's own raster bounds. A negative offset or one
            # past xsize/ysize just means "reach into whatever's there in
            # the province VRT", which may be a neighbouring municipality.
            buf_x0 = inner_x0 - buf
            buf_y0 = inner_y0 - buf
            buf_x1 = inner_x1 + buf
            buf_y1 = inner_y1 + buf

            win_w = buf_x1 - buf_x0
            win_h = buf_y1 - buf_y0

            tile_id   = f"tile_{row:04d}_{col:04d}"
            tile_path = tiles_dir / f"{tile_id}.tif"

            # Geo-coordinates of the buffered window (upper-left / lower-right)
            tile_x0 = gt[0] + buf_x0 * gt[1]
            tile_y0 = gt[3] + buf_y0 * gt[5]
            buf_geo_x1 = gt[0] + buf_x1 * gt[1]
            buf_geo_y1 = gt[3] + buf_y1 * gt[5]

            if tile_path.exists():
                log.debug(f"  skip existing {tile_id}")
            else:
                gdal.Translate(
                    str(tile_path),
                    province_vrt,
                    projWin=[tile_x0, tile_y0, buf_geo_x1, buf_geo_y1],
                    width=win_w,
                    height=win_h,
                    creationOptions=config.TILE_CREATION_OPTIONS,
                )
                _fill_nodata(tile_path)

            inner_geo_x0 = gt[0] + inner_x0 * gt[1]
            inner_geo_y0 = gt[3] + inner_y0 * gt[5]
            inner_geo_x1 = gt[0] + inner_x1 * gt[1]
            inner_geo_y1 = gt[3] + inner_y1 * gt[5]

            index[tile_id] = {
                "path":          str(tile_path),
                "buf_x0_px":     buf_x0,
                "buf_y0_px":     buf_y0,
                "win_w_px":      win_w,
                "win_h_px":      win_h,
                # Buffered extent in map coordinates (use for tree queries —
                # a tile must "see" neighbours' trees within MAX_DISTANCE of
                # its inner edge to compute correct viewshed counts there)
                "buf_xmin":      min(tile_x0, buf_geo_x1),
                "buf_xmax":      max(tile_x0, buf_geo_x1),
                "buf_ymin":      min(tile_y0, buf_geo_y1),
                "buf_ymax":      max(tile_y0, buf_geo_y1),
                # Inner extent in map coordinates (use for cropping output —
                # only the inner window is written, so adjacent tiles never
                # overlap in the final mosaic)
                "inner_xmin":    min(inner_geo_x0, inner_geo_x1),
                "inner_xmax":    max(inner_geo_x0, inner_geo_x1),
                "inner_ymin":    min(inner_geo_y0, inner_geo_y1),
                "inner_ymax":    max(inner_geo_y0, inner_geo_y1),
                # Inner window in pixel coords relative to the buffered tile array
                "inner_col_off": inner_x0 - buf_x0,
                "inner_row_off": inner_y0 - buf_y0,
                "inner_w_px":    inner_x1 - inner_x0,
                "inner_h_px":    inner_y1 - inner_y0,
                # Full tile origin (top-left corner of buffered tile)
                "tile_gt": [tile_x0, gt[1], gt[2], tile_y0, gt[4], gt[5]],
                "projection": proj,
            }

            done += 1
            if done % 50 == 0 or done == total:
                log.info(f"  {done}/{total} tiles written")

    with open(tile_index_path, "w") as fh:
        json.dump(index, fh, indent=2)

    log.info(f"Tile index saved: {tile_index_path}")
    log.info(f"Done. {total} tiles in {tiles_dir}")
    return total


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    pairs = config.require_municipality_pairs()

    if not config.PROVINCE_DEM_VRT.exists():
        sys.exit(
            f"ERROR: {config.PROVINCE_DEM_VRT} not found.\n"
            "Build it once with:\n"
            f'  gdalbuildvrt "{config.PROVINCE_DEM_VRT}" "{config.VIEWANALYSE_DIR}"\\*.tif'
        )

    province_vrt = gdal.Open(str(config.PROVINCE_DEM_VRT))
    if province_vrt is None:
        sys.exit(f"ERROR: GDAL could not open {config.PROVINCE_DEM_VRT}")

    problems = check_float_dems(province_vrt)
    if problems:
        sys.exit(
            "ERROR: DEM(s) not stored as plain floating point — heights would be\n"
            "truncated or mis-scaled. Re-export as Float32 (see sde_reexport/):\n  "
            + "\n  ".join(problems)
        )
    log.info(f"DEM check: province VRT and {len(province_vrt.GetFileList()) - 1} sources are floating point")

    log.info(f"Municipalities to tile: {[name for name, _, _ in pairs]}")

    for name, dem_path, _trees_path in pairs:
        log.info(f"=== {name} ===")
        t0 = time.perf_counter()
        n_tiles = tile_dem(dem_path, config.dem_tiles_dir(name), config.tile_index_path(name), province_vrt)
        config.log_benchmark(name, "tile_dem", time.perf_counter() - t0, tiles=n_tiles)
