"""
build_tiles_samen.py — EXPERIMENT (branch test/samen-gewogen): vector tiles
for a fourth, compound map that combines the 3, the 30 and the 300 with
weights the visitor sets with sliders (web/data/samen.pmtiles).

Every home gets a score from 0 to 100 per indicator, in two variants:
  gradual (s3, s30, s300)    3:   min(visible trees, 3) / 3
                             30:  min(canopy within 500 m / 30%, 1)
                             300: 100 within 425 m walk (5 min), falling
                                  linearly to 0 at 1,275 m (15 min)
  This is min-max normalisation (OECD/JRC Handbook on Constructing Composite
  Indicators, 2008) with bounds from the rule and the data instead of from
  the sample: 0 = the worst value that is measured (0 trees, 0% canopy, and
  1,275 m, beyond which 03_grass.py records no distance: MAX_DISTANCE_M),
  100 = the target of the rule, with everything beyond the target capped.
  pass/fail (p3, p30, p300)  100 if the home meets that part of the rule, else 0
The map combines them as  (w3*s3 + w30*s30 + w300*s300) / (w3 + w30 + w300).
That is linear in the scores, so the mean over the homes of an area of this
compound equals the compound of the area means: the tiles hold the mean score
per indicator per area, and the browser recombines them for any weights,
without rebuilding anything.

Layers (same areas and zooms as the other maps):
  gemeenten   zoom 6-9    } woningen, and per indicator the mean gradual score
  wijken      zoom 9-11   } (s3, s30, s300) and the share of homes that meet it
  buurten     zoom 11-12  } (p3, p30, p300); alle = share meeting all three
  woningen    zoom 13-16  every residential building: the same scores

A building without a free facade scores 0 on the 3, as the 3's own area
shares count it (06_area_summaries.py). A building without a result for the
30 or the 300 has none for that indicator: its weight drops out (per
building and in the area mean of that indicator).

Usage:
    python web/build_tiles_samen.py [--data DIR]
(DIR: the repository's data folder, default config.DATA_DIR; pass the main
checkout's data folder when running from a worktree)
"""

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "indicator_3_bomen" / "etl"))
import config  # noqa: E402

from osgeo import gdal, ogr, osr  # noqa: E402
gdal.UseExceptions()
ogr.UseExceptions()

_args = argparse.ArgumentParser(description=__doc__.splitlines()[1])
_args.add_argument("--data", type=Path, default=config.DATA_DIR)
ARGS = _args.parse_args()
PROCESSED = ARGS.data / "processed"
SLUG = config.PROVINCE_SLUG
OUT = Path(__file__).resolve().parent / "data" / "samen.pmtiles"
ZOOMS = {"gemeenten": (6, 9), "wijken": (9, 11), "buurten": (11, 12), "woningen": (13, 16)}
NEAR, FAR = 425, 1275      # metres: 5 minutes' walk (the target), 15 (the last distance measured)
KEYS = ("3", "30", "300")

RD = osr.SpatialReference()
RD.ImportFromEPSG(28992)
RD.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)


def score_300(d):
    """Gradual 300 score of one home from its walking distance (None: beyond
    FAR, not measured, score 0)."""
    if d is None or d >= FAR:
        return 0.0
    return 100.0 if d <= NEAR else 100.0 * (FAR - d) / (FAR - NEAR)


def woningen(dst):
    """Buildings with their scores (layer woningen), and per building a point
    with the sums the areas need (layer punten)."""
    # The 300 per home, summed per building: [homes, sum of gradual scores, homes within 5 min]
    per_pand = {}
    homes_ds = ogr.Open(str(ARGS.data / "interim" / "300" / SLUG / "300.gpkg"))   # 05_combine.py
    for f in homes_ds.GetLayerByName("homes"):
        d = f.GetField("afstand_m")
        row = per_pand.setdefault(f.GetField("pand_id"), [0, 0.0, 0])
        row[0] += 1
        row[1] += score_300(d)
        row[2] += d is not None and d <= NEAR
    with open(PROCESSED / f"{SLUG}_kroon500_woningen.csv", encoding="utf-8") as fh:
        kroon = {r["pand_id"]: float(r["kroon"]) for r in csv.DictReader(fh) if r["kroon"]}

    src_ds = ogr.Open(str(PROCESSED / f"{SLUG}_woningen.gpkg"))
    out = dst.CreateLayer("woningen", RD, ogr.wkbMultiPolygon)
    pts = dst.CreateLayer("punten", RD, ogr.wkbPoint)
    out.CreateField(ogr.FieldDefn("woningen", ogr.OFTInteger))
    for k in KEYS:
        out.CreateField(ogr.FieldDefn(f"s{k}", ogr.OFTInteger))
        out.CreateField(ogr.FieldDefn(f"p{k}", ogr.OFTInteger))
    # Per point: homes with a value (n), sum of gradual scores (s) and homes meeting it (p)
    for name in [f"{c}{k}" for k in KEYS for c in "nsp"] + ["alle"]:
        pts.CreateField(ogr.FieldDefn(name, ogr.OFTReal))
    pts.CreateField(ogr.FieldDefn("woningen", ogr.OFTInteger))
    dst.StartTransaction()
    for f in src_ds.GetLayer(0):
        pid = f.GetField("pand_id")
        homes = f.GetField("n_woningen") or 0
        bomen = f.GetField("bomen_zichtbaar")
        k30 = kroon.get(pid)
        r300 = per_pand.get(pid)
        # Per building: gradual score and pass (0/100) per indicator, None = no value
        # No free facade: 0 visible trees for the 3, as 06_area_summaries.py
        # counts these homes (in the denominator, not meeting the 3)
        bomen = bomen or 0
        s = {"3": 100.0 * min(bomen, 3) / 3,
             "30": None if k30 is None else 100.0 * min(k30 / 30, 1),
             "300": None if r300 is None else r300[1] / r300[0]}
        p = {"3": 100 * (bomen >= 3),
             "30": None if k30 is None else 100 * (k30 >= 30),
             # the building's share of its homes within 5 min
             "300": None if r300 is None else 100.0 * r300[2] / r300[0]}
        g = f.GetGeometryRef().Clone()
        g.FlattenTo2D()
        nf = ogr.Feature(out.GetLayerDefn())
        nf.SetField("woningen", homes)
        for k in KEYS:
            if s[k] is not None:
                nf.SetField(f"s{k}", round(s[k]))
                nf.SetField(f"p{k}", round(p[k]))
        nf.SetGeometry(g)
        out.CreateFeature(nf)

        pf = ogr.Feature(pts.GetLayerDefn())
        pf.SetField("woningen", homes)
        for k in KEYS:
            n = (r300[0] if k == "300" else homes) if s[k] is not None else 0
            pf.SetField(f"n{k}", n)
            pf.SetField(f"s{k}", n * (s[k] or 0) / 100)
            pf.SetField(f"p{k}", n * (p[k] or 0) / 100)
        # Homes meeting all three: the building meets the 3 and the 30, the home is within 5 min
        if p["3"] and p["30"] and r300:
            pf.SetField("alle", r300[2])
        else:
            pf.SetField("alle", 0)
        pf.SetGeometry(g.PointOnSurface())
        pts.CreateFeature(pf)
    dst.CommitTransaction()
    print(f"woningen: {out.GetFeatureCount():,} buildings")
    return pts


def areas(dst, pts):
    """Gemeenten, wijken and buurten (the polygons of the other maps) with the
    mean scores and shares of their homes."""
    src_ds = ogr.Open(str(PROCESSED / f"{SLUG}_gebieden.gpkg"))
    sums = [f"{c}{k}" for k in KEYS for c in "nsp"] + ["alle", "woningen"]
    for level in ("gemeenten", "wijken", "buurten"):
        out = dst.CreateLayer(level, RD, ogr.wkbMultiPolygon)
        for name, ftype in [("naam", ogr.OFTString), ("gemeente", ogr.OFTString), ("woningen", ogr.OFTInteger),
                            ("alle", ogr.OFTReal)] + [(f"{c}{k}", ogr.OFTReal) for k in KEYS for c in "sp"]:
            out.CreateField(ogr.FieldDefn(name, ftype))
        dst.StartTransaction()
        for f in src_ds.GetLayerByName(level):
            g = f.GetGeometryRef()
            t = dict.fromkeys(sums, 0.0)
            pts.SetSpatialFilter(g)
            for pt in pts:
                for name in sums:
                    t[name] += pt.GetField(name) or 0
            nf = ogr.Feature(out.GetLayerDefn())
            nf.SetField("naam", f.GetField("naam"))
            nf.SetField("gemeente", f.GetField("gemeente"))
            nf.SetField("woningen", int(t["woningen"]))
            for k in KEYS:
                if t[f"n{k}"]:
                    nf.SetField(f"s{k}", round(100 * t[f"s{k}"] / t[f"n{k}"], 1))
                    nf.SetField(f"p{k}", round(100 * t[f"p{k}"] / t[f"n{k}"], 1))
            if t["n300"]:
                nf.SetField("alle", round(100 * t["alle"] / t["n300"], 1))
            nf.SetGeometry(g)
            out.CreateFeature(nf)
        dst.CommitTransaction()
        pts.SetSpatialFilter(None)
        print(f"{level}: {out.GetFeatureCount():,}")


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    staging = OUT.with_suffix(".staging.gpkg")
    staging.unlink(missing_ok=True)
    dst = ogr.GetDriverByName("GPKG").CreateDataSource(str(staging))
    areas(dst, woningen(dst))
    dst = None

    conf = {layer: {"minzoom": z0, "maxzoom": z1} for layer, (z0, z1) in ZOOMS.items()}
    tmp = OUT.with_suffix(".partial.pmtiles")
    tmp.unlink(missing_ok=True)
    print(f"Writing {OUT.name} …")
    gdal.VectorTranslate(str(tmp), str(staging), format="PMTiles", dstSRS="EPSG:3857", layers=list(ZOOMS),
                         datasetCreationOptions=["MINZOOM=6", "MAXZOOM=16", f"CONF={json.dumps(conf)}",
                                                 f"NAME=3-30-300 {config.PROVINCE}: samen"],
                         callback=gdal.TermProgress_nocb)
    tmp.replace(OUT)
    staging.unlink()
    print(f"Wrote {OUT} ({OUT.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
