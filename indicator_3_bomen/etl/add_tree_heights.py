"""
add_tree_heights.py — Record, per tree, the heights the viewshed stage uses,
and write them out as a NEW GeoPackage (does not modify the source tree
layer).

Reuses 02_compute_viewsheds._prepare_tree() — the exact logic of the live
pipeline (canopy top ignoring building pixels, local ground estimate,
plausibility check, own-crown removal, observer offset) — so the values
here match what the viewshed computation actually used for that tree.
Reads config.PROVINCE_DEM_VRT and config.PROVINCE_BUILDINGS_GPKG, block by
block, so even the largest municipality never has to fit in memory at once.

Usage:
    python indicator_3_bomen/etl/add_tree_heights.py <municipality_name>

Output:
    data/processed/<municipality_name>_tree_heights.gpkg
    Point layer, same geometry as the source trees, with columns:
      canopy_top_m      — max non-building surface value within
                            TREE_HEIGHT_BUFFER_RADIUS (NAP)
      ground_m          — local ground estimate (min within
                            TREE_GROUND_SEARCH_RADIUS), NAP
      tree_height_m     — canopy_top_m minus ground_m
      observer_offset_m — the observerHeight actually passed to
                            ViewshedGenerate
      was_clamped        — True if the tree failed the plausibility check
                            and fell back to OBSERVER_HEIGHT
"""

import sys
import importlib
from collections import defaultdict

import numpy as np

import config

try:
    from osgeo import gdal, ogr
except ImportError as exc:
    sys.exit(f"ERROR: cannot import osgeo — {exc}")

gdal.UseExceptions()
ogr.UseExceptions()

# Reuse the real per-tree logic from 02_compute_viewsheds.py rather than
# re-implementing it, so this always matches what the pipeline actually did.
_viewsheds_mod = importlib.import_module("02_compute_viewsheds")
_prepare_tree = _viewsheds_mod._prepare_tree
_building_mask = _viewsheds_mod._building_mask

BLOCK_M = 500.0      # trees are processed per block of this size
MARGIN_M = 10.0      # > TREE_GROUND_SEARCH_RADIUS: context read around a block


def add_heights_for_municipality(name: str) -> None:
    """Write <name>_tree_heights.gpkg: every tree of one municipality with the
    heights stage 2 would use for it (see the module docstring)."""
    pairs ={n: (dem, trees) for n, dem, trees in config.municipality_pairs()}
    if name not in pairs:
        sys.exit(f"ERROR: '{name}' not found (check spelling / config.CORRUPTED_DEM_MUNICIPALITIES)")
    _dem_path, trees_path = pairs[name]

    print(f"[{name}] DEM: {config.PROVINCE_DEM_VRT}")
    print(f"[{name}] Trees: {trees_path}")
    print(f"[{name}] Buildings: {config.PROVINCE_BUILDINGS_GPKG}")

    # The province DEM (pgt: its geotransform) and the municipality's trees
    dem_ds = gdal.Open(str(config.PROVINCE_DEM_VRT))
    dem_band = dem_ds.GetRasterBand(1)
    pgt = dem_ds.GetGeoTransform()
    nodata = dem_band.GetNoDataValue()
    proj = dem_ds.GetProjection()

    src_ds = ogr.Open(str(trees_path), 0)
    src_layer = src_ds.GetLayer(0)
    srs = src_layer.GetSpatialRef()

    # Group trees by block so each block's DEM + building mask is read once
    blocks = defaultdict(list)
    for feat in src_layer:
        geom = feat.GetGeometryRef()
        if geom is None:
            continue
        pt = geom.Centroid() if geom.GetGeometryType() not in (ogr.wkbPoint, ogr.wkbPoint25D) else geom
        x, y = pt.GetX(), pt.GetY()
        blocks[(int(x // BLOCK_M), int(y // BLOCK_M))].append((x, y))

    # Output: a new point layer (the source trees are never modified)
    out_path = config.PROCESSED_DIR / f"{name}_tree_heights.gpkg"
    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    out_ds = ogr.GetDriverByName("GPKG").CreateDataSource(str(out_path))
    out_layer = out_ds.CreateLayer(f"{name}_tree_heights", srs, ogr.wkbPoint)
    for field in ("canopy_top_m", "ground_m", "tree_height_m", "observer_offset_m"):
        out_layer.CreateField(ogr.FieldDefn(field, ogr.OFTReal))
    was_clamped_field = ogr.FieldDefn("was_clamped", ogr.OFTInteger)
    was_clamped_field.SetSubType(ogr.OFSTBoolean)
    out_layer.CreateField(was_clamped_field)
    out_defn = out_layer.GetLayerDefn()

    n_trees = 0
    n_clamped = 0
    out_layer.StartTransaction()
    for (bx, by), points in blocks.items():
        # Block window (+ margin) on the province DEM grid
        c0 = max(0, int(np.floor((bx * BLOCK_M - MARGIN_M - pgt[0]) / pgt[1])))
        c1 = min(dem_ds.RasterXSize, int(np.ceil(((bx + 1) * BLOCK_M + MARGIN_M - pgt[0]) / pgt[1])))
        r0 = max(0, int(np.floor(((by + 1) * BLOCK_M + MARGIN_M - pgt[3]) / pgt[5])))
        r1 = min(dem_ds.RasterYSize, int(np.ceil((by * BLOCK_M - MARGIN_M - pgt[3]) / pgt[5])))
        if c1 <= c0 or r1 <= r0:
            continue
        dem = dem_band.ReadAsArray(c0, r0, c1 - c0, r1 - r0)
        gt = (pgt[0] + c0 * pgt[1], pgt[1], 0.0, pgt[3] + r0 * pgt[5], 0.0, pgt[5])
        buildings = _building_mask(gt, c1 - c0, r1 - r0, proj)

        # Every tree in the block: run stage 2's own height logic and store
        # what it found; a tree outside the DEM gets a point without heights
        for x, y in points:
            prepared = _prepare_tree(dem, buildings, gt, x, y, nodata)
            out_feat = ogr.Feature(out_defn)
            if prepared is not None:
                _window, _wgt, offset, info = prepared
                if info["canopy_top"] is not None:
                    out_feat.SetField("canopy_top_m", info["canopy_top"])
                    out_feat.SetField("ground_m", info["ground"])
                    out_feat.SetField("tree_height_m", info["canopy_top"] - info["ground"])
                out_feat.SetField("observer_offset_m", offset)
                clamped = not info["plausible"]
                out_feat.SetField("was_clamped", 1 if clamped else 0)
                n_clamped += clamped
            out_feat.SetGeometry(ogr.Geometry(ogr.wkbPoint))
            out_feat.GetGeometryRef().AddPoint(x, y)
            out_layer.CreateFeature(out_feat)
            out_feat = None

            n_trees += 1
            if n_trees % config.LOG_EVERY == 0:
                print(f"  ...{n_trees} trees processed", flush=True)
    out_layer.CommitTransaction()

    out_ds = None
    src_ds = None
    dem_ds = None

    print(f"[{name}] Done. {n_trees} trees, {n_clamped} clamped ({100*n_clamped/max(n_trees, 1):.1f}%)")
    print(f"[{name}] Written to {out_path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python indicator_3_bomen/etl/add_tree_heights.py <municipality_name>")
    add_heights_for_municipality(sys.argv[1])
