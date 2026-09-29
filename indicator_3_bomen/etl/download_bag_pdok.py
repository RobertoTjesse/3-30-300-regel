"""
download_bag_pdok.py — Download BAG building footprints (pand) and addresses
(verblijfsobject) for every municipality from PDOK's BAG WFS, into
  config.PROVINCE_BUILDINGS_GPKG   (layer "buildings": identificatie, status)
  config.PROVINCE_ADDRESSES_GPKG   (layer "addresses": identificatie,
                                    gebruiksdoel, status, pandidentificatie)

Why blocks: PDOK's WFS stops paging at ~50,000 features per query (HTTP 400
beyond that), so the area is covered in BLOCK_M blocks — only those
intersecting a municipality DEM — and any block that hits the limit is split
into 4 and retried. Blocks are downloaded in parallel with ogr2ogr.exe (the
CLI, not the Python bindings: on a network with TLS interception only the
CLI's certificate setup works here), then merged with duplicates (objects
on block borders) removed by identificatie.

Resumable: finished blocks are kept in data/interim/bag_blocks/ until the
merge succeeds, and skipped on a re-run.

Usage:
    python indicator_3_bomen/etl/download_bag_pdok.py
"""

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import config

from osgeo import gdal, ogr
gdal.UseExceptions()
ogr.UseExceptions()

WFS = "WFS:https://service.pdok.nl/lv/bag/wfs/v2_0"
LAYERS = {
    # name: (WFS type, fields, output gpkg)
    "buildings": ("bag:pand", ["identificatie", "status"], config.PROVINCE_BUILDINGS_GPKG),
    "addresses": ("bag:verblijfsobject",
                  ["identificatie", "gebruiksdoel", "status", "pandidentificatie"],
                  config.PROVINCE_ADDRESSES_GPKG),
}
BLOCK_M = 5000              # starting block size (m); crowded blocks are split
PAGE_LIMIT = 50000          # PDOK's paging ceiling
WORKERS = 4                 # blocks downloaded at the same time
BLOCK_DIR = config.INTERIM_DIR / "bag_blocks"
OGR2OGR = os.path.join(config.GDAL_BIN, "ogr2ogr.exe")


def municipality_extents():
    """Yield (name, (xmin, ymin, xmax, ymax)) for every municipality DEM."""
    # Every municipality DEM, regardless of config.MUNICIPALITIES (the BAG
    # layers are province-wide sources, like province_trees.gpkg)
    for dem in sorted(config.VIEWANALYSE_DIR.glob("*.tif")):
        name = dem.stem
        info = gdal.Info(str(dem), format="json")
        (x0, y1), (x1, y0) = info["cornerCoordinates"]["upperLeft"], info["cornerCoordinates"]["lowerRight"]
        yield name, (x0, y0, x1, y1)


def initial_blocks(extents):
    """The BLOCK_M blocks (x, y, size) that touch any municipality extent;
    a set, so blocks shared by neighbours are downloaded once."""
    blocks = set()
    for _name, (x0, y0, x1, y1) in extents:
        for bx in range(int(x0 // BLOCK_M), int(x1 // BLOCK_M) + 1):
            for by in range(int(y0 // BLOCK_M), int(y1 // BLOCK_M) + 1):
                blocks.add((bx * BLOCK_M, by * BLOCK_M, BLOCK_M))
    return sorted(blocks)


def count(path):
    """Number of features in a downloaded block file (0 if it can't be opened)."""
    ds = ogr.Open(str(path))
    return ds.GetLayer(0).GetFeatureCount() if ds else 0


def fetch(layer_key, block):
    """Download one block; returns (block, n) or (block, None) if it must be split."""
    wfs_type, fields, _ = LAYERS[layer_key]
    x, y, size = block
    out = BLOCK_DIR / layer_key / f"{x}_{y}_{size}.fgb"
    done = out.with_suffix(".done")     # marker: this block finished in an earlier run
    if done.exists():
        return block, count(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    for f in (out, done):
        f.unlink(missing_ok=True)
    # ogr2ogr: the WFS layer within the block (-spat), only the needed
    # fields (-select), fetched in pages of 1000
    cmd = [OGR2OGR, "-f", "FlatGeobuf", str(out), WFS, wfs_type,
           "-spat", str(x), str(y), str(x + size), str(y + size),
           "-spat_srs", "EPSG:28992", "-t_srs", "EPSG:28992",
           "-nln", layer_key, "-select", ",".join(fields),
           "--config", "OGR_WFS_PAGE_SIZE", "1000"]
    for attempt in range(3):
        result = subprocess.run(cmd, capture_output=True, text=True)
        n = count(out) if out.exists() else 0
        hit_limit = n >= PAGE_LIMIT or "HTTP error code : 400" in result.stderr
        if hit_limit:
            out.unlink(missing_ok=True)
            return block, None
        if result.returncode == 0:
            done.touch()
            return block, n
        time.sleep(10 * (attempt + 1))   # transient server/network error: retry
    raise RuntimeError(f"{layer_key} block {block} failed:\n{result.stderr[-2000:]}")


def download(layer_key, blocks):
    """Download all blocks of one layer, splitting any block that is too big
    and retrying its quarters, until none are left; returns the feature count."""
    total, todo = 0, list(blocks)
    while todo:
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            results = list(ex.map(lambda b: fetch(layer_key, b), todo))
        todo = []
        for (x, y, size), n in results:
            if n is None:   # too many features: split into 4
                h = size // 2
                todo += [(x, y, h), (x + h, y, h), (x, y + h, h), (x + h, y + h, h)]
            else:
                total += n
        print(f"  {layer_key}: {total:,} features so far, {len(todo)} blocks to split/retry", flush=True)
    return total


def merge(layer_key):
    """Combine the block files of one layer into its GeoPackage, keeping each
    object once (objects on block borders are in several blocks)."""
    _, _, out_path = LAYERS[layer_key]
    tmp = out_path.with_suffix(".partial.gpkg")
    tmp.unlink(missing_ok=True)
    out_ds = ogr.GetDriverByName("GPKG").CreateDataSource(str(tmp))
    out_layer, seen = None, set()
    for fgb in sorted((BLOCK_DIR / layer_key).glob("*.fgb")):
        src = ogr.Open(str(fgb))
        sl = src.GetLayer(0)
        if out_layer is None:
            geom_type = ogr.wkbMultiPolygon if layer_key == "buildings" else ogr.wkbPoint
            out_layer = out_ds.CreateLayer(layer_key, sl.GetSpatialRef(), geom_type)
            for i in range(sl.GetLayerDefn().GetFieldCount()):
                out_layer.CreateField(sl.GetLayerDefn().GetFieldDefn(i))
        out_layer.StartTransaction()
        for f in sl:
            fid = f.GetField("identificatie")
            if fid in seen:
                continue
            seen.add(fid)
            nf = ogr.Feature(out_layer.GetLayerDefn())
            nf.SetFrom(f)
            g = nf.GetGeometryRef()
            if layer_key == "buildings" and g is not None:
                nf.SetGeometry(ogr.ForceToMultiPolygon(g.Clone()))
            out_layer.CreateFeature(nf)
        out_layer.CommitTransaction()
    out_ds = None
    tmp.replace(out_path)
    return len(seen)


def main():
    """Download and merge the buildings, then the addresses."""
    extents = list(municipality_extents())
    blocks = initial_blocks(extents)
    print(f"{len(extents)} municipalities -> {len(blocks)} blocks of {BLOCK_M} m per layer", flush=True)
    for key in LAYERS:
        t0 = time.time()
        n = download(key, blocks)
        print(f"{key}: {n:,} downloaded (incl. border duplicates) in {(time.time() - t0) / 60:.0f} min", flush=True)
        kept = merge(key)
        print(f"{key}: {kept:,} unique -> {LAYERS[key][2]}", flush=True)
    print("Done. Block files in", BLOCK_DIR, "can be deleted.")


if __name__ == "__main__":
    main()
