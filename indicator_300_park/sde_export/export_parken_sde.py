r"""
export_parken_sde.py — Copy the green of the new 300 from the provincial SDE to the project folder.

Two sources, both from the production geodatabase "Geo":

  Recreatiegebieden uit het ontwerp-omgevingsbeleid
      Geo.OMGEVINGSBELEID_ONTWERP.Recreatiegebied (connection omgevingsbeleid_ontwerp)
  TOP10NL functioneel gebied, the green kinds only
      Geo.TOPOGRAFIE.PDOK_TOP10NL_FUNCTIONEEL_GEBIED_VLAK_PZH (connection topografie)
      with typefunctioneelgebied IN ('botanische tuin', 'heemtuin', 'landgoed',
      'nationaal park', 'natuurgebied', 'recreatiegebied', 'park')

Writes data/raw/groen_300/groen_300.gdb (replaced on every run):

  recreatiegebied_ontwerp   the first source, all fields
  top10_functioneel_groen   the second source, all fields, only the kinds above
  parken_300                both together, one polygon per source feature, with
                            bron ("omgevingsbeleid ontwerp" / "top10nl"), type
                            (the TOP10 kind, or "recreatiegebied") and naam (when
                            the source has a name field). This is the layer the
                            300 reads (indicator_300_park/etl/config.py,
                            PARKS_PATH and PARKS_LAYER).

Overlapping polygons from the two sources are kept as they are; the 300
finds entrances per polygon.

Run with ArcGIS Pro closed, from the ArcGIS Pro Python Command Prompt:
    python D:\Repositories\330300regel\indicator_300_park\sde_export\export_parken_sde.py
or with the full path to its Python:
    "C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe" indicator_300_park\sde_export\export_parken_sde.py
"""
import os
import time

import arcpy

CONNECTIONS = r"R:\ESRI\BEHEER\Database_verbindingen\Geodatabase\Productie\Geo"
RECREATIE = (os.path.join(CONNECTIONS, "Geodatabase@Geo@omgevingsbeleid_ontwerp.sde"),
             "Geo.OMGEVINGSBELEID_ONTWERP.Recreatiegebied")
TOP10 = (os.path.join(CONNECTIONS, "Geodatabase@Geo@topografie.sde"),
         "Geo.TOPOGRAFIE.PDOK_TOP10NL_FUNCTIONEEL_GEBIED_VLAK_PZH")
TOP10_TYPE_FIELD = "typefunctioneelgebied"
TOP10_TYPES = ("botanische tuin", "heemtuin", "landgoed", "nationaal park",
               "natuurgebied", "recreatiegebied", "park")
NAME_FIELDS = ("naam", "naamnl", "naamofficieel", "naamfries", "name")   # first one found is copied

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "raw", "groen_300")
OUT_GDB = os.path.normpath(os.path.join(OUT_DIR, "groen_300.gdb"))


def source(conn, name):
    """Full path of an SDE feature class; stops with the available names when it is missing."""
    if not os.path.exists(conn):
        raise SystemExit(f"Connection file not found: {conn}")
    path = os.path.join(conn, name)
    if not arcpy.Exists(path):
        arcpy.env.workspace = conn
        near = [fc for fc in arcpy.ListFeatureClasses() or [] if name.split(".")[-1].lower() in fc.lower()]
        raise SystemExit(f"Not found: {name} in {conn}\nSimilar names: {near or 'none'}")
    return path


def export(src, out, where=None):
    """Copy a feature class (ExportFeatures from ArcGIS Pro 3.0, else FeatureClassToFeatureClass)."""
    if hasattr(arcpy.conversion, "ExportFeatures"):
        arcpy.conversion.ExportFeatures(src, out, where_clause=where or "")
    else:
        arcpy.conversion.FeatureClassToFeatureClass(src, os.path.dirname(out), os.path.basename(out), where or "")


def field(fc, wanted):
    """The real name of a field, matched without case; None when absent."""
    for f in arcpy.ListFields(fc):
        if f.name.lower() == wanted.lower():
            return f.name
    return None


def name_field(fc):
    for wanted in NAME_FIELDS:
        found = field(fc, wanted)
        if found:
            return found
    return None


def main():
    t0 = time.time()
    arcpy.env.overwriteOutput = True
    recreatie = source(*RECREATIE)
    top10 = source(*TOP10)

    os.makedirs(OUT_DIR, exist_ok=True)
    if arcpy.Exists(OUT_GDB):
        arcpy.management.Delete(OUT_GDB)
    arcpy.management.CreateFileGDB(OUT_DIR, "groen_300.gdb")
    print(f"Output: {OUT_GDB}")

    out_recreatie = os.path.join(OUT_GDB, "recreatiegebied_ontwerp")
    export(recreatie, out_recreatie)
    print(f"recreatiegebied_ontwerp: {int(arcpy.management.GetCount(out_recreatie)[0]):,} polygons")

    type_field = field(top10, TOP10_TYPE_FIELD)
    if type_field is None:
        raise SystemExit(f"Field {TOP10_TYPE_FIELD} not in {TOP10[1]}: "
                         f"{[f.name for f in arcpy.ListFields(top10)]}")
    where = (f"{arcpy.AddFieldDelimiters(top10, type_field)} IN ("
             + ", ".join(f"'{t}'" for t in TOP10_TYPES) + ")")
    out_top10 = os.path.join(OUT_GDB, "top10_functioneel_groen")
    export(top10, out_top10, where)
    counts = {}
    with arcpy.da.SearchCursor(out_top10, [type_field]) as rows:
        for (t,) in rows:
            counts[t] = counts.get(t, 0) + 1
    print(f"top10_functioneel_groen: {sum(counts.values()):,} polygons "
          + ", ".join(f"{k} {v:,}" for k, v in sorted(counts.items())))
    missing = [t for t in TOP10_TYPES if t not in counts]
    if missing:
        print(f"  not found in TOP10 (check the spelling): {missing}")

    sr = arcpy.Describe(out_top10).spatialReference
    merged = os.path.join(OUT_GDB, "parken_300")
    arcpy.management.CreateFeatureclass(OUT_GDB, "parken_300", "POLYGON", spatial_reference=sr)
    for name, length in (("bron", 40), ("type", 60), ("naam", 200)):
        arcpy.management.AddField(merged, name, "TEXT", field_length=length)
    n = 0
    with arcpy.da.InsertCursor(merged, ["SHAPE@", "bron", "type", "naam"]) as out:
        for fc, bron, kind_field in ((out_recreatie, "omgevingsbeleid ontwerp", None),
                                     (out_top10, "top10nl", type_field)):
            nf = name_field(fc)
            cols = ["SHAPE@"] + [kind_field or "OID@"] + [nf or "OID@"]
            with arcpy.da.SearchCursor(fc, cols, spatial_reference=sr) as rows:
                for shape, kind, naam in rows:
                    if shape is None:
                        continue
                    out.insertRow([shape, bron, kind if kind_field else "recreatiegebied",
                                   naam if nf else None])
                    n += 1
    print(f"parken_300: {n:,} polygons ({sr.name})")
    print(f"Done in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
