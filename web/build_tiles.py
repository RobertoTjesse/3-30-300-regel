"""
build_tiles.py — Build the web map's vector tiles (web/data/zuid-holland.pmtiles)
from the pipeline results.

Two layers:
  gemeenten   zoom 6-12: current municipalities of the province with the
              per-municipality summary (05_merge_province.py), for the
              province-wide overview
  woningen    zoom 13-16 (the map over-zooms beyond): every residential
              building with its class and number of visible trees

Inputs:
  data/processed/ZuidHolland_woningen.gpkg, ZuidHolland_samenvatting.csv
  data/interim/gemeenten.gpkg, provincies.gpkg      (see 05_merge_province.py)

Usage:
    python web/build_tiles.py
"""

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "etl"))
import config  # noqa: E402

from osgeo import gdal, ogr  # noqa: E402
gdal.UseExceptions()
ogr.UseExceptions()

PROVINCE = "Zuid-Holland"
OUT = Path(__file__).resolve().parent / "data" / "zuid-holland.pmtiles"
BUILDING_ZOOMS = (13, 16)
MUNICIPALITY_ZOOMS = (6, 12)


def municipalities(dst):
    """Copy the province's municipalities into dst, with the summary columns."""
    with open(config.PROCESSED_DIR / "ZuidHolland_samenvatting.csv", encoding="utf-8") as fh:
        summary = {r["gemeente"]: r for r in csv.DictReader(fh)}

    prov_ds = ogr.Open(str(config.INTERIM_DIR / "provincies.gpkg"))
    prov_layer = prov_ds.GetLayer(0)
    prov_layer.SetAttributeFilter(f"naam = '{PROVINCE}'")
    prov_feat = next(iter(prov_layer))
    prov = prov_feat.GetGeometryRef().Clone()

    src_ds = ogr.Open(str(config.INTERIM_DIR / "gemeenten.gpkg"))
    src = src_ds.GetLayer(0)
    src.SetSpatialFilter(prov)
    out = dst.CreateLayer("gemeenten", src.GetSpatialRef(), ogr.wkbMultiPolygon)
    fields = [("naam", ogr.OFTString), ("woningen", ogr.OFTInteger),
              ("pct_3_of_meer", ogr.OFTReal), ("pct_0", ogr.OFTReal)]
    for name, ftype in fields:
        out.CreateField(ogr.FieldDefn(name, ftype))
    n = 0
    for f in src:
        g = f.GetGeometryRef()
        if not prov.Contains(g.PointOnSurface()):     # same rule as 05_merge_province.py
            continue
        name = f.GetField("naam")
        s = summary.get(name)
        nf = ogr.Feature(out.GetLayerDefn())
        nf.SetField("naam", name)
        if s:
            nf.SetField("woningen", int(s["woningen"]))
            nf.SetField("pct_3_of_meer", float(s["pct_woningen_3_of_meer"]))
            nf.SetField("pct_0", float(s["pct_woningen_0"]))
        nf.SetGeometry(ogr.ForceToMultiPolygon(g.Clone()))
        out.CreateFeature(nf)
        n += 1
    print(f"gemeenten: {n} municipalities ({sum(1 for k in summary if k != PROVINCE)} with scores)")


def buildings(dst):
    """Copy the scored buildings into dst, keeping only the fields the map shows."""
    src_ds = ogr.Open(str(config.PROCESSED_DIR / "ZuidHolland_woningen.gpkg"))
    src = src_ds.GetLayer(0)
    out = dst.CreateLayer("woningen", src.GetSpatialRef(), ogr.wkbMultiPolygon)
    for name, ftype in (("klasse", ogr.OFTString), ("bomen", ogr.OFTInteger),
                        ("woningen", ogr.OFTInteger), ("gemeente", ogr.OFTString)):
        out.CreateField(ogr.FieldDefn(name, ftype))
    out.StartTransaction()
    for f in src:
        nf = ogr.Feature(out.GetLayerDefn())
        nf.SetField("klasse", f.GetField("klasse"))     # NULL = no facade ring
        if f.GetField("bomen_zichtbaar") is not None:
            nf.SetField("bomen", f.GetField("bomen_zichtbaar"))
        nf.SetField("woningen", f.GetField("n_woningen"))
        nf.SetField("gemeente", f.GetField("gemeente"))
        nf.SetGeometry(f.GetGeometryRef())
        out.CreateFeature(nf)
    out.CommitTransaction()
    print(f"woningen: {out.GetFeatureCount():,} buildings")


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    staging = OUT.with_suffix(".staging.gpkg")
    staging.unlink(missing_ok=True)
    dst = ogr.GetDriverByName("GPKG").CreateDataSource(str(staging))
    municipalities(dst)
    buildings(dst)
    dst = None

    conf = {"gemeenten": {"minzoom": MUNICIPALITY_ZOOMS[0], "maxzoom": MUNICIPALITY_ZOOMS[1]},
            "woningen": {"minzoom": BUILDING_ZOOMS[0], "maxzoom": BUILDING_ZOOMS[1]}}
    tmp = OUT.with_suffix(".partial.pmtiles")
    tmp.unlink(missing_ok=True)
    print("Writing vector tiles …")
    gdal.VectorTranslate(str(tmp), str(staging), format="PMTiles", dstSRS="EPSG:3857",
                         datasetCreationOptions=[f"MINZOOM={MUNICIPALITY_ZOOMS[0]}",
                                                 f"MAXZOOM={BUILDING_ZOOMS[1]}",
                                                 f"CONF={json.dumps(conf)}",
                                                 "NAME=3-30-300 Zuid-Holland"],
                         callback=gdal.TermProgress_nocb)
    tmp.replace(OUT)
    staging.unlink()
    size = OUT.stat().st_size / 1e6
    print(f"Wrote {OUT} ({size:.0f} MB)" + ("  — over GitHub's 100 MB file limit!" if size > 100 else ""))


if __name__ == "__main__":
    main()
