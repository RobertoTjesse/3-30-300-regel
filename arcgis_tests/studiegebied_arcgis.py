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

Inputs (made by `python arcgis_tests/studiegebied.py prepare`), in
data\studiegebied\<buurtcode>\ (the one named in current.json):
  dem_raw.tif   the benchmark DSM cut out, NoData not filled
  bomen.shp     the trees of buurt + buffer, with RASTERVALU
Output: arcgis.tif in the same folder.

The old GRID engine behind Visibility fails on paths like
D:\Repositories\3-regel (digit + hyphen), so the inputs are copied to a
plain work folder (WORK) and the result is copied back.

HOW TO RUN — ArcGIS Pro Python Command Prompt (Start menu -> ArcGIS ->
Python Command Prompt), from the repository folder:
    python arcgis_tests\studiegebied_arcgis.py
Takes about a minute. Then, in OSGeo4W Python:
    python arcgis_tests\studiegebied.py gdal
    python arcgis_tests\studiegebied.py compare
"""

import glob
import json
import os
import shutil
import time

import arcpy
from arcpy.sa import Visibility

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.join(REPO, "data", "studiegebied")
WORK = r"D:\Temp\studiegebied"      # plain path for the old GRID engine


def copy_files(stem, src, dst):
    for f in glob.glob(os.path.join(src, stem + ".*")):
        shutil.copy2(f, dst)


def main():
    with open(os.path.join(ROOT, "current.json"), encoding="utf-8") as fh:
        area = json.load(fh)
    folder = os.path.join(ROOT, area["buurtcode"])
    for stem in ("dem_raw", "bomen"):
        if not glob.glob(os.path.join(folder, stem + ".*")):
            raise SystemExit(f"Not found: {folder}\\{stem} - run `python arcgis_tests/studiegebied.py prepare` first")
    work = os.path.join(WORK, area["buurtcode"])
    os.makedirs(work, exist_ok=True)
    for stem in ("dem_raw", "bomen"):
        copy_files(stem, folder, work)

    arcpy.CheckOutExtension("Spatial")
    arcpy.env.overwriteOutput = True
    arcpy.env.workspace = arcpy.env.scratchWorkspace = work
    dem = os.path.join(work, "dem_raw.tif")
    trees = os.path.join(work, "bomen.shp")
    arcpy.env.snapRaster = arcpy.env.extent = arcpy.env.cellSize = dem
    n = int(arcpy.management.GetCount(trees)[0])
    print(f"{area['naam']} ({area['buurtcode']}): Visibility for {n} trees, benchmark settings ...", flush=True)

    t0 = time.time()
    result = Visibility(dem, trees, analysis_type="FREQUENCY", nonvisible_cell_value="ZERO", z_factor=1,
                        curvature_correction="FLAT_EARTH", refractivity_coefficient=0.13,
                        surface_offset="1.8", observer_elevation="RASTERVALU",
                        observer_offset="RASTERVALU", outer_radius="30")
    result.save(os.path.join(work, "arcgis.tif"))
    copy_files("arcgis", work, folder)
    print(f"Saved {folder}\\arcgis.tif in {time.time() - t0:.0f} s", flush=True)
    print("Next, in OSGeo4W Python:  python arcgis_tests\\studiegebied.py gdal   and   ... compare")


main()
