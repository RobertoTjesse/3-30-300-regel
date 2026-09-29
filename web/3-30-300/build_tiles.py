"""
build_tiles.py — Build the 3-30-300 web map's vector tiles
(web/3-30-300/data/30-300.pmtiles) from the FME results for the 30 and the 300.
The 3 comes from the 3-regel map's own tile file (web/build_tiles.py).

Layers:
  gemeenten   zoom 6-9    } the same areas as the 3-regel map, with canopy
  wijken      zoom 9-11   } cover (the 30) and the share of homes within 5 / 15
  buurten     zoom 11-12  } minutes' walk of green (the 300)
  iso5, iso15 zoom 9-16   5- and 15-minute walking isochrones from the entrances
  ingangen    zoom 13-16  entrances of parks and woods
  woningen    zoom 13-16  every BAG pand with a woonfunctie and its walking class

Inputs:
  ZuidHolland_gebieden.gpkg   gemeenten, wijken 2025, buurten 2025 (06_area_summaries.py)
  FME output on the share (SRC):
  30_regel_v2.gdb   FeatureClass_buurt: crown area (sum of NEO crowns touching
                    the buurt) and land area per CBS buurt 2023. Carried over to
                    the areas above in proportion to overlapping area.
  300.gdb           _300regel / _300regel_15: every BAG pand, _related_suppliers
                    = 1 when it lies in a 5 / 15 minute pedestrian isochrone
                    (Valhalla) from an entrance of a park or wood >= 300 m2;
                    isochrones_dissolved(_15); ingang_parken

Usage (OSGeo4W Python, reads R:):
    python web/3-30-300/build_tiles.py [path\\to\\ZuidHolland_gebieden.gpkg]
"""

import json
import sys
from pathlib import Path

from osgeo import gdal, ogr, osr
gdal.UseExceptions()
ogr.UseExceptions()

SRC = Path(r"R:\ESRI\BEHEER\Projecten\Tijdelijk_Roberto\3-30-300\fme output")
GEBIEDEN = Path(sys.argv[1]) if len(sys.argv) > 1 else \
    Path(__file__).resolve().parents[2] / "data" / "processed" / "ZuidHolland_gebieden.gpkg"
OUT = Path(__file__).resolve().parent / "data" / "30-300.pmtiles"
ZOOMS = {"gemeenten": (6, 9), "wijken": (9, 11), "buurten": (11, 12),
         "iso15": (9, 16), "iso5": (9, 16), "ingangen": (13, 16), "woningen": (13, 16)}
GONE = {"Pand gesloopt", "Niet gerealiseerd pand", "Pand buiten gebruik"}

RD = osr.SpatialReference()
RD.ImportFromEPSG(28992)
RD.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)


def num(v, digits=1):
    return None if v is None else round(float(v), digits)


def woningen(dst):
    """Homes with their class ('5', '15' or 'ver'); also returns the point layer
    of their centroids for the area shares."""
    gdb = ogr.Open(str(SRC / "300.gdb"))
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
        n = f.GetField("gebr_woonfunctie") or 0
        if n <= 0 or f.GetField("status") in GONE:
            continue
        g = f.GetGeometryRef().Clone()
        g.FlattenTo2D()
        if (f.GetField("_related_suppliers") or 0) >= 1:
            klasse = "5"
        elif f.GetField("identificatie") in in15:
            klasse = "15"
        else:
            klasse = "ver"
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


def buurten_2023(dst):
    """The FME 30 per CBS buurt 2023: crown area and land area (m2), as a layer
    to apportion from. FME: Percentage_groen = crown m2 / 100 / land ha."""
    gdb = ogr.Open(str(SRC / "30_regel_v2.gdb"))
    src = gdb.GetLayerByName("FeatureClass_buurt")
    out = dst.CreateLayer("buurten_2023", RD, ogr.wkbMultiPolygon)
    for fname in ("kroon", "land"):
        out.CreateField(ogr.FieldDefn(fname, ogr.OFTReal))
    dst.StartTransaction()
    for f in src:
        kroon = f.GetField("totaal_kroonoppervlak_m2")
        if kroon in (None, ""):              # no crown data (all of Voorne aan Zee): leave out
            continue
        land = f.GetField("oppervlakte_land_in_ha") or 0
        nf = ogr.Feature(out.GetLayerDefn())
        nf.SetField("kroon", float(kroon))
        nf.SetField("land", max(land, 0) * 1e4)          # -99997 = no land
        g = f.GetGeometryRef().Clone()
        nf.SetGeometry(g if g.IsValid() else g.MakeValid())
        out.CreateFeature(nf)
    dst.CommitTransaction()
    return out


def areas(dst, pts, b23):
    """Gemeenten, wijken and buurten of the 3-regel map (same polygons):
    canopy cover apportioned from the 2023 buurten by overlapping area, and the
    share of homes by walking class."""
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
        for f in src:
            g = f.GetGeometryRef()
            valid = g if g.IsValid() else g.MakeValid()
            counts = {"5": 0, "15": 0, "ver": 0}
            pts.SetSpatialFilter(g)
            for p in pts:
                counts[p.GetField("klasse")] += p.GetField("woningen")
            total = sum(counts.values())
            kroon = land = 0.0
            b23.SetSpatialFilter(g)
            for b in b23:
                bg = b.GetGeometryRef()
                share = valid.Intersection(bg).GetArea() / bg.GetArea() if bg.GetArea() else 0
                kroon += share * b.GetField("kroon")
                land += share * b.GetField("land")
            nf = ogr.Feature(out.GetLayerDefn())
            nf.SetField("naam", f.GetField("naam"))
            nf.SetField("gemeente", f.GetField("gemeente"))
            if land > 0:
                nf.SetField("groen", num(100 * kroon / land))
                nf.SetField("kroon_m2", round(kroon))
            nf.SetField("woningen", total)
            if total:
                nf.SetField("pct_5", num(100 * counts["5"] / total))
                nf.SetField("pct_15", num(100 * (counts["5"] + counts["15"]) / total))
            nf.SetGeometry(g)
            out.CreateFeature(nf)
        dst.CommitTransaction()
        pts.SetSpatialFilter(None)
        b23.SetSpatialFilter(None)
        print(f"{level}: {out.GetFeatureCount():,}")


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


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    staging = OUT.with_suffix(".staging.gpkg")
    staging.unlink(missing_ok=True)
    dst = ogr.GetDriverByName("GPKG").CreateDataSource(str(staging))
    pts = woningen(dst)
    areas(dst, pts, buurten_2023(dst))
    copy(dst, "isochrones_dissolved", "iso5")
    copy(dst, "isochrones_dissolved_15", "iso15")
    copy(dst, "ingang_parken", "ingangen", ("type", "name"))
    dst = None

    conf = {layer: {"minzoom": z0, "maxzoom": z1} for layer, (z0, z1) in ZOOMS.items()}
    tmp = OUT.with_suffix(".partial.pmtiles")
    tmp.unlink(missing_ok=True)
    print("Writing vector tiles …")
    gdal.VectorTranslate(str(tmp), str(staging), format="PMTiles", dstSRS="EPSG:3857",
                         layers=list(ZOOMS),
                         datasetCreationOptions=[f"MINZOOM={min(z for z, _ in ZOOMS.values())}",
                                                 f"MAXZOOM={max(z for _, z in ZOOMS.values())}",
                                                 f"CONF={json.dumps(conf)}",
                                                 "NAME=3-30-300 Zuid-Holland: 30 en 300"],
                         callback=gdal.TermProgress_nocb)
    tmp.replace(OUT)
    staging.unlink()
    size = OUT.stat().st_size / 1e6
    print(f"Wrote {OUT} ({size:.0f} MB)" + ("  — over GitHub's 100 MB file limit!" if size > 100 else ""))


if __name__ == "__main__":
    main()
