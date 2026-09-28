r"""
one_tree_arcgis.py — ArcGIS Pro half of the one-tree comparison
(arcgis_tests/one_tree.py): the viewshed of one tree in Delft with
Viewshed2, the classic Viewshed tool and Visibility, on exactly the
inputs GDAL got.

Inputs (made by `python arcgis_tests/one_tree.py prepare`):
  arcgis_tests\one_tree\dem.tif    AHN5 raw DSM, 0.5 m, NoData already filled
  arcgis_tests\one_tree\tree.shp   one point, OBS_Z = observer elevation (m NAP);
                                   SPOT/OFFSETA/OFFSETB/RADIUS2 for the classic
                                   Viewshed tool (RADIUS2 -30 = 2D)
  arcgis_tests\one_tree\tree_3d.shp  the same with RADIUS2 = 30 (3D)
Settings, identical for every tool: observer at OBS_Z with offset 0, target
1.8 m above the surface, 30 m radius, flat earth.

Outputs (arcgis_tests\one_tree\, same grid as dem.tif):
  arc_v2_2d.tif        Viewshed2, 30 m measured on the ground (2D)
  arc_v2_3d.tif        Viewshed2, 30 m as 3D line-of-sight distance
  arc_vs_2d.tif        classic Viewshed, RADIUS2 = -30 (2D)
  arc_vs_3d.tif        classic Viewshed, RADIUS2 = 30 (3D)
  arc_vis_2d.tif       Visibility, outer radius -30 (negative = 2D)
  arc_vis_3d.tif       Visibility, outer radius 30 (positive = 3D, as the
                       reference visibility_Delft run)
Order: Viewshed2, classic Viewshed, Visibility; every result is saved at
once. The Visibility tool has crashed ArcGIS Pro 3.6.1 on this machine (the
classic Viewshed tool is from the same wavefront family and may too); if one
does, the results before it are already on disk. Set RUN to skip variants.

HOW TO RUN — from a command prompt (preferred; ArcGIS Pro may stay closed):
    "C:\Program Files\ArcGIS\Pro\bin\Python\scripts\propy.bat" D:\Repositories\3-regel\arcgis_tests\one_tree_arcgis.py
  Every variant then runs in its own child process: a tool that crashes
  only loses its own result, and the next variant still runs. Variants
  whose .tif already exists are skipped (delete the .tif to redo one;
  this also holds in the Python window).
Or in ArcGIS Pro → Analysis → Python window (all in one process — a crash
takes Pro down):
    exec(open(r"D:\Repositories\3-regel\arcgis_tests\one_tree_arcgis.py", encoding="utf-8").read())
Then, in OSGeo4W Python:  python arcgis_tests\one_tree.py compare
"""

import os
import subprocess
import sys

import arcpy
from arcpy.sa import Viewshed, Viewshed2, Visibility

DIR = r"D:\Repositories\3-regel\arcgis_tests\one_tree"
RUN = ["arc_v2_2d", "arc_v2_3d", "arc_vs_2d", "arc_vs_3d", "arc_vis_2d", "arc_vis_3d"]  # remove names to skip

DEM = os.path.join(DIR, "dem.tif")
TREE = os.path.join(DIR, "tree.shp")
TREE_3D = os.path.join(DIR, "tree_3d.shp")
VARIANTS = [
    ("arc_v2_2d", lambda: Viewshed2(
        DEM, TREE, analysis_type="FREQUENCY", refractivity_coefficient=0.13,
        surface_offset="1.8 Meters", observer_elevation="OBS_Z", observer_offset="0 Meters",
        outer_radius="30 Meters", outer_radius_is_3d="GROUND",
        analysis_method="ALL_SIGHTLINES", analysis_target_device="CPU_ONLY")),
    ("arc_v2_3d", lambda: Viewshed2(
        DEM, TREE, analysis_type="FREQUENCY", refractivity_coefficient=0.13,
        surface_offset="1.8 Meters", observer_elevation="OBS_Z", observer_offset="0 Meters",
        outer_radius="30 Meters", outer_radius_is_3d="3D",
        analysis_method="ALL_SIGHTLINES", analysis_target_device="CPU_ONLY")),
    # classic Viewshed: observer settings from the SPOT/OFFSETA/OFFSETB/RADIUS2 fields
    ("arc_vs_2d", lambda: Viewshed(DEM, TREE, z_factor=1, curvature_correction="FLAT_EARTH",
                                   refractivity_coefficient=0.13)),
    ("arc_vs_3d", lambda: Viewshed(DEM, TREE_3D, z_factor=1, curvature_correction="FLAT_EARTH",
                                   refractivity_coefficient=0.13)),
    ("arc_vis_2d", lambda: Visibility(
        DEM, TREE, analysis_type="FREQUENCY", nonvisible_cell_value="ZERO", z_factor=1,
        curvature_correction="FLAT_EARTH", refractivity_coefficient=0.13,
        surface_offset="1.8", observer_elevation="OBS_Z", observer_offset="0", outer_radius="-30")),
    ("arc_vis_3d", lambda: Visibility(
        DEM, TREE, analysis_type="FREQUENCY", nonvisible_cell_value="ZERO", z_factor=1,
        curvature_correction="FLAT_EARTH", refractivity_coefficient=0.13,
        surface_offset="1.8", observer_elevation="OBS_Z", observer_offset="0", outer_radius="30")),
]


def main(only=None):
    """Run the variants in RUN in this process (or just `only`)."""
    for f in (DEM, TREE):
        if not arcpy.Exists(f):
            raise SystemExit(f"Not found: {f} - run `python arcgis_tests/one_tree.py prepare` first")
    arcpy.CheckOutExtension("Spatial")
    arcpy.env.overwriteOutput = True
    arcpy.env.snapRaster = DEM
    arcpy.env.extent = DEM
    arcpy.env.cellSize = DEM
    for name, run in VARIANTS:
        if name not in (RUN if only is None else [only]):
            continue
        if only is None and os.path.exists(os.path.join(DIR, f"{name}.tif")):
            print(f"{name}: already there, skipped")
            continue
        print(f"{name} ...", flush=True)
        try:
            run().save(os.path.join(DIR, f"{name}.tif"))
            print(f"  saved {name}.tif", flush=True)
        except Exception as exc:        # a normal tool error: report and continue
            print(f"  FAILED: {exc}", flush=True)
            if only is not None:
                raise SystemExit(1)


def main_isolated():
    """Command line: one child process per variant, so a crash loses only that variant."""
    for name, _ in VARIANTS:
        if name not in RUN:
            continue
        if os.path.exists(os.path.join(DIR, f"{name}.tif")):
            print(f"{name}: already there, skipped")
            continue
        code = subprocess.run([sys.executable, os.path.abspath(__file__), name]).returncode
        if code != 0:
            print(f"  {name}: child process ended with code {code} (crash or tool error), no result")
    print("Done. Compare with:  python arcgis_tests\\one_tree.py compare")


if "__file__" not in globals():         # exec() in the ArcGIS Pro Python window
    main()
    print("Done. Compare with:  python arcgis_tests\\one_tree.py compare")
elif len(sys.argv) > 1:                 # child process: one variant
    main(sys.argv[1])
else:
    main_isolated()
