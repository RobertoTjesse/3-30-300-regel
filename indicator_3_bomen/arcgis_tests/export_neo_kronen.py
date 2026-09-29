r"""
export_neo_kronen.py — Export NEO's crown polygons (BODEM.BOMEN_KRONEN, with
their height, crown diameter and how/when these were measured) for one
municipality, to compare NEO's tree heights with this pipeline's canopy tops.

Our tree points (province_trees.gpkg) have no height; the 3 takes each tree's
height from the surface model. BOMEN_KRONEN, the table the 30 reads its crown
areas from, does have per-crown fields height, height_dataset, height_date,
height_method, height_quality and crown_diameter.

What it does:
  1. Finds the BOMEN_KRONEN feature class through the Bodem SDE connection.
  2. Selects every crown that intersects the municipality (its boundary from
     data\interim\gemeenten.gpkg) and copies them, with all fields, to
     D:\Temp\neo_kronen\kronen.gdb\kronen_<municipality>.
  3. Prints the fields and a short summary of the height values, so it is
     clear whether "height" is metres above ground or NAP.

HOW TO RUN — ArcGIS Pro Python Command Prompt (Pro itself closed; the prompt
opens in Pro's own folder, so give the full path):
    python D:\Repositories\330300regel\indicator_3_bomen\arcgis_tests\export_neo_kronen.py
    python D:\Repositories\330300regel\indicator_3_bomen\arcgis_tests\export_neo_kronen.py Leiden
Default municipality: Delft. The result is read afterwards with GDAL (OSGeo4W
Python), so nothing needs to be copied back.
"""

import os
import sys
import time

import arcpy

SDE = r"R:\ESRI\BEHEER\Database_verbindingen\Geodatabase\Productie\Geo\Geodatabase@Geo@bodem.sde"
TABLE = "BOMEN_KRONEN"                  # BODEM.BOMEN_KRONEN, as in the 30's workbench
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # repo root
MUNICIPALITIES = os.path.join(REPO, "data", "interim", "gemeenten.gpkg")
WORK = r"D:\Temp\neo_kronen"
GDB = os.path.join(WORK, "kronen.gdb")


def find_crowns():
    """The full path of the BOMEN_KRONEN feature class in the SDE database
    (its name there carries the database and schema, e.g. Geo.BODEM.BOMEN_KRONEN)."""
    arcpy.env.workspace = SDE
    names = [n for n in (arcpy.ListFeatureClasses(f"*{TABLE}") or []) if n.upper().endswith(TABLE)]
    for ds in arcpy.ListDatasets(feature_type="Feature") or []:   # also inside feature datasets
        names += [os.path.join(ds, n) for n in (arcpy.ListFeatureClasses(f"*{TABLE}", feature_dataset=ds) or [])
                  if n.upper().endswith(TABLE)]
    if not names:
        raise SystemExit(f"No feature class *{TABLE} found in {SDE}")
    if len(names) > 1:
        print(f"Several matches, using the first: {names}")
    return os.path.join(SDE, names[0])


def municipality_layer(name):
    """A layer with only this municipality's boundary polygon."""
    layer = arcpy.management.MakeFeatureLayer(
        os.path.join(MUNICIPALITIES, "main.gemeenten"), "gemeente", f"naam = '{name}'")[0]
    if int(arcpy.management.GetCount(layer)[0]) != 1:
        raise SystemExit(f"Municipality '{name}' not found in {MUNICIPALITIES}")
    return layer


def summarise(fc):
    """Print the fields and the spread of the height values."""
    fields = [f.name for f in arcpy.ListFields(fc)]
    print("\nFields:", ", ".join(fields))
    wanted = [f for f in ("height", "height_dataset", "height_date", "height_method", "height_quality",
                          "crown_diameter", "crown_area") if f in [x.lower() for x in fields]]
    # Field names in the database may be upper case: map to the real spelling
    real = {f.lower(): f for f in fields}
    cols = [real[w] for w in wanted]
    heights, no_height, texts = [], 0, {w: {} for w in ("height_dataset", "height_method", "height_date")}
    with arcpy.da.SearchCursor(fc, cols) as cur:
        for row in cur:
            values = dict(zip(wanted, row))
            h = values.get("height")
            if h is None:
                no_height += 1
            else:
                heights.append(float(h))
            for w in texts:
                if w in values:
                    key = str(values[w])[:30]
                    texts[w][key] = texts[w].get(key, 0) + 1
    print(f"\n{len(heights) + no_height:,} crowns, {no_height:,} without a height")
    if heights:
        heights.sort()
        pick = lambda p: heights[min(len(heights) - 1, int(p * len(heights)))]
        print(f"height: min {heights[0]:.2f}  p10 {pick(.1):.2f}  median {pick(.5):.2f}  "
              f"p90 {pick(.9):.2f}  max {heights[-1]:.2f}")
        print("  (a median of ~8 m and a minimum near 0 means height above ground;"
              " values around the local NAP level, e.g. negative ones, would mean NAP)")
    for w, counts in texts.items():
        if counts:
            top = sorted(counts.items(), key=lambda kv: -kv[1])[:5]
            print(f"{w}: " + ", ".join(f"{k} ({v:,})" for k, v in top))


def main():
    """Export one municipality's crowns and summarise their heights."""
    name = sys.argv[1] if len(sys.argv) > 1 else "Delft"
    arcpy.env.overwriteOutput = True
    os.makedirs(WORK, exist_ok=True)
    if not arcpy.Exists(GDB):
        arcpy.management.CreateFileGDB(WORK, os.path.basename(GDB))

    t0 = time.time()
    crowns = find_crowns()
    print(f"Crowns: {crowns}")
    # Every crown touching the municipality (not clipped: whole crowns, as in the 30)
    layer = arcpy.management.MakeFeatureLayer(crowns, "kronen")[0]
    arcpy.management.SelectLayerByLocation(layer, "INTERSECT", municipality_layer(name))
    out = os.path.join(GDB, "kronen_" + "".join(c if c.isalnum() else "_" for c in name))
    arcpy.management.CopyFeatures(layer, out)
    print(f"Copied {int(arcpy.management.GetCount(out)[0]):,} crowns of {name} to {out} "
          f"in {time.time() - t0:.0f} s")
    summarise(out)
    print("\nNext: compare these heights with the canopy tops (OSGeo4W Python, GDAL).")


main()
