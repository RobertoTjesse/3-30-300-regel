r"""
one_tree_arcgis.py — ArcGIS Pro half of the one-tree / tree-group comparison
(arcgis_tests/one_tree.py): Viewshed2, the classic Viewshed tool and
Visibility on exactly the inputs GDAL got, plus the benchmark's own
Visibility settings.

Inputs per case (made by `python arcgis_tests/one_tree.py prepare`), in
arcgis_tests\one_tree\<case>\:
  dem.tif       AHN5 raw DSM, 0.5 m, NoData filled (what GDAL got)
  dem_raw.tif   the same without the fill (what the benchmark got)
  tree.shp      the case's trees: OBS_Z = observer elevation (m NAP),
                RASTERVALU (benchmark), SPOT/OFFSETA/OFFSETB/RADIUS2
                (classic Viewshed, RADIUS2 -30); tree_3d.shp RADIUS2 = +30

Outputs per case (same grid as dem.tif; counts = number of the case's
trees that see a cell):
  arc_bench.tif    Visibility exactly as the benchmark visibility_Delft:
                   dem_raw, observer_elevation RASTERVALU, observer_offset
                   left out (tool default, 1 m), surface_offset 1.8, outer
                   radius 30, FREQUENCY, flat earth
  arc_bench_offset.tif  the same, but RASTERVALU as observer_offset and no
                   observer_elevation: the observer the benchmark turned
                   out to use (surface + RASTERVALU)
  arc_bench_both.tif    RASTERVALU as observer_elevation AND observer_offset
                   — reproduces visibility_Delft exactly
  arc_bench_fixed.tif   the corrected benchmark (benchmark_corrected.py):
                   RASTERVALU as observer_elevation, observer_offset 1 m
  arc_v2_2d.tif    Viewshed2, 30 m measured on the ground (2D)
  arc_v2_3d.tif    Viewshed2, 30 m as 3D line-of-sight distance
  arc_vs_2d.tif    classic Viewshed, RADIUS2 = -30
  arc_vs_3d.tif    classic Viewshed, RADIUS2 = 30
  arc_vis_2d.tif   Visibility, OBS_Z with offset 0, outer radius -30
  arc_vis_3d.tif   Visibility, OBS_Z with offset 0, outer radius 30
All but arc_bench use dem.tif and OBS_Z (= RASTERVALU + 1 m) with offset 0:
the same observer as the benchmark, on the filled DEM.

The classic Viewshed and Visibility tools (old GRID engine) die with exit
code -1 on inputs under D:\Repositories\3-regel (a folder name starting
with a digit and containing a hyphen), so every case is copied to a plain
work folder (WORK\<case>), the tools run there, and the results are copied
back.

HOW TO RUN — from the ArcGIS Pro Python Command Prompt (Start menu →
ArcGIS → Python Command Prompt), all cases:
    python D:\Repositories\3-regel\arcgis_tests\one_tree_arcgis.py
  or one case:
    python D:\Repositories\3-regel\arcgis_tests\one_tree_arcgis.py group_5
  Every tool runs in its own child process: a tool that crashes only loses
  its own result. Results that already exist are skipped (delete the .tif
  to redo one).
Or in ArcGIS Pro → Analysis → Python window (all in one process — a crash
takes Pro down):
    exec(open(r"D:\Repositories\3-regel\arcgis_tests\one_tree_arcgis.py", encoding="utf-8").read())
Then, in OSGeo4W Python:  python arcgis_tests\one_tree.py compare
"""

import glob
import os
import shutil
import subprocess
import sys

import arcpy
from arcpy.sa import Viewshed, Viewshed2, Visibility

ROOT = r"D:\Repositories\3-regel\arcgis_tests\one_tree"
WORK = r"D:\Temp\onetree"     # plain path for the old GRID-engine tools; None = work in ROOT
CASES = ["tree_68418", "group_5", "tree_58448"]
RUN = ["arc_bench", "arc_bench_offset", "arc_bench_both", "arc_bench_fixed", "arc_v2_2d", "arc_v2_3d", "arc_vs_2d", "arc_vs_3d", "arc_vis_2d", "arc_vis_3d"]

# Set per case by use_case()
DEM = DEM_RAW = TREE = TREE_3D = None


def _v2(radius_3d):
    return Viewshed2(
        DEM, TREE, analysis_type="FREQUENCY", refractivity_coefficient=0.13,
        surface_offset="1.8 Meters", observer_elevation="OBS_Z", observer_offset="0 Meters",
        outer_radius="30 Meters", outer_radius_is_3d=radius_3d,
        analysis_method="ALL_SIGHTLINES", analysis_target_device="CPU_ONLY")


def _vis(radius):
    return Visibility(
        DEM, TREE, analysis_type="FREQUENCY", nonvisible_cell_value="ZERO", z_factor=1,
        curvature_correction="FLAT_EARTH", refractivity_coefficient=0.13,
        surface_offset="1.8", observer_elevation="OBS_Z", observer_offset="0", outer_radius=radius)


VARIANTS = [
    # the benchmark run: raw DSM, RASTERVALU, observer_offset left at the tool default
    ("arc_bench", lambda: Visibility(
        DEM_RAW, TREE, analysis_type="FREQUENCY", nonvisible_cell_value="ZERO", z_factor=1,
        curvature_correction="FLAT_EARTH", refractivity_coefficient=0.13,
        surface_offset="1.8", observer_elevation="RASTERVALU", outer_radius="30")),
    # the observer the benchmark turned out to use (one_tree.py compare): RASTERVALU
    # given as the observer OFFSET (height above the surface), no observer elevation
    ("arc_bench_offset", lambda: Visibility(
        DEM_RAW, TREE, analysis_type="FREQUENCY", nonvisible_cell_value="ZERO", z_factor=1,
        curvature_correction="FLAT_EARTH", refractivity_coefficient=0.13,
        surface_offset="1.8", observer_offset="RASTERVALU", outer_radius="30")),
    # ... or RASTERVALU for both the elevation and the offset
    ("arc_bench_both", lambda: Visibility(
        DEM_RAW, TREE, analysis_type="FREQUENCY", nonvisible_cell_value="ZERO", z_factor=1,
        curvature_correction="FLAT_EARTH", refractivity_coefficient=0.13,
        surface_offset="1.8", observer_elevation="RASTERVALU", observer_offset="RASTERVALU",
        outer_radius="30")),
    # the corrected benchmark (benchmark_corrected.py): RASTERVALU as elevation, offset 1 m
    ("arc_bench_fixed", lambda: Visibility(
        DEM_RAW, TREE, analysis_type="FREQUENCY", nonvisible_cell_value="ZERO", z_factor=1,
        curvature_correction="FLAT_EARTH", refractivity_coefficient=0.13,
        surface_offset="1.8", observer_elevation="RASTERVALU", observer_offset="1",
        outer_radius="30")),
    ("arc_v2_2d", lambda: _v2("GROUND")),
    ("arc_v2_3d", lambda: _v2("3D")),
    # classic Viewshed: observer settings from the SPOT/OFFSETA/OFFSETB/RADIUS2 fields
    ("arc_vs_2d", lambda: Viewshed(DEM, TREE, z_factor=1, curvature_correction="FLAT_EARTH",
                                   refractivity_coefficient=0.13)),
    ("arc_vs_3d", lambda: Viewshed(DEM, TREE_3D, z_factor=1, curvature_correction="FLAT_EARTH",
                                   refractivity_coefficient=0.13)),
    ("arc_vis_2d", lambda: _vis("-30")),
    ("arc_vis_3d", lambda: _vis("30")),
]


def copy_files(stem, src, dst):
    """Copy stem.* (a raster or shapefile with its side files) from src to dst."""
    for f in glob.glob(os.path.join(src, stem + ".*")):
        shutil.copy2(f, dst)


def use_case(case):
    """Point DEM/DEM_RAW/TREE/TREE_3D to the case's inputs (copied to WORK)
    and return (folder, work folder)."""
    global DEM, DEM_RAW, TREE, TREE_3D
    folder = os.path.join(ROOT, case)
    work = os.path.join(WORK, case) if WORK else folder
    for stem in ("dem", "dem_raw", "tree", "tree_3d"):
        if not glob.glob(os.path.join(folder, stem + ".*")):
            raise SystemExit(f"Not found: {folder}\\{stem} - run `python arcgis_tests/one_tree.py prepare` first")
    if WORK:
        os.makedirs(work, exist_ok=True)
        for stem in ("dem", "dem_raw", "tree", "tree_3d"):
            copy_files(stem, folder, work)
    DEM, DEM_RAW = os.path.join(work, "dem.tif"), os.path.join(work, "dem_raw.tif")
    TREE, TREE_3D = os.path.join(work, "tree.shp"), os.path.join(work, "tree_3d.shp")
    return folder, work


def main(cases, only=None):
    """Run the variants in RUN (or just `only`) for the cases, in this process."""
    arcpy.CheckOutExtension("Spatial")
    arcpy.env.overwriteOutput = True
    for case in cases:
        folder, work = use_case(case)
        arcpy.env.workspace = arcpy.env.scratchWorkspace = work
        arcpy.env.snapRaster = arcpy.env.extent = arcpy.env.cellSize = DEM
        for name, run in VARIANTS:
            if name not in (RUN if only is None else [only]):
                continue
            if only is None and os.path.exists(os.path.join(folder, f"{name}.tif")):
                print(f"{case} {name}: already there, skipped")
                continue
            print(f"{case} {name} ...", flush=True)
            try:
                run().save(os.path.join(work, f"{name}.tif"))
                if work != folder:
                    copy_files(name, work, folder)
                print(f"  saved {name}.tif", flush=True)
            except Exception as exc:        # a normal tool error: report and continue
                print(f"  FAILED: {exc}", flush=True)
                if only is not None:
                    raise SystemExit(1)


def main_isolated(cases):
    """Command line: one child process per case and variant, so a crash loses only that one."""
    for case in cases:
        for name, _ in VARIANTS:
            if name not in RUN:
                continue
            if os.path.exists(os.path.join(ROOT, case, f"{name}.tif")):
                print(f"{case} {name}: already there, skipped")
                continue
            code = subprocess.run([sys.executable, os.path.abspath(__file__), case, name]).returncode
            if code != 0:
                print(f"  {case} {name}: child process ended with code {code} (crash or tool error), no result")
    print("Done. Compare with:  python arcgis_tests\\one_tree.py compare")


if "__file__" not in globals():         # exec() in the ArcGIS Pro Python window
    main(CASES)
    print("Done. Compare with:  python arcgis_tests\\one_tree.py compare")
elif len(sys.argv) > 2:                 # child process: one case, one variant
    main([sys.argv[1]], sys.argv[2])
else:
    main_isolated([sys.argv[1]] if len(sys.argv) > 1 else CASES)
