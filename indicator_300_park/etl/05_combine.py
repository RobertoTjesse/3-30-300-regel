"""
05_combine.py — The walking distance per home the map shows: GRASS, with Valhalla as a second chance.

GRASS (03_grass.py) gives the distance. Where GRASS finds more than 300 m, or
no way at all, Valhalla (02_valhalla.py) gets a second look: it walks its own
network, built from the same OSM extract by other rules (it also uses private
and destination-only roads to reach a home), so it can find a way GRASS
misses, for example to a wood next to a home. The shorter of the two then
counts. A false "too far" is the worse error here, so the map takes the
benefit of the doubt.

A home inside a green area has 0 m in both engines.

Output config.FINAL_GPKG, layer homes: home_id, pand_id, gemeente, afstand_m
(empty beyond config.MAX_DISTANCE_M), bron ('groen' inside a green area,
'grass' or 'valhalla': which engine gave afstand_m).

Usage (from the repository root, with the OSGeo4W Python, after 02 and 03):
    python indicator_300_park/etl/05_combine.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402

import numpy as np  # noqa: E402
from osgeo import gdal, ogr, osr  # noqa: E402
gdal.UseExceptions()
ogr.UseExceptions()

SECOND_LOOK_M = 300   # GRASS above this (or nothing): Valhalla may do better


def main():
    t0 = time.time()
    for path in (config.GRASS_GPKG, config.VALHALLA_GPKG):
        if not path.exists():
            sys.exit(f"ERROR: {path} is missing; run 02_valhalla.py and 03_grass.py first")
    vh_ds = ogr.Open(str(config.VALHALLA_GPKG))
    vh = {f.GetField("home_id"): f.GetField("vh_m") for f in vh_ds.GetLayerByName("homes")}
    gr_ds = ogr.Open(str(config.GRASS_GPKG))
    grass = gr_ds.GetLayerByName("homes")

    if config.FINAL_GPKG.exists():
        config.FINAL_GPKG.unlink()
    rd = osr.SpatialReference()
    rd.ImportFromEPSG(28992)
    out = ogr.GetDriverByName("GPKG").CreateDataSource(str(config.FINAL_GPKG))
    hl = out.CreateLayer("homes", rd, ogr.wkbPoint)
    for name, t in (("home_id", ogr.OFTString), ("pand_id", ogr.OFTString), ("gemeente", ogr.OFTString),
                    ("afstand_m", ogr.OFTReal), ("bron", ogr.OFTString)):
        hl.CreateField(ogr.FieldDefn(name, t))
    dists, gr_only, sources = [], [], {"groen": 0, "grass": 0, "valhalla": 0}
    out.StartTransaction()
    for f in grass:
        gr_m = f.GetField("gr_m")
        d, bron = gr_m, "grass"
        if f.GetField("in_groen"):
            d, bron = 0.0, "groen"
        elif gr_m is None or gr_m > SECOND_LOOK_M:
            vh_m = vh.get(f.GetField("home_id"))
            if vh_m is not None and (gr_m is None or vh_m < gr_m):
                d, bron = vh_m, "valhalla"
        nf = ogr.Feature(hl.GetLayerDefn())
        for name in ("home_id", "pand_id", "gemeente"):
            nf.SetField(name, f.GetField(name))
        if d is not None:
            nf.SetField("afstand_m", d)
            nf.SetField("bron", bron)
            sources[bron] += 1
        nf.SetGeometry(f.GetGeometryRef().Clone())
        hl.CreateFeature(nf)
        dists.append(np.nan if d is None else d)
        gr_only.append(np.nan if gr_m is None else gr_m)
    out.CommitTransaction()

    dists, gr_only = np.array(dists), np.array(gr_only)
    n = len(dists)
    for name, m in config.THRESHOLDS_M.items():
        print(f"  within {name:>5} ({m:>4} m): {np.sum(dists <= m) / n:6.1%}   (GRASS alone {np.sum(gr_only <= m) / n:6.1%})")
    print(f"Homes: {n:,}; distance from " + ", ".join(f"{k} {v:,}" for k, v in sources.items()))
    print(f"Wrote {config.FINAL_GPKG} in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
