"""
build_tiles_30_300.py — Build the web map's vector tiles for the 30 (from BKB
2024) and the 300 (from the FME results), in two files. The 3 has its own tile file
(web/build_tiles.py -> web/data/3.pmtiles), the 300's parks and woods too
(web/build_tiles_groen.py -> web/data/groen.pmtiles).

web/data/30-300.pmtiles:
  gemeenten   zoom 6-9    } the same areas as the 3 map, with canopy
  wijken      zoom 9-11   } cover (the 30) and the share of homes within 5 / 15
  buurten     zoom 11-12  } minutes' walk of green (the 300)
  woningen    zoom 13-16  every BAG pand with a woonfunctie and its walking class
web/data/looptijd.pmtiles (the map's optional "walking zones" layers):
  iso5, iso15 zoom 9-16   5- and 15-minute walking isochrones from the entrances
  ingangen    zoom 13-16  entrances of parks and woods
A file of its own because MapLibre re-processes every loaded tile of a source
when a layer of it is switched on or off: with the zones inside
30-300.pmtiles, ticking them re-processed all the (heavy) building tiles.

Inputs:
  data/processed/<Province>_gebieden.gpkg   gemeenten, wijken 2025, buurten 2025
                                            (indicator_3_bomen/etl/06_area_summaries.py)
  data/processed/<Province>_kroonbedekking.csv   the 30: canopy cover per
                    gemeente, wijk and buurt from BKB 2024 on the same CBS 2025
                    areas (indicator_30_kroonbedekking/etl/kroonbedekking_gebieden.py)
  config.FME_OUTPUT_DIR (set in indicator_3_bomen/etl/config_local.py):
  300.gdb           _300regel / _300regel_15: every BAG pand, _related_suppliers
                    = 1 when it lies in a 5 / 15 minute pedestrian isochrone
                    (Valhalla) from an entrance of a park or wood >= 300 m2;
                    isochrones_dissolved(_15); ingang_parken

Usage:
    python web/build_tiles_30_300.py [--fme DIR] [--gebieden GPKG] [--kroon CSV]
(all default to the paths in indicator_3_bomen/etl/config.py / config_local.py)
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
_args.add_argument("--fme", type=Path, default=config.FME_OUTPUT_DIR,
                   help="folder with 300.gdb")
_args.add_argument("--gebieden", type=Path,
                   default=config.PROCESSED_DIR / f"{config.PROVINCE_SLUG}_gebieden.gpkg")
_args.add_argument("--kroon", type=Path,
                   default=config.PROCESSED_DIR / f"{config.PROVINCE_SLUG}_kroonbedekking.csv",
                   help="the 30 per area (kroonbedekking_gebieden.py)")
ARGS = _args.parse_args()
SRC = ARGS.fme
GEBIEDEN = ARGS.gebieden
DATA = Path(__file__).resolve().parent / "data"
# Output file -> {layer: (minzoom, maxzoom)}; the map over-zooms the last level
OUTPUTS = {
    "30-300.pmtiles": {"gemeenten": (6, 9), "wijken": (9, 11), "buurten": (11, 12), "woningen": (13, 16)},
    "looptijd.pmtiles": {"iso15": (9, 16), "iso5": (9, 16), "ingangen": (13, 16)},
}
GONE = {"Pand gesloopt", "Niet gerealiseerd pand", "Pand buiten gebruik"}

# RD New (the Dutch grid, metres), with x = easting first
RD = osr.SpatialReference()
RD.ImportFromEPSG(28992)
RD.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)


def num(v, digits=1):
    """Round to one decimal for the tiles; None (no value) stays None."""
    return None if v is None else round(float(v), digits)


def woningen(dst):
    """Homes with their class ('5', '15' or 'ver'); also returns the point layer
    of their centroids for the area shares."""
    gdb = ogr.Open(str(SRC / "300.gdb"))
    # Ids of the panden within 15 minutes (a separate FME layer)
    in15 = {f.GetField("identificatie") for f in gdb.GetLayerByName("_300regel_15")
            if (f.GetField("_related_suppliers") or 0) >= 1}
    src = gdb.GetLayerByName("_300regel")
    out = dst.CreateLayer("woningen", RD, ogr.wkbMultiPolygon)
    pts = dst.CreateLayer("punten", RD, ogr.wkbPoint)
    for lyr in (out, pts):
        lyr.CreateField(ogr.FieldDefn("klasse", ogr.OFTString))
        lyr.CreateField(ogr.FieldDefn("woningen", ogr.OFTInteger))
    out.CreateField(ogr.FieldDefn("bouwjaar", ogr.OFTInteger))
    dst.StartTransaction()                  # GPKG: one transaction for both layers
    for f in src:
        # Only existing panden with at least one home (n = number of homes)
        n = f.GetField("gebr_woonfunctie") or 0
        if n <= 0 or f.GetField("status") in GONE:
            continue
        g = f.GetGeometryRef().Clone()
        g.FlattenTo2D()
        # Walking class: within 5 minutes, else within 15, else further
        if (f.GetField("_related_suppliers") or 0) >= 1:
            klasse = "5"
        elif f.GetField("identificatie") in in15:
            klasse = "15"
        else:
            klasse = "ver"
        # The pand for the map, and a point inside it for counting per area
        nf = ogr.Feature(out.GetLayerDefn())
        nf.SetField("klasse", klasse)
        nf.SetField("woningen", n)
        nf.SetField("bouwjaar", f.GetField("bouwjaar"))
        nf.SetGeometry(g)
        out.CreateFeature(nf)
        pf = ogr.Feature(pts.GetLayerDefn())
        pf.SetField("klasse", klasse)
        pf.SetField("woningen", n)
        pf.SetGeometry(g.PointOnSurface())
        pts.CreateFeature(pf)
    dst.CommitTransaction()
    print(f"woningen: {out.GetFeatureCount():,} panden")
    return pts


def kroonbedekking():
    """The 30 from BKB 2024: {(level, key): (canopy %, crown m2)}, with the
    gemeenten keyed by name (the map's gemeenten have no code) and the wijken
    and buurten by CBS code."""
    out = {}
    with open(ARGS.kroon, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            level = {"gemeente": "gemeenten", "wijk": "wijken", "buurt": "buurten"}[r["niveau"]]
            key = r["naam"] if level == "gemeenten" else r["code"]
            out[(level, key)] = (float(r["pct"]) if r["pct"] else None, float(r["kroon_m2"]))
    return out


def areas(dst, pts, kroon):
    """Gemeenten, wijken and buurten of the 3 map (same polygons): canopy
    cover from BKB 2024 (the 30) and the share of homes by walking class (the 300)."""
    src_ds = ogr.Open(str(GEBIEDEN))
    for level in ("gemeenten", "wijken", "buurten"):
        src = src_ds.GetLayerByName(level)
        out = dst.CreateLayer(level, RD, ogr.wkbMultiPolygon)
        for fname, ftype in (("naam", ogr.OFTString), ("gemeente", ogr.OFTString),
                             ("groen", ogr.OFTReal), ("kroon_m2", ogr.OFTInteger),
                             ("woningen", ogr.OFTInteger),
                             ("pct_5", ogr.OFTReal), ("pct_15", ogr.OFTReal)):
            out.CreateField(ogr.FieldDefn(fname, ftype))
        dst.StartTransaction()
        missing = 0
        for f in src:
            g = f.GetGeometryRef()
            # The 300: homes per walking class among the pand points in the area
            counts = {"5": 0, "15": 0, "ver": 0}
            pts.SetSpatialFilter(g)
            for p in pts:
                counts[p.GetField("klasse")] += p.GetField("woningen")
            total = sum(counts.values())
            nf = ogr.Feature(out.GetLayerDefn())
            nf.SetField("naam", f.GetField("naam"))
            nf.SetField("gemeente", f.GetField("gemeente"))
            # The 30 (no value for an area without land)
            key = f.GetField("naam") if level == "gemeenten" else f.GetField("code")
            pct, kroon_m2 = kroon.get((level, key), (None, None))
            if kroon_m2 is None:
                missing += 1
            elif pct is not None:
                nf.SetField("groen", num(pct))
                nf.SetField("kroon_m2", round(kroon_m2))
            nf.SetField("woningen", total)
            if total:
                nf.SetField("pct_5", num(100 * counts["5"] / total))
                nf.SetField("pct_15", num(100 * (counts["5"] + counts["15"]) / total))
            nf.SetGeometry(g)
            out.CreateFeature(nf)
        dst.CommitTransaction()
        pts.SetSpatialFilter(None)
        print(f"{level}: {out.GetFeatureCount():,}" + (f"  ({missing} without a 30 value!)" if missing else ""))


def copy(dst, src_layer, name, fields=()):
    """Copy a layer (2D, into RD) keeping only the given string fields."""
    gdb = ogr.Open(str(SRC / "300.gdb"))
    src = gdb.GetLayerByName(src_layer)
    srs = src.GetSpatialRef().Clone()
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    ct = osr.CoordinateTransformation(srs, RD)
    out = dst.CreateLayer(name, RD, ogr.wkbUnknown)
    for fname in fields:
        out.CreateField(ogr.FieldDefn(fname, ogr.OFTString))
    out.StartTransaction()
    for f in src:
        # Drop Z and reproject (the entrances are stored in lat/lon)
        g = f.GetGeometryRef().Clone()
        g.FlattenTo2D()
        g.Transform(ct)
        nf = ogr.Feature(out.GetLayerDefn())
        for fname in fields:
            nf.SetField(fname, f.GetField(fname))
        nf.SetGeometry(g)
        out.CreateFeature(nf)
    out.CommitTransaction()
    print(f"{name}: {out.GetFeatureCount():,}")


def write(staging, out, zooms):
    """One PMTiles file in web Mercator with the given layers of the staging
    GeoPackage, each at its own zoom range; written to .partial, then renamed."""
    conf = {layer: {"minzoom": z0, "maxzoom": z1} for layer, (z0, z1) in zooms.items()}
    tmp = out.with_suffix(".partial.pmtiles")
    tmp.unlink(missing_ok=True)
    print(f"Writing {out.name} …")
    gdal.VectorTranslate(str(tmp), str(staging), format="PMTiles", dstSRS="EPSG:3857",
                         layers=list(zooms),
                         datasetCreationOptions=[f"MINZOOM={min(z for z, _ in zooms.values())}",
                                                 f"MAXZOOM={max(z for _, z in zooms.values())}",
                                                 f"CONF={json.dumps(conf)}",
                                                 f"NAME=3-30-300 {config.PROVINCE}: {out.stem}"],
                         callback=gdal.TermProgress_nocb)
    tmp.replace(out)
    size = out.stat().st_size / 1e6
    print(f"Wrote {out} ({size:.0f} MB)" + ("  — over GitHub's 100 MB file limit!" if size > 100 else ""))


def main():
    """Collect all layers in a temporary GeoPackage, then write the PMTiles
    files of OUTPUTS from it (the helper layer "punten" goes into neither)."""
    DATA.mkdir(parents=True, exist_ok=True)
    staging = DATA / "30-300.staging.gpkg"
    staging.unlink(missing_ok=True)
    dst = ogr.GetDriverByName("GPKG").CreateDataSource(str(staging))
    pts = woningen(dst)
    areas(dst, pts, kroonbedekking())
    copy(dst, "isochrones_dissolved", "iso5")
    copy(dst, "isochrones_dissolved_15", "iso15")
    copy(dst, "ingang_parken", "ingangen", ("type", "name"))
    dst = None

    for name, zooms in OUTPUTS.items():
        write(staging, DATA / name, zooms)
    staging.unlink()


if __name__ == "__main__":
    main()
