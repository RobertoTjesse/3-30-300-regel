"""
add_tree_heights.py — Sample each tree's height from the DEM and write it
out as a NEW GeoPackage (does not modify the source tree layer).

Reuses the exact same sampling logic as 02_compute_viewsheds.py
(_sample_tree_height: DSM value at the tree point, rejected unless
0 < height above local ground <= TREE_HEIGHT_MAX_PLAUSIBLE;
_observer_offset: 0 for a plausible tree, i.e. the observer on that surface
at the tree point, falling back to OBSERVER_HEIGHT) so
the values shown here match what the live viewshed computation actually
used for that tree.

Usage:
    python etl/add_tree_heights.py <municipality_name>

Output:
    data/processed/<municipality_name>_tree_heights.gpkg
    Point layer, same geometry as the source trees, with columns:
      sampled_height_m  — DSM surface at the tree point (NAP) from
                            _sample_tree_height(); NULL if rejected
      ground_m          — local ground estimate (min within
                            TREE_GROUND_SEARCH_RADIUS), NAP
      tree_height_m     — sampled surface minus ground_m (unfiltered)
      observer_offset_m — the observerHeight actually passed to
                            ViewshedGenerate (from _observer_offset())
      was_clamped        — True if the tree was rejected by the
                            plausibility check and the offset fell back
                            to OBSERVER_HEIGHT
"""

import sys
import importlib
from pathlib import Path

import config

try:
    from osgeo import gdal, ogr
except ImportError as exc:
    sys.exit(f"ERROR: cannot import osgeo — {exc}")

gdal.UseExceptions()
ogr.UseExceptions()

# Reuse the real sampling function from 02_compute_viewsheds.py rather than
# re-implementing it, so this always matches what the pipeline actually did.
_viewsheds_mod = importlib.import_module("02_compute_viewsheds")
_sample_tree_point = _viewsheds_mod._sample_tree_point
_sample_tree_height = _viewsheds_mod._sample_tree_height
_observer_offset = _viewsheds_mod._observer_offset


def add_heights_for_municipality(name: str) -> None:
    pairs = {n: (dem, trees) for n, dem, trees in config.municipality_pairs()}
    if name not in pairs:
        sys.exit(f"ERROR: '{name}' not found (check spelling / config.CORRUPTED_DEM_MUNICIPALITIES)")
    dem_path, trees_path = pairs[name]

    print(f"[{name}] DEM: {dem_path}")
    print(f"[{name}] Trees: {trees_path}")

    dem_ds = gdal.Open(str(dem_path))
    dem_band = dem_ds.GetRasterBand(1)
    gt = dem_ds.GetGeoTransform()
    nx, ny = dem_ds.RasterXSize, dem_ds.RasterYSize

    src_ds = ogr.Open(str(trees_path), 0)
    src_layer = src_ds.GetLayer(0)
    srs = src_layer.GetSpatialRef()

    out_path = config.PROCESSED_DIR / f"{name}_tree_heights.gpkg"
    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    driver = ogr.GetDriverByName("GPKG")
    out_ds = driver.CreateDataSource(str(out_path))
    out_layer = out_ds.CreateLayer(f"{name}_tree_heights", srs, ogr.wkbPoint)
    out_layer.CreateField(ogr.FieldDefn("sampled_height_m", ogr.OFTReal))
    out_layer.CreateField(ogr.FieldDefn("ground_m", ogr.OFTReal))
    out_layer.CreateField(ogr.FieldDefn("tree_height_m", ogr.OFTReal))
    out_layer.CreateField(ogr.FieldDefn("observer_offset_m", ogr.OFTReal))
    was_clamped_field = ogr.FieldDefn("was_clamped", ogr.OFTInteger)
    was_clamped_field.SetSubType(ogr.OFSTBoolean)
    out_layer.CreateField(was_clamped_field)
    out_defn = out_layer.GetLayerDefn()

    n_trees = 0
    n_clamped = 0
    for feat in src_layer:
        geom = feat.GetGeometryRef()
        if geom is None:
            continue
        geom_type = geom.GetGeometryType()
        pt = geom.Centroid() if geom_type not in (ogr.wkbPoint, ogr.wkbPoint25D) else geom
        x, y = pt.GetX(), pt.GetY()

        h = _sample_tree_height(dem_band, gt, nx, ny, x, y)
        offset = _observer_offset(dem_band, gt, nx, ny, x, y)

        # The raw (unfiltered) sample, to report ground/height and whether
        # the plausibility check rejected THIS tree.
        raw = _sample_tree_point(dem_band, gt, nx, ny, x, y)
        clamped = raw is not None and h is None
        if clamped:
            n_clamped += 1

        out_feat = ogr.Feature(out_defn)
        if h is not None:
            out_feat.SetField("sampled_height_m", h)
        if raw is not None:
            out_feat.SetField("ground_m", raw[1])
            out_feat.SetField("tree_height_m", raw[0] - raw[1])
        out_feat.SetField("observer_offset_m", offset)
        out_feat.SetField("was_clamped", 1 if clamped else 0)
        out_feat.SetGeometry(ogr.Geometry(ogr.wkbPoint))
        out_feat.GetGeometryRef().AddPoint(x, y)
        out_layer.CreateFeature(out_feat)
        out_feat = None

        n_trees += 1
        if n_trees % config.LOG_EVERY == 0:
            print(f"  ...{n_trees} trees processed", flush=True)

    out_ds = None
    src_ds = None
    dem_ds = None

    print(f"[{name}] Done. {n_trees} trees, {n_clamped} clamped ({100*n_clamped/n_trees:.1f}%)")
    print(f"[{name}] Written to {out_path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python etl/add_tree_heights.py <municipality_name>")
    add_heights_for_municipality(sys.argv[1])
