"""
05_merge_province.py — Merge the per-municipality residential building
scores into one province-wide layer, assigned to current municipalities,
and summarise per municipality.

Why: each <name>_woningen.gpkg covers its municipality's DEM *rectangle*,
so neighbouring outputs overlap (the same building appears in several, with
the same score — the viewsheds use cross-boundary context) and edge
municipalities also contain buildings from neighbouring provinces. The
DEM/municipality names are also pre-2023 (Brielle, Hellevoetsluis and
Westvoorne are now Voorne aan Zee).

Inputs:
  data/processed/*_woningen.gpkg          (04_score_buildings.py)
  data/interim/gemeenten.gpkg, provincies.gpkg
      PDOK bestuurlijkegebieden WFS (Gemeentegebied, Provinciegebied), e.g.
      ogr2ogr -f GPKG data/interim/gemeenten.gpkg
        "WFS:https://service.pdok.nl/kadaster/bestuurlijkegebieden/wfs/v1_0"
        bestuurlijkegebieden:Gemeentegebied -t_srs EPSG:28992 -nln gemeenten

Outputs:
  data/processed/<Province>_woningen.gpkg   every residential building in
      the province once, with its current municipality (by footprint
      centroid, looked up on a 5 m raster of the boundaries)
  data/processed/<Province>_samenvatting.csv   per municipality: buildings,
      homes, and share of homes per class / with >= 3 trees visible

Usage:
    python indicator_3_bomen/etl/05_merge_province.py
"""

import csv
import sys
from collections import defaultdict

import numpy as np

import config

from osgeo import gdal, ogr
gdal.UseExceptions()
ogr.UseExceptions()

LOOKUP_RES = 5.0      # metres
CLASSES = ["0", "1-2", "3-5", "6-7", "8+"]


def municipality_lookup():
    """Rasterise the province's current municipalities: (array, gt, names).

    Every 5 m cell holds the index of its municipality in names (0 = none),
    so finding a building's municipality is one array lookup instead of a
    polygon test per building."""
    # The province outline, from PDOK's provincies layer
    prov_ds = ogr.Open(str(config.INTERIM_DIR / "provincies.gpkg"))
    prov_layer = prov_ds.GetLayer(0)
    prov_layer.SetAttributeFilter(f"naam = '{config.PROVINCE}'")
    prov_feat = next(iter(prov_layer))            # keep the feature alive while cloning
    prov = prov_feat.GetGeometryRef().Clone()
    xmin, xmax, ymin, ymax = prov.GetEnvelope()

    # Copy the province's municipalities to a temporary layer, each with a
    # number (idx) that points into names
    gem_ds = ogr.Open(str(config.INTERIM_DIR / "gemeenten.gpkg"))
    gem_layer = gem_ds.GetLayer(0)
    mem = ogr.GetDriverByName("Memory").CreateDataSource("")
    sel = mem.CreateLayer("gem", gem_layer.GetSpatialRef(), ogr.wkbMultiPolygon)
    sel.CreateField(ogr.FieldDefn("idx", ogr.OFTInteger))
    names = [None]
    gem_layer.SetSpatialFilter(prov)
    for f in gem_layer:
        g = f.GetGeometryRef()
        # a municipality belongs to the province if its interior point lies in it
        if not prov.Contains(g.PointOnSurface()):
            continue
        names.append(f.GetField("naam"))
        nf = ogr.Feature(sel.GetLayerDefn())
        nf.SetField("idx", len(names) - 1)
        nf.SetGeometry(g.Clone())
        sel.CreateFeature(nf)

    # Burn each municipality's idx into a grid over the province
    nx = int(np.ceil((xmax - xmin) / LOOKUP_RES))
    ny = int(np.ceil((ymax - ymin) / LOOKUP_RES))
    gt = (xmin, LOOKUP_RES, 0.0, ymax, 0.0, -LOOKUP_RES)
    ras = gdal.GetDriverByName("MEM").Create("", nx, ny, 1, gdal.GDT_Int16)
    ras.SetGeoTransform(gt)
    gdal.RasterizeLayer(ras, [1], sel, options=["ATTRIBUTE=idx"])
    return ras.GetRasterBand(1).ReadAsArray(), gt, names


def main():
    """Merge all <name>_woningen.gpkg into one province layer (each building
    once, with its current municipality) and write the summary CSV."""
    # Every municipality's scored buildings, except an earlier province output
    inputs = sorted(p for p in config.PROCESSED_DIR.glob("*_woningen.gpkg")
                    if not p.name.startswith(config.PROVINCE_SLUG))
    if not inputs:
        sys.exit("ERROR: no *_woningen.gpkg found — run 04_score_buildings.py first")
    lookup, gt, names = municipality_lookup()
    print(f"{len(names) - 1} municipalities in {config.PROVINCE}; merging {len(inputs)} files", flush=True)

    out_path = config.PROCESSED_DIR / f"{config.PROVINCE_SLUG}_woningen.gpkg"
    tmp_path = out_path.with_suffix(".partial.gpkg")
    tmp_path.unlink(missing_ok=True)
    out_ds = ogr.GetDriverByName("GPKG").CreateDataSource(str(tmp_path))
    out_layer = None
    seen = set()          # building ids already written (outputs overlap)
    # Per municipality: buildings, homes, and homes per class ("w_3-5" etc.)
    stats = defaultdict(lambda: {"gebouwen": 0, "woningen": 0, **{f"w_{c}": 0 for c in CLASSES},
                                 "w_geen_ring": 0})
    for path in inputs:
        src = ogr.Open(str(path))
        sl = src.GetLayer(0)
        if out_layer is None:
            # First file: create the output with the same fields + "gemeente"
            out_layer = out_ds.CreateLayer("woningen", sl.GetSpatialRef(), ogr.wkbMultiPolygon)
            for i in range(sl.GetLayerDefn().GetFieldCount()):
                out_layer.CreateField(sl.GetLayerDefn().GetFieldDefn(i))
            out_layer.CreateField(ogr.FieldDefn("gemeente", ogr.OFTString))
        out_layer.StartTransaction()
        for f in sl:
            pid = f.GetField("pand_id")
            if pid in seen:
                continue
            # Look up the municipality at the building's centre
            c = f.GetGeometryRef().Centroid()
            col = int((c.GetX() - gt[0]) / gt[1])
            row = int((c.GetY() - gt[3]) / gt[5])
            if not (0 <= row < lookup.shape[0] and 0 <= col < lookup.shape[1]) or lookup[row, col] == 0:
                continue      # outside the province (or on water between boundaries)
            seen.add(pid)
            gem = names[lookup[row, col]]
            nf = ogr.Feature(out_layer.GetLayerDefn())
            nf.SetFrom(f)
            nf.SetField("gemeente", gem)
            out_layer.CreateFeature(nf)
            # Add its homes to the municipality's totals (weighted by homes,
            # not buildings); no class = no facade ring
            s = stats[gem]
            homes = f.GetField("n_woningen") or 0
            s["gebouwen"] += 1
            s["woningen"] += homes
            k = f.GetField("klasse")
            s[f"w_{k}" if k else "w_geen_ring"] += homes
        out_layer.CommitTransaction()
        print(f"  {path.stem}: total {len(seen):,} unique buildings so far", flush=True)
    out_ds = None
    tmp_path.replace(out_path)

    # Summary: one row per municipality plus a province total, as percentages
    # of homes per class; "3_of_meer" = classes 3-5, 6-7 and 8+
    csv_path = config.PROCESSED_DIR / f"{config.PROVINCE_SLUG}_samenvatting.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["gemeente", "gebouwen", "woningen", *[f"pct_woningen_{c}" for c in CLASSES],
                    "pct_woningen_geen_ring", "pct_woningen_3_of_meer"])
        total = defaultdict(int)
        for s in stats.values():
            for k, v in s.items():
                total[k] += v
        for gem, s in [*sorted(stats.items()), (config.PROVINCE, total)]:
            pct = lambda n: round(100 * n / s["woningen"], 1) if s["woningen"] else 0.0
            w.writerow([gem, s["gebouwen"], s["woningen"], *[pct(s[f"w_{c}"]) for c in CLASSES],
                        pct(s["w_geen_ring"]), pct(sum(s[f"w_{c}"] for c in CLASSES[2:]))])
    print(f"Wrote {out_path.name} ({len(seen):,} buildings) and {csv_path.name}")


if __name__ == "__main__":
    main()
