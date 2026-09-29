r"""
benchmark_corrected.py — Rerun the ArcGIS benchmark for Delft
(visibility_Delft) with the observer height it was meant to have.

The original run gave RASTERVALU (the bilinear AHN5 DSM value at the tree
point) as BOTH observer_elevation and observer_offset, so every observer
sat at 2 x RASTERVALU m NAP (arcgis_tests/one_tree.py; WORKLOG section 9;
ARCHITECTURE.md section 14). This run changes only that:

  observer_elevation  RASTERVALU     (unchanged)
  observer_offset     1 m            (was RASTERVALU; given explicitly —
                                      left empty, Visibility adds nothing)
  everything else as the original: Visibility, AHN5ruw05m_Delft (raw DSM,
  NoData not filled), bomen_Delft_met_hoogte_uit_AHN05ruw, FREQUENCY,
  non-visible = 0, surface_offset 1.8 m, outer radius 30 m (the tool
  treats it as 2D whatever the sign), flat earth.
Trees without a RASTERVALU (349, on NoData/water) are left out.

The old GRID engine behind Visibility dies on paths like
D:\Repositories\3-regel (digit + hyphen), so the inputs are copied to a
plain work folder first and the result is copied back afterwards.

Output:
  WORK\visibility_Delft_corrected.tif  (or ..._test.tif for the test area)
  data\processed\experiments\<same name>.tif  — picked up by the QGIS
      validation project (qgis_validation\build_project.py)
Compare with the old benchmark and the pipeline afterwards (OSGeo4W Python):
  python arcgis_tests\compare_benchmark.py

HOW TO RUN — ArcGIS Pro Python Command Prompt (Start menu → ArcGIS →
Python Command Prompt):
  1. the small test area first (~1-2 minutes):
       python D:\Repositories\3-regel\arcgis_tests\benchmark_corrected.py test
  2. then all of Delft (~87,000 trees; this takes a while):
       python D:\Repositories\3-regel\arcgis_tests\benchmark_corrected.py
"""

import glob
import os
import shutil
import sys
import time

import arcpy
from arcpy.sa import Visibility

SOURCE_GDB = r"R:\ESRI\DATA\RUIMTELIJKE ONTWIKKELING\PERSOONLIJK\Chris\test\data.gdb"
DEM = os.path.join(SOURCE_GDB, "AHN5ruw05m_Delft")
TREES = os.path.join(SOURCE_GDB, "bomen_Delft_met_hoogte_uit_AHN05ruw")
WORK = r"D:\Temp\benchmark_corrected"
EXPERIMENTS_DIR = r"D:\Repositories\3-regel\data\processed\experiments"
OUT_NAME = "visibility_Delft_corrected"

# The "stukje" test area in Delft (as arcgis_tests/visibility_variants.py), + 30 m margin
TEST_AREA = (82982.0, 445985.5, 83510.5, 446451.0)
MARGIN = 30.0

OBSERVER_OFFSET = "1"          # metres; the fix
SURFACE_OFFSET = "1.8"
OUTER_RADIUS = "30"


def copy_files(stem, src, dst):
    for f in glob.glob(os.path.join(src, stem + ".*")):
        shutil.copy2(f, dst)


def main(test):
    name = OUT_NAME + ("_test" if test else "")
    arcpy.CheckOutExtension("Spatial")
    arcpy.env.overwriteOutput = True
    os.makedirs(WORK, exist_ok=True)
    # Plain .tif / .shp files in a plain folder, as in one_tree_arcgis.py:
    # the GRID engine failed on this run with its inputs in a file geodatabase
    arcpy.env.workspace = arcpy.env.scratchWorkspace = WORK

    t0 = time.time()
    dem = os.path.join(WORK, "dem_test.tif" if test else "dem.tif")
    trees = os.path.join(WORK, "trees_test.shp" if test else "trees.shp")
    if test:
        x0, y0, x1, y1 = (TEST_AREA[0] - MARGIN, TEST_AREA[1] - MARGIN,
                          TEST_AREA[2] + MARGIN, TEST_AREA[3] + MARGIN)
        arcpy.env.extent = arcpy.Extent(x0, y0, x1, y1)
        arcpy.management.Clip(DEM, f"{x0} {y0} {x1} {y1}", dem, nodata_value="",
                              clipping_geometry="NONE", maintain_clipping_extent="MAINTAIN_EXTENT")
    elif not arcpy.Exists(dem):
        print("Copying the DEM to the work folder ...", flush=True)
        arcpy.management.CopyRaster(DEM, dem)
    arcpy.analysis.Select(TREES, trees, "RASTERVALU IS NOT NULL")
    arcpy.env.snapRaster = arcpy.env.cellSize = dem
    arcpy.env.extent = dem
    n = int(arcpy.management.GetCount(trees)[0])
    print(f"{n:,} trees with a RASTERVALU; inputs ready in {time.time() - t0:.0f}s", flush=True)

    print(f"Visibility: observer_elevation RASTERVALU, observer_offset {OBSERVER_OFFSET} m, "
          f"surface_offset {SURFACE_OFFSET} m, outer radius {OUTER_RADIUS} m ...", flush=True)
    t1 = time.time()
    result = Visibility(dem, trees, analysis_type="FREQUENCY", nonvisible_cell_value="ZERO",
                        z_factor=1, curvature_correction="FLAT_EARTH", refractivity_coefficient=0.13,
                        surface_offset=SURFACE_OFFSET, observer_elevation="RASTERVALU",
                        observer_offset=OBSERVER_OFFSET, outer_radius=OUTER_RADIUS)
    out = os.path.join(WORK, f"{name}.tif")
    result.save(out)
    print(f"Saved {out} in {(time.time() - t1) / 60:.1f} min", flush=True)

    os.makedirs(EXPERIMENTS_DIR, exist_ok=True)
    copy_files(name, WORK, EXPERIMENTS_DIR)
    print(f"Copied to {EXPERIMENTS_DIR}\\{name}.tif")
    print("Next, in OSGeo4W Python:  python arcgis_tests\\compare_benchmark.py")


main(test=len(sys.argv) > 1 and sys.argv[1] == "test")
