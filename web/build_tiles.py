"""
build_tiles.py — Build the web map's vector tiles (web/data/3.pmtiles)
from the pipeline results.

Layers (the map shows one area level at a time, by zoom):
  gemeenten   zoom 6-9    current municipalities    } share of homes with
  wijken      zoom 9-11   CBS wijken 2025           } >= 3 visible trees etc.
  buurten     zoom 11-12  CBS buurten 2025          } (06_area_summaries.py)
  woningen    zoom 13-16 (the map over-zooms beyond): every residential
              building with its class and number of visible trees

Inputs:
  data/processed/<Province>_woningen.gpkg        (05_merge_province.py)
  data/processed/<Province>_gebieden.gpkg        (06_area_summaries.py)

Usage:
    python web/build_tiles.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "indicator_3_bomen" / "etl"))
import config  # noqa: E402

from osgeo import gdal, ogr  # noqa: E402
gdal.UseExceptions()
ogr.UseExceptions()

OUT = Path(__file__).resolve().parent / "data" / "3.pmtiles"
ZOOMS = {"gemeenten": (6, 9), "wijken": (9, 11), "buurten": (11, 12), "woningen": (13, 16)}


def areas(dst):
    """Copy the gemeenten, wijken and buurten with the fields the map shows."""
    src_ds = ogr.Open(str(config.PROCESSED_DIR / f"{config.PROVINCE_SLUG}_gebieden.gpkg"))
    for level in ("gemeenten", "wijken", "buurten"):
        src = src_ds.GetLayerByName(level)
        out = dst.CreateLayer(level, src.GetSpatialRef(), ogr.wkbMultiPolygon)
        fields = [("naam", ogr.OFTString, "naam"), ("gemeente", ogr.OFTString, "gemeente"),
                  ("woningen", ogr.OFTInteger, "woningen"),
                  ("pct_3_of_meer", ogr.OFTReal, "pct_woningen_3_of_meer"),
                  ("pct_0", ogr.OFTReal, "pct_woningen_0")]
        for name, ftype, _ in fields:
            out.CreateField(ogr.FieldDefn(name, ftype))
        out.StartTransaction()
        for f in src:
            nf = ogr.Feature(out.GetLayerDefn())
            for name, _, src_name in fields:
                if f.GetField(src_name) is not None:
                    nf.SetField(name, f.GetField(src_name))
            nf.SetGeometry(f.GetGeometryRef())
            out.CreateFeature(nf)
        out.CommitTransaction()
        print(f"{level}: {out.GetFeatureCount():,}")


def buildings(dst):
    """Copy the scored buildings into dst, keeping only the fields the map shows."""
    src_ds = ogr.Open(str(config.PROCESSED_DIR / f"{config.PROVINCE_SLUG}_woningen.gpkg"))
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
    areas(dst)
    buildings(dst)
    dst = None

    conf = {layer: {"minzoom": z0, "maxzoom": z1} for layer, (z0, z1) in ZOOMS.items()}
    tmp = OUT.with_suffix(".partial.pmtiles")
    tmp.unlink(missing_ok=True)
    print("Writing vector tiles …")
    gdal.VectorTranslate(str(tmp), str(staging), format="PMTiles", dstSRS="EPSG:3857",
                         datasetCreationOptions=[f"MINZOOM={min(z for z, _ in ZOOMS.values())}",
                                                 f"MAXZOOM={max(z for _, z in ZOOMS.values())}",
                                                 f"CONF={json.dumps(conf)}",
                                                 f"NAME=330300regel 3 {config.PROVINCE}"],
                         callback=gdal.TermProgress_nocb)
    tmp.replace(OUT)
    staging.unlink()
    size = OUT.stat().st_size / 1e6
    print(f"Wrote {OUT} ({size:.0f} MB)" + ("  — over GitHub's 100 MB file limit!" if size > 100 else ""))


if __name__ == "__main__":
    main()
