"""
build_tiles_groen.py — Build the web map's tiles of the green the 300 walks to
(web/data/groen.pmtiles): the parks and woods themselves, shown on the 300 tab.
A file of its own so 30-300.pmtiles stays under GitHub's 100 MB limit.

Layer:
  groen   zoom 11-14 (the map over-zooms beyond): type, bron and m2 per polygon

Input:
  config.FME_INPUT_DIR / groenvoorzieningen/groenkaart.gdb
          layer groen_uit_osm_top10_2024: OSM and TOP10NL green (2024), the
          input of indicator_300_park/fme/300_2025 regel.fmw. The FME results
          hold only the entrances, so the green is read here with the
          workbench's own selection: its reader leaves out bron
          'top 10 water' (lakes, watercourses, the sea) and its Tester keeps
          _area >= 300 m2 with perimeter / area <= 0.35 (no narrow strips).

Usage:
    python web/build_tiles_groen.py [--groen GDB]
(default: the path in indicator_3_bomen/etl/config.py / config_local.py)
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "indicator_3_bomen" / "etl"))
import config  # noqa: E402

from osgeo import gdal, ogr, osr  # noqa: E402
gdal.UseExceptions()
ogr.UseExceptions()

_args = argparse.ArgumentParser(description=__doc__.splitlines()[1])
_args.add_argument("--groen", type=Path,
                   default=config.FME_INPUT_DIR / "groenvoorzieningen" / "groenkaart.gdb",
                   help="groenkaart.gdb, the green input of the 300 workbench")
ARGS = _args.parse_args()
OUT = Path(__file__).resolve().parent / "data" / "groen.pmtiles"
ZOOMS = {"groen": (11, 14)}
# The workbench's selection (FeatureReader WHERE + Tester); field names quoted,
# OGR SQL does not accept a bare name starting with "_"
WHERE = ("\"bron\" <> 'top 10 water' AND \"_area\" >= 300 "
         "AND \"Shape_Length\" <= 0.35 * \"Shape_Area\"")

# RD New (the Dutch grid, metres), with x = easting first
RD = osr.SpatialReference()
RD.ImportFromEPSG(28992)
RD.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)


def groen(dst):
    """Copy the selected green polygons (2D) with type, source and area."""
    gdb = ogr.Open(str(ARGS.groen))           # keep a reference: the layer dies with it
    src = gdb.GetLayerByName("groen_uit_osm_top10_2024")
    src.SetAttributeFilter(WHERE)
    out = dst.CreateLayer("groen", RD, ogr.wkbMultiPolygon)
    for fname in ("type", "bron"):
        out.CreateField(ogr.FieldDefn(fname, ogr.OFTString))
    out.CreateField(ogr.FieldDefn("m2", ogr.OFTInteger64))
    dst.StartTransaction()
    for f in src:
        nf = ogr.Feature(out.GetLayerDefn())
        nf.SetField("type", f.GetField("type"))
        nf.SetField("bron", f.GetField("bron"))
        nf.SetField("m2", round(f.GetField("_area")))
        g = f.GetGeometryRef().Clone()
        g.FlattenTo2D()
        nf.SetGeometry(g)
        out.CreateFeature(nf)
    dst.CommitTransaction()
    print(f"groen: {out.GetFeatureCount():,} parks and woods")


def main():
    """Stage the layer in a temporary GeoPackage, then write it as PMTiles."""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    staging = OUT.with_suffix(".staging.gpkg")
    staging.unlink(missing_ok=True)
    dst = ogr.GetDriverByName("GPKG").CreateDataSource(str(staging))
    groen(dst)
    dst = None

    # Vector tiles in web Mercator; written to .partial, then renamed
    conf = {layer: {"minzoom": z0, "maxzoom": z1} for layer, (z0, z1) in ZOOMS.items()}
    tmp = OUT.with_suffix(".partial.pmtiles")
    tmp.unlink(missing_ok=True)
    print("Writing vector tiles …")
    gdal.VectorTranslate(str(tmp), str(staging), format="PMTiles", dstSRS="EPSG:3857",
                         layers=list(ZOOMS),
                         datasetCreationOptions=[f"MINZOOM={min(z for z, _ in ZOOMS.values())}",
                                                 f"MAXZOOM={max(z for _, z in ZOOMS.values())}",
                                                 f"CONF={json.dumps(conf)}",
                                                 f"NAME=3-30-300 {config.PROVINCE}: groen van de 300"],
                         callback=gdal.TermProgress_nocb)
    tmp.replace(OUT)
    staging.unlink()
    size = OUT.stat().st_size / 1e6
    print(f"Wrote {OUT} ({size:.0f} MB)" + ("  — over GitHub's 100 MB file limit!" if size > 100 else ""))


if __name__ == "__main__":
    main()
