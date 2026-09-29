"""
build_tiles.py — Build the 3-30-300 web map's vector tiles
(web/3-30-300/data/30-300.pmtiles) from the FME results for the 30 and the 300.
The 3 comes from the 3-regel map's own tile file (web/build_tiles.py).

Layers:
  wijken      zoom 7-11   CBS wijken 2023   } canopy cover (the 30) and the
  buurten     zoom 11-13  CBS buurten 2023  } share of homes within 5 / 15
                                              minutes' walk of green (the 300)
  iso5, iso15 zoom 9-16   5- and 15-minute walking isochrones from the entrances
  ingangen    zoom 13-16  entrances of parks and woods
  woningen    zoom 13-16  every BAG pand with a woonfunctie and its walking class

Inputs (FME output on the share, see SRC below):
  30_regel_v2.gdb   FeatureClass1 (wijken), FeatureClass_buurt (buurten):
                    Percentage_groen = crown area / area, per wijk/buurt
  300.gdb           _300regel / _300regel_15: every BAG pand, _related_suppliers
                    = 1 when it lies in a 5 / 15 minute pedestrian isochrone
                    (Valhalla) from an entrance of a park or wood >= 300 m2;
                    isochrones_dissolved(_15); ingang_parken

Usage (OSGeo4W Python, reads R:):
    python web/3-30-300/build_tiles.py
"""

import json
from pathlib import Path

from osgeo import gdal, ogr, osr
gdal.UseExceptions()
ogr.UseExceptions()

SRC = Path(r"R:\ESRI\BEHEER\Projecten\Tijdelijk_Roberto\3-30-300\fme output")
OUT = Path(__file__).resolve().parent / "data" / "30-300.pmtiles"
ZOOMS = {"wijken": (7, 11), "buurten": (11, 13), "iso15": (9, 16), "iso5": (9, 16),
         "ingangen": (13, 16), "woningen": (13, 16)}
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


def areas(dst, pts):
    """Wijken and buurten: canopy cover, and the share of homes by walking class."""
    gdb = ogr.Open(str(SRC / "30_regel_v2.gdb"))
    for level, layer, name in (("wijken", "FeatureClass1", "wijknaam"),
                               ("buurten", "FeatureClass_buurt", "buurtnaam")):
        src = gdb.GetLayerByName(layer)
        out = dst.CreateLayer(level, RD, ogr.wkbMultiPolygon)
        for fname, ftype in (("naam", ogr.OFTString), ("gemeente", ogr.OFTString),
                             ("water", ogr.OFTInteger), ("groen", ogr.OFTReal),
                             ("kroon_m2", ogr.OFTInteger), ("woningen", ogr.OFTInteger),
                             ("pct_5", ogr.OFTReal), ("pct_15", ogr.OFTReal)):
            out.CreateField(ogr.FieldDefn(fname, ftype))
        out.StartTransaction()
        for f in src:
            g = f.GetGeometryRef()
            counts = {"5": 0, "15": 0, "ver": 0}
            pts.SetSpatialFilter(g)
            for p in pts:
                counts[p.GetField("klasse")] += p.GetField("woningen")
            total = sum(counts.values())
            nf = ogr.Feature(out.GetLayerDefn())
            nf.SetField("naam", f.GetField(name))
            nf.SetField("gemeente", f.GetField("gemeentenaam"))
            nf.SetField("water", 1 if f.GetField("water") == "JA" else 0)
            groen = f.GetField("Percentage_groen")
            if groen is not None:
                nf.SetField("groen", max(0.0, num(groen)))
            kroon = f.GetField("totaal_kroonoppervlak_m2")
            if kroon not in (None, ""):
                nf.SetField("kroon_m2", round(float(kroon)))
            nf.SetField("woningen", total)
            if total:
                nf.SetField("pct_5", num(100 * counts["5"] / total))
                nf.SetField("pct_15", num(100 * (counts["5"] + counts["15"]) / total))
            nf.SetGeometry(g)
            out.CreateFeature(nf)
        out.CommitTransaction()
        pts.SetSpatialFilter(None)
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
    areas(dst, pts)
    copy(dst, "isochrones_dissolved", "iso5")
    copy(dst, "isochrones_dissolved_15", "iso15")
    copy(dst, "ingang_parken", "ingangen", ("type", "name"))
    dst.DeleteLayer(pts.GetName())
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
