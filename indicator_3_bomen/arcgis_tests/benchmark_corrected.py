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

In tiles: one Visibility run over all of Delft took more than 2.5 hours
on one core without finishing. Every tree sees at most 30 m, so Delft is
cut into TILE_M x TILE_M tiles; each tile runs on its own DEM clip and the
trees within 30 m of it (tile + 30 m on every side), only its inner part
is kept, and the tiles are mosaicked. The result equals one run over the
whole area. WORKERS tiles run at the same time, each in its own process
and scratch folder; finished tiles are kept, so a rerun resumes.

The old GRID engine behind Visibility dies on paths like
D:\Repositories\3-regel (digit + hyphen) and failed with inputs in a file
geodatabase, so everything runs on .tif/.shp files in a plain work folder
and the result is copied back afterwards.

Output:
  WORK\visibility_Delft_corrected.tif  (or ..._test.tif for the test area)
  data\processed\experiments\<same name>.tif  — picked up by the QGIS
      validation project (qgis_validation\build_project.py)
Compare with the old benchmark and the pipeline afterwards (OSGeo4W Python):
  python indicator_3_bomen/arcgis_tests/compare_benchmark.py

HOW TO RUN — ArcGIS Pro Python Command Prompt (Start menu → ArcGIS →
Python Command Prompt), with ArcGIS Pro itself CLOSED:
  the small test area (one tile, ~1-2 minutes):
      python D:\Repositories\330300regel\indicator_3_bomen\arcgis_tests\benchmark_corrected.py test
  all of Delft, in tiles (prints progress and an estimate of the time left):
      python D:\Repositories\330300regel\indicator_3_bomen\arcgis_tests\benchmark_corrected.py
"""

import glob
import os
import shutil
import subprocess
import sys
import time

import arcpy
from arcpy.sa import Int, Raster, Visibility

SOURCE_GDB = r"R:\ESRI\DATA\RUIMTELIJKE ONTWIKKELING\PERSOONLIJK\Chris\test\data.gdb"
DEM = os.path.join(SOURCE_GDB, "AHN5ruw05m_Delft")
TREES = os.path.join(SOURCE_GDB, "bomen_Delft_met_hoogte_uit_AHN05ruw")
WORK = r"D:\Temp\benchmark_corrected"
TILES_DIR = os.path.join(WORK, "tiles")
EXPERIMENTS_DIR = r"D:\Repositories\330300regel\data\processed\experiments"
OUT_NAME = "visibility_Delft_corrected"

TILE_M = 500.0                 # tile size; the 85 s test area was 528 x 466 m
MARGIN = 30.0                  # = outer radius: trees this far outside a tile see into it
WORKERS = 5                    # tiles at the same time (this machine: 6 cores)
CELL = 0.5
NODATA = 2147483647           # of every tile and of the mosaic

# The "stukje" test area in Delft (as arcgis_tests/visibility_variants.py)
TEST_AREA = (82982.0, 445985.5, 83510.5, 446451.0)

# Visibility settings: as the original benchmark, except the observer offset
OBSERVER_OFFSET = "1"          # metres; the fix
SURFACE_OFFSET = "1.8"         # target (eye) height above the surface
OUTER_RADIUS = "30"

# Plain copies of the inputs in the work folder (the old GRID engine fails
# on some paths, see the module docstring)
WORK_DEM = os.path.join(WORK, "dem.tif")
WORK_TREES = os.path.join(WORK, "trees.shp")


def copy_files(stem, src, dst):
    """Copy a raster and its side files (stem.tif, .tfw, .aux.xml, ...)."""
    for f in glob.glob(os.path.join(src, stem + ".*")):
        shutil.copy2(f, dst)


def box(x0, y0, x1, y1):
    """An extent as the "xmin ymin xmax ymax" string arcpy's Clip expects."""
    return f"{x0} {y0} {x1} {y1}"


def run_tile(x0, y0, x1, y1, out_tif, folder):
    """Visibility for the inner box (x0, y0)-(x1, y1): DEM and trees of the
    box + MARGIN, result clipped to the inner box and saved as out_tif.
    Returns the number of trees used."""
    arcpy.CheckOutExtension("Spatial")
    arcpy.env.overwriteOutput = True
    os.makedirs(folder, exist_ok=True)
    arcpy.env.workspace = arcpy.env.scratchWorkspace = folder
    # Cut the DEM and the trees to the tile plus MARGIN (the "outer" box)
    ox0, oy0, ox1, oy1 = x0 - MARGIN, y0 - MARGIN, x1 + MARGIN, y1 + MARGIN
    dem = os.path.join(folder, "dem.tif")
    trees = os.path.join(folder, "trees.shp")
    area = os.path.join(folder, "area.shp")
    arcpy.env.snapRaster = WORK_DEM
    arcpy.management.Clip(WORK_DEM, box(ox0, oy0, ox1, oy1), dem, nodata_value="",
                          clipping_geometry="NONE", maintain_clipping_extent="MAINTAIN_EXTENT")
    sr = arcpy.Describe(WORK_DEM).spatialReference
    corners = [arcpy.Point(ox0, oy0), arcpy.Point(ox0, oy1), arcpy.Point(ox1, oy1), arcpy.Point(ox1, oy0)]
    arcpy.management.CopyFeatures(arcpy.Polygon(arcpy.Array(corners), sr), area)
    arcpy.analysis.Clip(WORK_TREES, area, trees)
    n = int(arcpy.management.GetCount(trees)[0])
    arcpy.env.snapRaster = arcpy.env.cellSize = arcpy.env.extent = dem
    if n:
        result = Visibility(dem, trees, analysis_type="FREQUENCY", nonvisible_cell_value="ZERO",
                            z_factor=1, curvature_correction="FLAT_EARTH", refractivity_coefficient=0.13,
                            surface_offset=SURFACE_OFFSET, observer_elevation="RASTERVALU",
                            observer_offset=OBSERVER_OFFSET, outer_radius=OUTER_RADIUS)
    else:
        result = Int(Raster(dem) * 0)          # no trees: 0 where the DEM has data
    # Keep only the inner box: the margin was context, the neighbouring tile
    # computes those cells itself
    full = os.path.join(folder, "vis.tif")
    result.save(full)
    arcpy.env.extent = arcpy.Extent(x0, y0, x1, y1)
    inner = os.path.join(folder, "inner.tif")
    arcpy.management.Clip(full, box(x0, y0, x1, y1), inner, nodata_value="",
                          clipping_geometry="NONE", maintain_clipping_extent="MAINTAIN_EXTENT")
    # One pixel type and NoData value for every tile: otherwise each tile gets
    # the smallest type for its values (8 or 16 bit, NoData -128 / 65535) and
    # MosaicToNewRaster copies those markers into the result as counts
    arcpy.management.CopyRaster(inner, out_tif, pixel_type="32_BIT_SIGNED", nodata_value=str(NODATA))
    return n


def prepare_inputs(test):
    """Copy the DEM and the trees that have a RASTERVALU to the work folder
    (once; later runs reuse them)."""
    arcpy.env.overwriteOutput = True
    os.makedirs(TILES_DIR, exist_ok=True)
    if not os.path.exists(WORK_DEM):
        print("Copying the DEM to the work folder ...", flush=True)
        arcpy.management.CopyRaster(DEM, WORK_DEM)
    if not os.path.exists(WORK_TREES):
        arcpy.analysis.Select(TREES, WORK_TREES, "RASTERVALU IS NOT NULL")
    print(f"{int(arcpy.management.GetCount(WORK_TREES)[0]):,} trees with a RASTERVALU", flush=True)


def tiles():
    """Inner tile boxes over the DEM extent, on the DEM grid."""
    ext = arcpy.Describe(WORK_DEM).extent
    out = []
    y = ext.YMin
    j = 0
    while y < ext.YMax - CELL / 2:
        x, i = ext.XMin, 0
        while x < ext.XMax - CELL / 2:
            out.append((f"t_{j:03d}_{i:03d}", x, y, min(x + TILE_M, ext.XMax), min(y + TILE_M, ext.YMax)))
            x += TILE_M
            i += 1
        y += TILE_M
        j += 1
    return out


def main_all():
    """All of Delft: run the missing tiles, WORKERS at a time, each in its own
    process (this script with "tile ..."); when all succeeded, mosaic them."""
    t_start = time.time()
    prepare_inputs(False)
    todo = [t for t in tiles() if not os.path.exists(os.path.join(TILES_DIR, t[0] + ".tif"))]
    n_all = len(tiles())
    print(f"{n_all} tiles of {TILE_M:.0f} m, {n_all - len(todo)} already done; running {len(todo)} "
          f"with {WORKERS} at a time", flush=True)
    running, failed, done, t0 = [], [], 0, time.time()
    queue = list(todo)
    # A simple process pool: keep WORKERS tiles running, check every 2 s which
    # finished, and start the next ones
    while queue or running:
        while queue and len(running) < WORKERS:
            name, x0, y0, x1, y1 = queue.pop(0)
            log = open(os.path.join(TILES_DIR, name + ".log"), "w")
            p = subprocess.Popen([sys.executable, os.path.abspath(__file__), "tile", name,
                                  str(x0), str(y0), str(x1), str(y1)], stdout=log, stderr=subprocess.STDOUT)
            running.append((p, name, log))
        time.sleep(2)
        for item in list(running):
            p, name, log = item
            if p.poll() is None:
                continue
            running.remove(item)
            log.close()
            # Success = exit code 0 and a result file; then its scratch folder goes
            if p.returncode == 0 and os.path.exists(os.path.join(TILES_DIR, name + ".tif")):
                done += 1
                shutil.rmtree(os.path.join(TILES_DIR, name), ignore_errors=True)
            else:
                failed.append(name)
                print(f"  {name} FAILED (code {p.returncode}) — see {TILES_DIR}\\{name}.log", flush=True)
            per_tile = (time.time() - t0) / max(done + len(failed), 1)
            left = (len(queue) + len(running)) * per_tile
            print(f"  {done + n_all - len(todo)}/{n_all} tiles done, {len(failed)} failed; "
                  f"about {left / 60:.0f} min left", flush=True)

    if failed:
        print(f"\n{len(failed)} tile(s) failed: {', '.join(failed)}. Run the same command again to "
              f"retry them (finished tiles are kept); no mosaic yet.")
        return
    print("Mosaicking ...", flush=True)
    finished = sorted(glob.glob(os.path.join(TILES_DIR, "t_*.tif")))
    out = os.path.join(WORK, f"{OUT_NAME}.tif")
    if os.path.exists(out):
        arcpy.management.Delete(out)
    arcpy.env.snapRaster = WORK_DEM
    arcpy.management.MosaicToNewRaster(finished, WORK, f"{OUT_NAME}.tif", pixel_type="32_BIT_SIGNED",
                                       cellsize=CELL, number_of_bands=1, mosaic_method="FIRST")
    os.makedirs(EXPERIMENTS_DIR, exist_ok=True)
    copy_files(OUT_NAME, WORK, EXPERIMENTS_DIR)
    print(f"Saved {out} and copied it to {EXPERIMENTS_DIR} — {(time.time() - t_start) / 60:.0f} min in total")
    print("Next, in OSGeo4W Python:  python indicator_3_bomen/arcgis_tests/compare_benchmark.py")


def main_test():
    """Only the small test area, as one tile in this process."""
    prepare_inputs(True)
    t0 = time.time()
    name = f"{OUT_NAME}_test"
    out = os.path.join(WORK, f"{name}.tif")
    n = run_tile(*TEST_AREA, out, os.path.join(TILES_DIR, "test"))
    print(f"Test area: {n:,} trees, {time.time() - t0:.0f}s; saved {out}", flush=True)
    os.makedirs(EXPERIMENTS_DIR, exist_ok=True)
    copy_files(name, WORK, EXPERIMENTS_DIR)
    print(f"Copied to {EXPERIMENTS_DIR}. Next, in OSGeo4W Python:  "
          f"python indicator_3_bomen/arcgis_tests/compare_benchmark.py test")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "tile":             # child process: one tile
        name, x0, y0, x1, y1 = args[1], *map(float, args[2:6])
        t0 = time.time()
        n = run_tile(x0, y0, x1, y1, os.path.join(TILES_DIR, name + ".tif"), os.path.join(TILES_DIR, name))
        print(f"{name}: {n} trees, {time.time() - t0:.0f}s")
    elif args and args[0] == "test":
        main_test()
    else:
        main_all()
