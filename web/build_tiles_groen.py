"""
build_tiles_groen.py — Build the web map's tiles of the green the 300 walks to
(web/data/groen.pmtiles): the green areas themselves, shown on the 300 tab.
A file of its own so 30-300.pmtiles stays under GitHub's 100 MB limit.

Layer:
  groen   zoom 11-14 (the map over-zooms beyond): soorten, bronnen, namen and
          m2 per green area

Input:
  data/interim/300/<Province>/prepared.gpkg, layer parks
          (indicator_300_park/etl/01_prepare.py): the green areas of the 300,
          from the provincial voorzieningen map and TOP10NL, without open
          water, at least 300 m2 and no narrow strips, merged where they
          overlap or touch.

Usage:
    python web/build_tiles_groen.py [--prepared GPKG]
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
_args.add_argument("--prepared", type=Path,
                   default=config.INTERIM_DIR / "300" / config.PROVINCE_SLUG / "prepared.gpkg",
                   help="prepared.gpkg of the 300 (indicator_300_park/etl/01_prepare.py)")
ARGS = _args.parse_args()
OUT = Path(__file__).resolve().parent / "data" / "groen.pmtiles"
ZOOMS = {"groen": (11, 14)}

# RD New (the Dutch grid, metres), with x = easting first
RD = osr.SpatialReference()
RD.ImportFromEPSG(28992)
RD.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)


def groen(dst):
    """Copy the green areas with their kinds, sources, names and area."""
    src_ds = ogr.Open(str(ARGS.prepared))          # keep a reference: the layer dies with it
    src = src_ds.GetLayerByName("parks")
    out = dst.CreateLayer("groen", RD, ogr.wkbMultiPolygon)
    for fname in ("soorten", "bronnen", "namen"):
        out.CreateField(ogr.FieldDefn(fname, ogr.OFTString))
    out.CreateField(ogr.FieldDefn("m2", ogr.OFTInteger64))
    dst.StartTransaction()
    for f in src:
        nf = ogr.Feature(out.GetLayerDefn())
        for fname in ("soorten", "bronnen", "namen", "m2"):
            nf.SetField(fname, f.GetField(fname))
        nf.SetGeometry(f.GetGeometryRef().Clone())
        out.CreateFeature(nf)
    dst.CommitTransaction()
    print(f"groen: {out.GetFeatureCount():,} green areas")


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
