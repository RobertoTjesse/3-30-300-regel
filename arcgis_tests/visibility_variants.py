r"""
visibility_variants.py — Run ArcGIS Pro's viewshed tools on one small test
area with several parameter variants, to find out why the ArcGIS
"visibility_Delft" result differs from this pipeline's GDAL result.

Background (see the chat / ARCHITECTURE.md §6): with identical inputs —
same AHN5 DEM, same trees, same observer heights — GDAL's viewshed counts
fewer visible trees than ArcGIS's Visibility tool on ~70% of pixels. This
script varies one thing at a time on the ArcGIS side:

  - observer height rule: DSM at the tree point (bilinear, as in the
    reference run, or plain cell value) vs canopy top (max DSM within
    1.5 m of the tree — this pipeline's rule, without its building mask
    and plausibility check)
  - observer offset: ArcGIS default (empty = 1 m) vs explicit 0 / 1 m
  - tool: legacy Visibility vs Viewshed2 (all vs perimeter sightlines,
    outer radius measured on the ground vs in 3D)

For every variant it writes:
  <OUT_GDB>\<variant>                                  the frequency raster
  data\processed\experiments\arc_<variant>.tif         a copy the QGIS
      validation project picks up (qgis_validation\build_project.py)
  arcgis_tests\visibility_variants.csv                  mean count, share of
      pixels with 0 and >= 3 visible trees, inside the test area

The DEM and trees are clipped to the test area plus a 30 m margin (trees
just outside still see into it); statistics use the test area only.

HOW TO RUN — inside ArcGIS Pro (Analysis → Python window):
    exec(open(r"D:\Repositories\3-regel\arcgis_tests\visibility_variants.py", encoding="utf-8").read())
On this machine (Pro 3.6.1) the legacy Visibility tool crashes ArcGIS —
inside Pro and standalone, even on the reference run's own data — so
RUN_TOOLS below runs only Viewshed2 by default. A variant that raises a
normal error is logged in the CSV and the rest still runs; a native crash
closes Pro.

Check the values under "EDIT THESE" first. Add, remove or change variants
in VARIANTS; set ONLY to run a subset.
"""

import csv
import os
import time

import arcpy
from arcpy.sa import ExtractMultiValuesToPoints, FocalStatistics, NbrCircle, Visibility, Viewshed2

# ---------------------------------------------------------------------------
# EDIT THESE
# ---------------------------------------------------------------------------
SOURCE_GDB = r"R:\ESRI\DATA\RUIMTELIJKE ONTWIKKELING\PERSOONLIJK\Chris\test\data.gdb"
DEM = os.path.join(SOURCE_GDB, "AHN5ruw05m_Delft")
TREES = os.path.join(SOURCE_GDB, "bomen_Delft")

# Test area (RD New, metres). Default: the "stukje" test area in Delft.
XMIN, YMIN, XMAX, YMAX = 82982.0, 445985.5, 83510.5, 446451.0
MARGIN = 30.0                 # = outer radius: trees this far outside still count

REPO = r"D:\Repositories\3-regel"
OUT_GDB = os.path.join(REPO, "arcgis_tests", "visibility_tests.gdb")
EXPERIMENTS_DIR = os.path.join(REPO, "data", "processed", "experiments")
COPY_TO_EXPERIMENTS = True    # also write arc_<variant>.tif for the QGIS project

CANOPY_RADIUS_CELLS = 3       # 3 x 0.5 m = 1.5 m, as TREE_HEIGHT_BUFFER_RADIUS

# Observer height fields added to the test trees:
#   Z_INTERP    DSM at the tree point, bilinear (= RASTERVALU of the reference run)
#   Z_CELL      DSM value of the cell the tree falls in
#   CANOPY_TOP  max DSM within CANOPY_RADIUS_CELLS of that cell
#
# observer_offset "" = the tool's default (1 m for Visibility and Viewshed2),
# which is what the reference run used.
VARIANTS = [
    # name, tool, keyword arguments for that tool
    ("vis_interp_default", "Visibility", dict(observer_elevation="Z_INTERP", observer_offset="")),
    ("vis_interp_off0",    "Visibility", dict(observer_elevation="Z_INTERP", observer_offset="0")),
    ("vis_cell_default",   "Visibility", dict(observer_elevation="Z_CELL", observer_offset="")),
    ("vis_canopy_off0",    "Visibility", dict(observer_elevation="CANOPY_TOP", observer_offset="0")),
    ("v2_interp_off1",     "Viewshed2",  dict(observer_elevation="Z_INTERP", observer_offset="1")),
    ("v2_interp_off1_3d",  "Viewshed2",  dict(observer_elevation="Z_INTERP", observer_offset="1",
                                              outer_radius_is_3d="3D")),
    ("v2_interp_off1_perimeter", "Viewshed2", dict(observer_elevation="Z_INTERP", observer_offset="1",
                                                   analysis_method="PERIMETER_SIGHTLINES")),
    ("v2_canopy_off0",     "Viewshed2",  dict(observer_elevation="CANOPY_TOP", observer_offset="0")),
]
ONLY = []                     # e.g. ["vis_interp_default", "v2_canopy_off0"]; [] = all
# Tools to run. The legacy Visibility tool crashes ArcGIS Pro 3.6.1 on this
# machine (native access violation — Pro closes, Python can't catch it),
# even on the reference run's own data; it did work on the machine that
# made visibility_Delft. Add "Visibility" back when running it there.
RUN_TOOLS = ["Viewshed2"]
# ---------------------------------------------------------------------------

SURFACE_OFFSET = 1.8          # target (eye) height, same as the reference run
OUTER_RADIUS = 30
CSV_PATH = os.path.join(REPO, "arcgis_tests", "visibility_variants.csv")


def prepare_inputs():
    """Clip DEM + trees to the test area (+ margin) and add observer height fields."""
    ext = arcpy.Extent(XMIN - MARGIN, YMIN - MARGIN, XMAX + MARGIN, YMAX + MARGIN)
    sr = arcpy.Describe(DEM).spatialReference
    box = arcpy.Polygon(arcpy.Array([arcpy.Point(ext.XMin, ext.YMin), arcpy.Point(ext.XMin, ext.YMax),
                                     arcpy.Point(ext.XMax, ext.YMax), arcpy.Point(ext.XMax, ext.YMin)]), sr)
    box_fc = os.path.join(OUT_GDB, "test_area_with_margin")
    arcpy.management.CopyFeatures(box, box_fc)

    dem = os.path.join(OUT_GDB, "dem_test")
    arcpy.management.Clip(DEM, f"{ext.XMin} {ext.YMin} {ext.XMax} {ext.YMax}", dem,
                          nodata_value="", clipping_geometry="NONE",
                          maintain_clipping_extent="MAINTAIN_EXTENT")
    trees = os.path.join(OUT_GDB, "trees_test")
    arcpy.analysis.Clip(TREES, box_fc, trees)

    canopy = FocalStatistics(dem, NbrCircle(CANOPY_RADIUS_CELLS, "CELL"), "MAXIMUM", "DATA")
    canopy_path = os.path.join(OUT_GDB, "canopy_top_test")
    canopy.save(canopy_path)

    ExtractMultiValuesToPoints(trees, [[dem, "Z_INTERP"]], "BILINEAR")
    ExtractMultiValuesToPoints(trees, [[dem, "Z_CELL"], [canopy_path, "CANOPY_TOP"]], "NONE")

    # Trees on NoData (water) get NULL heights; the tools can't use them.
    n_all = int(arcpy.management.GetCount(trees)[0])
    lyr = arcpy.management.MakeFeatureLayer(
        trees, "trees_valid", "Z_INTERP IS NOT NULL AND Z_CELL IS NOT NULL AND CANOPY_TOP IS NOT NULL")
    n_valid = int(arcpy.management.GetCount(lyr)[0])
    valid = os.path.join(OUT_GDB, "trees_test_valid")
    arcpy.management.CopyFeatures(lyr, valid)
    arcpy.management.Delete(lyr)
    print(f"{n_valid} of {n_all} trees in the test area (+{MARGIN:.0f} m) have all heights")

    with arcpy.da.SearchCursor(valid, ["Z_INTERP", "Z_CELL", "CANOPY_TOP"]) as cur:
        rows = list(cur)
    if rows:
        med = lambda v: sorted(v)[len(v) // 2]
        print(f"  median Z_INTERP {med([r[0] for r in rows]):.2f}, Z_CELL {med([r[1] for r in rows]):.2f}, "
              f"CANOPY_TOP {med([r[2] for r in rows]):.2f} m NAP; "
              f"canopy top - interp: median {med([r[2] - r[0] for r in rows]):+.2f} m")
    return dem, valid


def run_variant(tool, dem, trees, kwargs):
    # "" = tool default: leave the argument out rather than passing it
    kwargs = {k: v for k, v in kwargs.items() if v != ""}
    if tool == "Visibility":
        args = dict(analysis_type="FREQUENCY", nonvisible_cell_value="ZERO", z_factor=1,
                    curvature_correction="FLAT_EARTH", refractivity_coefficient=0.13,
                    surface_offset=str(SURFACE_OFFSET), outer_radius=str(OUTER_RADIUS))
        args.update(kwargs)
        return Visibility(dem, trees, **args)
    if tool == "Viewshed2":
        args = dict(analysis_type="FREQUENCY", refractivity_coefficient=0.13,
                    surface_offset=f"{SURFACE_OFFSET} Meters", outer_radius=f"{OUTER_RADIUS} Meters",
                    outer_radius_is_3d="GROUND", analysis_method="ALL_SIGHTLINES",
                    analysis_target_device="CPU_ONLY")   # GPU path untested here; area is small
        args.update(kwargs)
        if args.get("observer_offset") not in (None, ""):
            args["observer_offset"] = f"{args['observer_offset']} Meters"
        return Viewshed2(dem, trees, **args)
    raise ValueError(f"unknown tool {tool}")


def stats(raster_path):
    """Mean count and % of pixels with 0 / >= 3 visible trees, inside the test area."""
    cell = float(arcpy.Describe(raster_path).meanCellWidth)
    ncols, nrows = round((XMAX - XMIN) / cell), round((YMAX - YMIN) / cell)
    arr = arcpy.RasterToNumPyArray(raster_path, arcpy.Point(XMIN, YMIN), ncols, nrows, nodata_to_value=-1)
    valid = arr[arr >= 0]
    n = valid.size or 1
    return {"pixels": int(valid.size), "mean": round(float(valid.mean()), 3) if valid.size else None,
            "pct_0": round(100 * float((valid == 0).sum()) / n, 1),
            "pct_ge3": round(100 * float((valid >= 3).sum()) / n, 1),
            "max": int(valid.max()) if valid.size else None}


def main():
    arcpy.CheckOutExtension("Spatial")
    arcpy.env.overwriteOutput = True
    if not arcpy.Exists(OUT_GDB):
        arcpy.management.CreateFileGDB(os.path.dirname(OUT_GDB), os.path.basename(OUT_GDB))
    arcpy.env.scratchWorkspace = OUT_GDB
    arcpy.env.workspace = OUT_GDB
    if COPY_TO_EXPERIMENTS:
        os.makedirs(EXPERIMENTS_DIR, exist_ok=True)

    print(f"Test area {XMIN}, {YMIN} - {XMAX}, {YMAX} ({XMAX - XMIN:.0f} x {YMAX - YMIN:.0f} m)")
    dem, trees = prepare_inputs()
    arcpy.env.snapRaster = dem
    arcpy.env.extent = dem

    variants = [v for v in VARIANTS if (not ONLY or v[0] in ONLY) and v[1] in RUN_TOOLS]
    print(f"Running {len(variants)} variant(s) with {RUN_TOOLS}")
    results = []
    for name, tool, kwargs in variants:
        print(f"\n{name}: {tool} {kwargs}")
        t0 = time.time()
        try:
            out = run_variant(tool, dem, trees, kwargs)
            path = os.path.join(OUT_GDB, name)
            out.save(path)
            if COPY_TO_EXPERIMENTS:
                arcpy.management.CopyRaster(path, os.path.join(EXPERIMENTS_DIR, f"arc_{name}.tif"),
                                            pixel_type="16_BIT_UNSIGNED", format="TIFF")
            s = stats(path)
            print(f"  {time.time() - t0:.0f}s  mean {s['mean']}  0 trees {s['pct_0']}%  "
                  f">=3 trees {s['pct_ge3']}%  max {s['max']}")
            results.append({"variant": name, "tool": tool, "params": repr(kwargs), **s,
                            "seconds": round(time.time() - t0)})
        except Exception as exc:   # keep going; report in the CSV
            print(f"  FAILED: {exc}")
            results.append({"variant": name, "tool": tool, "params": repr(kwargs), "error": str(exc)})

    fields = ["variant", "tool", "params", "pixels", "mean", "pct_0", "pct_ge3", "max", "seconds", "error"]
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(results)
    print(f"\nSummary: {CSV_PATH}")
    if COPY_TO_EXPERIMENTS:
        print("Rasters copied to data\\processed\\experiments\\ — run qgis_validation\\build_project.py "
              "to add them to the QGIS project.")


main()
