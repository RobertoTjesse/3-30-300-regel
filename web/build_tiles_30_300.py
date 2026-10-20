"""
build_tiles_30_300.py — Build the web map's vector tiles for the 30 (canopy
within 500 m of every home, from BKB 2024) and the 300 (from indicator_300_park/etl), in two files. The 3 has
its own tile file (web/build_tiles.py -> web/data/3.pmtiles), the 300's green
areas too (web/build_tiles_groen.py -> web/data/groen.pmtiles).

web/data/30-300.pmtiles:
  gemeenten   zoom 6-9    } the same areas as the 3 map, with the share of homes
  wijken      zoom 9-11   } with >= 30% canopy within 500 m and their mean (the
  buurten     zoom 11-12  } 30), and the share of homes within 300 m, 5 and 15
                            minutes' walk of green (the 300)
  woningen    zoom 13-16  every residential building of the 3: canopy within
                          500 m (the 30), its walking class (by its nearest
                          home) and that walking distance (the 300)
web/data/looptijd.pmtiles (the map's optional "walking zones" layers):
  iso5, iso15 zoom 9-16   5- and 15-minute Valhalla isochrones from the entrances
  ingangen    zoom 13-16  entrances of the green areas
A file of its own because MapLibre re-processes every loaded tile of a source
when a layer of it is switched on or off: with the zones inside
30-300.pmtiles, ticking them re-processed all the (heavy) building tiles.

Inputs:
  data/processed/<Province>_gebieden.gpkg   gemeenten, wijken 2025, buurten 2025
                                            (indicator_3_bomen/etl/06_area_summaries.py)
  data/processed/<Province>_woningen.gpkg   the residential buildings of the 3
  data/processed/<Province>_kroon500_woningen.csv, _kroon500_gebieden.csv
                    the 30 per building and per gemeente, wijk and buurt
                    (indicator_30_kroonbedekking/etl/kroon_500m_woningen.py)
  data/interim/300/<Province>/300.gpkg   the 300 per home (05_combine.py):
                    afstand_m, the walking distance to the nearest entrance
                    (GRASS, or Valhalla where GRASS has more than 300 m and
                    Valhalla finds a shorter way; 0 inside a green area)
  data/interim/300/<Province>/prepared.gpkg, valhalla.gpkg   entrances and
                    isochrones of the 300

Usage:
    python web/build_tiles_30_300.py [--open GPKG] [--gebieden GPKG] [--kroon CSV] [--kroon-woningen CSV]
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
_args.add_argument("--open", type=Path,
                   default=config.INTERIM_DIR / "300" / config.PROVINCE_SLUG / "300.gpkg",
                   help="the 300 per home (indicator_300_park/etl/05_combine.py)")
_args.add_argument("--gebieden", type=Path,
                   default=config.PROCESSED_DIR / f"{config.PROVINCE_SLUG}_gebieden.gpkg")
_args.add_argument("--kroon", type=Path,
                   default=config.PROCESSED_DIR / f"{config.PROVINCE_SLUG}_kroon500_gebieden.csv",
                   help="the 30 per area (kroon_500m_woningen.py)")
_args.add_argument("--kroon-woningen", type=Path,
                   default=config.PROCESSED_DIR / f"{config.PROVINCE_SLUG}_kroon500_woningen.csv",
                   help="the 30 per building (kroon_500m_woningen.py)")
ARGS = _args.parse_args()
GEBIEDEN = ARGS.gebieden
BUILDINGS = config.PROCESSED_DIR / f"{config.PROVINCE_SLUG}_woningen.gpkg"
WORK_300 = config.INTERIM_DIR / "300" / config.PROVINCE_SLUG
LIMITS = {"300": 300, "5": 425, "15": 1275}    # metres: 300 m, 5 and 15 minutes at 5.1 km/h
DATA = Path(__file__).resolve().parent / "data"
# Output file -> {layer: (minzoom, maxzoom)}; the map over-zooms the last level
OUTPUTS = {
    "30-300.pmtiles": {"gemeenten": (6, 9), "wijken": (9, 11), "buurten": (11, 12), "woningen": (13, 16)},
    "looptijd.pmtiles": {"iso15": (9, 16), "iso5": (9, 16), "ingangen": (13, 16)},
}

# RD New (the Dutch grid, metres), with x = easting first
RD = osr.SpatialReference()
RD.ImportFromEPSG(28992)
RD.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)


def num(v, digits=1):
    """Round to one decimal for the tiles; None (no value) stays None."""
    return None if v is None else round(float(v), digits)


def woningen(dst):
    """Residential buildings of the 3 with their canopy cover within 500 m (the
    30), their walking class ('5', '15' or 'ver', by the building's nearest home)
    and that distance (the 300); also returns a point layer
    with the number of homes per building within 300 m, 5 and 15 minutes, for the
    area shares."""
    homes_ds = ogr.Open(str(ARGS.open))
    per_pand = {}      # pand_id: [homes, within 300 m, within 5 min, within 15 min, nearest m]
    for f in homes_ds.GetLayerByName("homes"):
        d = f.GetField("afstand_m")
        row = per_pand.setdefault(f.GetField("pand_id"), [0, 0, 0, 0, None])
        row[0] += 1
        if d is not None:
            for i, key in ((1, "300"), (2, "5"), (3, "15")):
                row[i] += d <= LIMITS[key]
            row[4] = d if row[4] is None else min(row[4], d)
    with open(ARGS.kroon_woningen, encoding="utf-8") as fh:
        kroon = {r["pand_id"]: float(r["kroon"]) for r in csv.DictReader(fh) if r["kroon"]}
    src_ds = ogr.Open(str(BUILDINGS))
    src = src_ds.GetLayer(0)
    out = dst.CreateLayer("woningen", RD, ogr.wkbMultiPolygon)
    pts = dst.CreateLayer("punten", RD, ogr.wkbPoint)
    for lyr in (out, pts):
        lyr.CreateField(ogr.FieldDefn("klasse", ogr.OFTString))
        lyr.CreateField(ogr.FieldDefn("woningen", ogr.OFTInteger))
    out.CreateField(ogr.FieldDefn("afstand", ogr.OFTInteger))
    out.CreateField(ogr.FieldDefn("kroon", ogr.OFTInteger))     # % canopy within 500 m, whole percent
    for name in ("n300", "n5", "n15"):
        pts.CreateField(ogr.FieldDefn(name, ogr.OFTInteger))
    dst.StartTransaction()                  # GPKG: one transaction for both layers
    missing = 0
    for f in src:
        row = per_pand.get(f.GetField("pand_id"))
        if row is None:
            missing += 1
            continue
        homes, n300, n5, n15, nearest = row
        if nearest is None or nearest > LIMITS["15"]:
            klasse = "ver"
        else:
            klasse = "5" if nearest <= LIMITS["5"] else "15"
        g = f.GetGeometryRef().Clone()
        g.FlattenTo2D()
        nf = ogr.Feature(out.GetLayerDefn())
        nf.SetField("klasse", klasse)
        nf.SetField("woningen", homes)
        if nearest is not None:
            nf.SetField("afstand", round(nearest))
        if f.GetField("pand_id") in kroon:
            # rounded down, so that 29.9% stays below the 30 on the map
            nf.SetField("kroon", int(kroon[f.GetField("pand_id")]))
        nf.SetGeometry(g)
        out.CreateFeature(nf)
        pf = ogr.Feature(pts.GetLayerDefn())
        for name, value in (("klasse", klasse), ("woningen", homes), ("n300", n300), ("n5", n5), ("n15", n15)):
            pf.SetField(name, value)
        pf.SetGeometry(g.PointOnSurface())
        pts.CreateFeature(pf)
    dst.CommitTransaction()
    print(f"woningen: {out.GetFeatureCount():,} buildings ({missing:,} without homes in the 300 result)")
    return pts


def kroonbedekking():
    """The 30 per area: {(level, key): (share of homes with >= 30% canopy within
    500 m, mean canopy within 500 m)}, with the gemeenten keyed by name (the
    map's gemeenten have no code) and the wijken and buurten by CBS code."""
    out = {}
    with open(ARGS.kroon, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            level = {"gemeente": "gemeenten", "wijk": "wijken", "buurt": "buurten"}[r["niveau"]]
            key = r["naam"] if level == "gemeenten" else r["code"]
            out[(level, key)] = tuple(float(r[k]) if r[k] else None for k in ("pct_30", "gem_kroon"))
    return out


def areas(dst, pts, kroon):
    """Gemeenten, wijken and buurten of the 3 map (same polygons): the share of
    homes with >= 30% canopy within 500 m and their mean (the 30), and the share
    of homes by walking class (the 300)."""
    src_ds = ogr.Open(str(GEBIEDEN))
    for level in ("gemeenten", "wijken", "buurten"):
        src = src_ds.GetLayerByName(level)
        out = dst.CreateLayer(level, RD, ogr.wkbMultiPolygon)
        for fname, ftype in (("naam", ogr.OFTString), ("gemeente", ogr.OFTString),
                             ("pct_30", ogr.OFTReal), ("kroon", ogr.OFTReal),
                             ("woningen", ogr.OFTInteger),
                             ("pct_300", ogr.OFTReal), ("pct_5", ogr.OFTReal), ("pct_15", ogr.OFTReal)):
            out.CreateField(ogr.FieldDefn(fname, ftype))
        dst.StartTransaction()
        missing = 0
        for f in src:
            g = f.GetGeometryRef()
            # The 300: homes within 300 m, 5 and 15 minutes among the building points in the area
            counts = {"n300": 0, "n5": 0, "n15": 0}
            total = 0
            pts.SetSpatialFilter(g)
            for p in pts:
                total += p.GetField("woningen")
                for k in counts:
                    counts[k] += p.GetField(k)
            nf = ogr.Feature(out.GetLayerDefn())
            nf.SetField("naam", f.GetField("naam"))
            nf.SetField("gemeente", f.GetField("gemeente"))
            # The 30, from the homes in the area (none: an area without homes)
            key = f.GetField("naam") if level == "gemeenten" else f.GetField("code")
            pct_30, gem = kroon.get((level, key), (None, None))
            if pct_30 is None:
                missing += 1
            else:
                nf.SetField("pct_30", num(pct_30))
                nf.SetField("kroon", num(gem))
            nf.SetField("woningen", total)
            if total:
                for k in counts:
                    nf.SetField(f"pct_{k[1:]}", num(100 * counts[k] / total))
            nf.SetGeometry(g)
            out.CreateFeature(nf)
        dst.CommitTransaction()
        pts.SetSpatialFilter(None)
        print(f"{level}: {out.GetFeatureCount():,}" + (f"  ({missing} without homes for the 30)" if missing else ""))


def zones(dst):
    """The 5- and 15-minute Valhalla isochrones (02_valhalla.py) as layers iso5 and iso15."""
    src_ds = ogr.Open(str(WORK_300 / "valhalla.gpkg"))
    src = src_ds.GetLayerByName("isochrones")
    for contour, name in (("5min", "iso5"), ("15min", "iso15")):
        out = dst.CreateLayer(name, RD, ogr.wkbMultiPolygon)
        src.SetAttributeFilter(f"contour = '{contour}'")
        for f in src:
            nf = ogr.Feature(out.GetLayerDefn())
            nf.SetGeometry(f.GetGeometryRef().Clone())
            out.CreateFeature(nf)
        print(f"{name}: {out.GetFeatureCount():,}")


def entrances(dst):
    """The entrances of the green areas (01_prepare.py), with their kind and the green's names."""
    src_ds = ogr.Open(str(WORK_300 / "prepared.gpkg"))
    names = {f.GetField("park_id"): f.GetField("namen") for f in src_ds.GetLayerByName("parks")}
    out = dst.CreateLayer("ingangen", RD, ogr.wkbPoint)
    for fname in ("soort", "naam"):
        out.CreateField(ogr.FieldDefn(fname, ogr.OFTString))
    out.StartTransaction()
    for f in src_ds.GetLayerByName("entrances"):
        nf = ogr.Feature(out.GetLayerDefn())
        nf.SetField("soort", f.GetField("soort"))
        nf.SetField("naam", names.get(f.GetField("park_id")) or "")
        nf.SetGeometry(f.GetGeometryRef().Clone())
        out.CreateFeature(nf)
    out.CommitTransaction()
    print(f"ingangen: {out.GetFeatureCount():,}")


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
    zones(dst)
    entrances(dst)
    dst = None

    for name, zooms in OUTPUTS.items():
        write(staging, DATA / name, zooms)
    staging.unlink()


if __name__ == "__main__":
    main()
