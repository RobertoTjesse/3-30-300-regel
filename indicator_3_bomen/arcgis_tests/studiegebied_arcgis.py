r"""
studiegebied_arcgis.py — ArcGIS half of arcgis_tests/studiegebied.py:
Visibility on the study area (a Delft buurt + 30 m buffer) with exactly
the settings of the colleague's benchmark visibility_Delft, so that the
result inside the buurt is identical to the benchmark.

  Visibility(dem_raw, bomen, FREQUENCY, non-visible 0, z-factor 1, flat earth,
             surface_offset 1.8, observer_elevation RASTERVALU,
             observer_offset RASTERVALU, outer_radius 30)
  (RASTERVALU for both the elevation and the offset: the observer sat at
   2 x RASTERVALU m NAP — the setting that reproduces visibility_Delft on
   100% of cells, WORKLOG section 9.)

Inputs (made by `python indicator_3_bomen/arcgis_tests/studiegebied.py prepare`), in
data\studiegebied\<buurtcode>\ (the one named in current.json):
  dem_raw.tif   the benchmark DSM cut out, NoData not filled
  trees.json    the ids of the trees of buurt + buffer
The trees themselves are copied straight from the benchmark's tree layer
(bomen_Delft_met_hoogte_uit_AHN05ruw) into a work geodatabase, so that
empty RASTERVALU values stay NULL exactly as in the benchmark run (a
shapefile turns them into 0, which Visibility treats differently).
Output: arcgis.tif in the same folder.

The old GRID engine behind Visibility fails on paths like
D:\Repositories\3-regel (digit + hyphen), so the inputs are copied to a
plain work folder (WORK) and the result is copied back.

HOW TO RUN — ArcGIS Pro Python Command Prompt (Start menu -> ArcGIS ->
Python Command Prompt; it opens in Pro's own folder, so give the full path):
    python D:\Repositories\330300regel\indicator_3_bomen\arcgis_tests\studiegebied_arcgis.py
Takes about a minute. Then, in OSGeo4W Python:
    python indicator_3_bomen/arcgis_tests/studiegebied.py gdal
    python indicator_3_bomen/arcgis_tests/studiegebied.py compare
"""

import glob
import json
import os
import shutil
import time

import arcpy
from arcpy.sa import Visibility

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # repo root
ROOT = os.path.join(REPO, "data", "studiegebied")
WORK = r"D:\Temp\studiegebied"      # plain path for the old GRID engine
# The benchmark's own tree layer
BENCH_TREES = (r"R:\ESRI\DATA\RUIMTELIJKE ONTWIKKELING\PERSOONLIJK\Chris\test\data.gdb"
               r"\bomen_Delft_met_hoogte_uit_AHN05ruw")


def copy_files(stem, src, dst):
    """Copy stem.* (a raster or shapefile with its side files) from src to dst."""
    for f in glob.glob(os.path.join(src, stem + ".*")):
        shutil.copy2(f, dst)


def main():
    """Run Visibility with the benchmark's settings on the current study area."""
    # The study area that studiegebied.py prepared last; copy its inputs to
    # the plain work folder
    with open(os.path.join(ROOT, "current.json"), encoding="utf-8") as fh:
        area = json.load(fh)
    folder = os.path.join(ROOT, area["buurtcode"])
    for name in ("dem_raw.tif", "trees.json"):
        if not os.path.exists(os.path.join(folder, name)):
            raise SystemExit(f"Not found: {folder}\\{name} - run `python indicator_3_bomen/arcgis_tests/studiegebied.py prepare` first")
    work = os.path.join(WORK, area["buurtcode"])
    os.makedirs(work, exist_ok=True)
    copy_files("dem_raw", folder, work)

    arcpy.CheckOutExtension("Spatial")
    arcpy.env.overwriteOutput = True
    arcpy.env.workspace = arcpy.env.scratchWorkspace = work
    dem = os.path.join(work, "dem_raw.tif")

    # The study area's trees, by id, from the benchmark's own layer into a
    # work geodatabase (keeps NULL RASTERVALU as NULL)
    with open(os.path.join(folder, "trees.json"), encoding="utf-8") as fh:
        fids = [t["fid"] for t in json.load(fh)]
    gdb = os.path.join(work, "bomen.gdb")
    if not arcpy.Exists(gdb):
        arcpy.management.CreateFileGDB(work, "bomen.gdb")
    trees = os.path.join(gdb, "bomen")
    oid = arcpy.Describe(BENCH_TREES).OIDFieldName
    arcpy.analysis.Select(BENCH_TREES, trees, f"{oid} IN ({','.join(map(str, fids))})")
    n = int(arcpy.management.GetCount(trees)[0])
    n_null = sum(1 for (v,) in arcpy.da.SearchCursor(trees, ["RASTERVALU"]) if v is None)
    if n != len(fids):
        raise SystemExit(f"Selected {n} trees, expected {len(fids)} — tree ids do not match")

    arcpy.env.snapRaster = arcpy.env.extent = arcpy.env.cellSize = dem
    print(f"{area['naam']} ({area['buurtcode']}): Visibility for {n} trees ({n_null} with RASTERVALU NULL), "
          "benchmark settings ...", flush=True)

    # The benchmark's settings, including RASTERVALU as both observer
    # elevation and offset (see the module docstring)
    t0 = time.time()
    result = Visibility(dem, trees, analysis_type="FREQUENCY", nonvisible_cell_value="ZERO", z_factor=1,
                        curvature_correction="FLAT_EARTH", refractivity_coefficient=0.13,
                        surface_offset="1.8", observer_elevation="RASTERVALU",
                        observer_offset="RASTERVALU", outer_radius="30")
    result.save(os.path.join(work, "arcgis.tif"))
    copy_files("arcgis", work, folder)
    print(f"Saved {folder}\\arcgis.tif in {time.time() - t0:.0f} s", flush=True)
    print("Next, in OSGeo4W Python:  python indicator_3_bomen/arcgis_tests/studiegebied.py gdal   and   ... compare")


main()
