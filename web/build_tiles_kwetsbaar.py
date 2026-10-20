"""
build_tiles_kwetsbaar.py — EXPERIMENT (branch test/samen-gewogen): where the
groups most sensitive to heat live, for two optional map layers
(web/data/kwetsbaar.pmtiles).

Layers:
  ouderen     zoom 10-14  residents aged 65 and older per 100 m square
                          (CBS vierkantstatistieken 100 m, 2024), a point at
                          the square's centre
  geboorten   zoom 8-14   births per buurt (CBS kerncijfers wijken en buurten
                          2024, geboorte_totaal), a point inside the buurt

Why these: CBS publishes no 0-4 age group. Births are the one official count
of babies (under 1); per 100 m square nearly all are suppressed (fewer than
5), so per buurt is the finest level that has them. The 65+ per square are
rounded to 5 by CBS and suppressed below 5 (-99997): those squares are left out.

Both come from PDOK's OGC API Features (CC BY 4.0), cut to the province's
gemeenten (data/processed/<Province>_gebieden.gpkg).

Usage:
    python web/build_tiles_kwetsbaar.py [--data DIR]
"""

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "indicator_3_bomen" / "etl"))
import config  # noqa: E402

from osgeo import gdal, ogr, osr  # noqa: E402
gdal.UseExceptions()
ogr.UseExceptions()

_args = argparse.ArgumentParser(description=__doc__.splitlines()[1])
_args.add_argument("--data", type=Path, default=config.DATA_DIR)
ARGS = _args.parse_args()
OUT = Path(__file__).resolve().parent / "data" / "kwetsbaar.pmtiles"
ZOOMS = {"ouderen": (10, 14), "geboorten": (8, 14)}
YEAR = 2024
API = "https://api.pdok.nl/cbs"
SOURCES = {   # layer: (collection URL, query, field kept as "n")
    "ouderen": (f"{API}/vierkantstatistieken100m/ogc/v1/collections/vierkant100/items",
                {"jaarcode": YEAR}, "aantal_inwoners_65_jaar_en_ouder"),
    "geboorten": (f"{API}/wijken-en-buurten-{YEAR}/ogc/v1/collections/buurten/items",
                  {}, "geboorte_totaal"),
}

RD = osr.SpatialReference()
RD.ImportFromEPSG(28992)
RD.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
WGS = osr.SpatialReference()
WGS.ImportFromEPSG(4326)
WGS.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
TO_RD = osr.CoordinateTransformation(WGS, RD)


def province():
    """The province as one geometry (RD), from the gemeenten of the 3 map."""
    ds = ogr.Open(str(ARGS.data / "processed" / f"{config.PROVINCE_SLUG}_gebieden.gpkg"))
    union = ogr.Geometry(ogr.wkbMultiPolygon)
    for f in ds.GetLayerByName("gemeenten"):
        union = union.Union(f.GetGeometryRef())
    return union


def fetch(url, query, bbox):
    """All features of an OGC API collection within bbox (lon/lat), page by page."""
    q = {"f": "json", "limit": 1000, "bbox": ",".join(f"{v:.5f}" for v in bbox), **query}
    url = url + "?" + urllib.parse.urlencode(q)
    while url:
        with urllib.request.urlopen(url, timeout=120) as r:
            page = json.load(r)
        yield from page["features"]
        url = next((l["href"] for l in page["links"] if l["rel"] == "next"), None)


def main():
    area = province()
    # bbox of the province in lon/lat for the API
    to_wgs = osr.CoordinateTransformation(RD, WGS)
    env = area.Clone()
    env.Transform(to_wgs)
    minx, maxx, miny, maxy = env.GetEnvelope()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    staging = OUT.with_suffix(".staging.gpkg")
    staging.unlink(missing_ok=True)
    dst = ogr.GetDriverByName("GPKG").CreateDataSource(str(staging))
    for layer, (url, query, field) in SOURCES.items():
        out = dst.CreateLayer(layer, RD, ogr.wkbPoint)
        out.CreateField(ogr.FieldDefn("n", ogr.OFTInteger))
        out.CreateField(ogr.FieldDefn("inwoners", ogr.OFTInteger))
        if layer == "geboorten":
            out.CreateField(ogr.FieldDefn("naam", ogr.OFTString))
        dst.StartTransaction()
        seen = suppressed = 0
        for feat in fetch(url, query, (minx, miny, maxx, maxy)):
            seen += 1
            p = feat["properties"]
            n = p.get(field)
            if n is None or n < 0:          # -99997 suppressed, -99995 not yet available
                suppressed += 1
                continue
            g = ogr.CreateGeometryFromJson(json.dumps(feat["geometry"]))
            g.Transform(TO_RD)
            pt = g.PointOnSurface()
            if not area.Contains(pt):
                continue
            nf = ogr.Feature(out.GetLayerDefn())
            nf.SetField("n", n)
            if p.get("aantal_inwoners", -1) >= 0:
                nf.SetField("inwoners", p["aantal_inwoners"])
            if layer == "geboorten":
                nf.SetField("naam", p.get("buurtnaam"))
            nf.SetGeometry(pt)
            out.CreateFeature(nf)
        dst.CommitTransaction()
        print(f"{layer}: {out.GetFeatureCount():,} kept of {seen:,} in the bbox ({suppressed:,} suppressed or missing)")
    dst = None

    conf = {layer: {"minzoom": z0, "maxzoom": z1} for layer, (z0, z1) in ZOOMS.items()}
    tmp = OUT.with_suffix(".partial.pmtiles")
    tmp.unlink(missing_ok=True)
    gdal.VectorTranslate(str(tmp), str(staging), format="PMTiles", dstSRS="EPSG:3857", layers=list(ZOOMS),
                         datasetCreationOptions=["MINZOOM=8", "MAXZOOM=14", f"CONF={json.dumps(conf)}",
                                                 f"NAME=3-30-300 {config.PROVINCE}: kwetsbaar"])
    tmp.replace(OUT)
    staging.unlink()
    print(f"Wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
