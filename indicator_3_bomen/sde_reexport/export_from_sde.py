r"""
export_from_sde.py — Re-export municipality DEM extents from the SDE raster,
end to end, from inside ArcGIS Pro.

Originally built for the 12 corrupted-DEM municipalities; now used for the
remaining re-export batch (every extent listed in
sde_reexport/municipality_extents.csv). For each municipality:

  1. arcpy Clip of its bounding box out of the SDE raster, with the source's
     real NoData value (or -9999 if it has none) → raw_clips/<name>.tif.
     Pyramids and statistics are switched off: they're thrown away anyway,
     and were most of the extra disk usage (the .ovr files) last time.
  2. Finishing pass with OSGeo4W's gdal_translate → finished/<name>.tif:
     COMPRESS=DEFLATE, PREDICTOR=3, TILED=YES 512x512, BIGTIFF=IF_SAFER.
     arcpy doesn't reliably control TIFF compression/internal tiling —
     getting that wrong is how the original files ended up strip-organized
     — so this step stays with GDAL, the same tool the pipeline uses
     everywhere else. Called as a subprocess, so it works from Pro's Python.
  3. Check the finished file has NoData set, then delete the raw clip.

Municipalities that already have a finished/<name>.tif are skipped, so the
script can simply be re-run after an interruption. A failure on one
municipality is logged and the batch continues.

It does NOT copy anything to R:\ — inspect the finished files first, then
replace the originals on the share by hand and rebuild province_dem.vrt
(see README, "Cross-municipality context").

HOW TO RUN (either works):
  - ArcGIS Pro → Analysis → Python window: paste this file's contents, or
    exec(open(r"D:\Repositories\330300regel\indicator_3_bomen\sde_reexport\export_from_sde.py", encoding="utf-8").read())
  - Standalone, with Pro's own interpreter:
    "C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe" sde_reexport\export_from_sde.py

Check the values under "EDIT THESE" first.
"""

import csv
import os
import subprocess
import time

import arcpy

# ---------------------------------------------------------------------------
# EDIT THESE
# ---------------------------------------------------------------------------
SDE_CONNECTION = r"R:\ESRI\BEHEER\Database_verbindingen\Geodatabase\Productie\Geo_raster\Geodatabase@Geo_raster@topografie.sde"
SDE_RASTER_DATASET = "Geo_raster.TOPOGRAFIE.AHN4_05M_RUW"  # AHN4, 0.5m, raw/unfiltered surface model

REEXPORT_DIR = r"D:\Repositories\330300regel\indicator_3_bomen\sde_reexport"
OSGEO4W_ROOT = r"C:\Users\bethrt\AppData\Local\Programs\OSGeo4W"

# Restrict to these names (as spelled in municipality_extents.csv);
# [] = every extent in the CSV that isn't finished yet.
ONLY = []
# ---------------------------------------------------------------------------

EXTENTS_CSV = os.path.join(REEXPORT_DIR, "municipality_extents.csv")
RAW_DIR = os.path.join(REEXPORT_DIR, "raw_clips")
FINISHED_DIR = os.path.join(REEXPORT_DIR, "finished")
NODATA_FALLBACK = -9999

GDAL_BIN = os.path.join(OSGEO4W_ROOT, "bin")
GDAL_TRANSLATE = os.path.join(GDAL_BIN, "gdal_translate.exe")
GDALINFO = os.path.join(GDAL_BIN, "gdalinfo.exe")
FINISH_OPTIONS = [
    "-co", "COMPRESS=DEFLATE", "-co", "PREDICTOR=3", "-co", "ZLEVEL=6",
    "-co", "TILED=YES", "-co", "BLOCKXSIZE=512", "-co", "BLOCKYSIZE=512",
    "-co", "BIGTIFF=IF_SAFER",
]


def _gdal_env():
    """Environment for the OSGeo4W subprocesses. Pro sets its own GDAL_DATA /
    PROJ paths for its bundled GDAL; OSGeo4W's tools must not pick those up."""
    env = dict(os.environ)
    for key in ("GDAL_DATA", "PROJ_LIB", "PROJ_DATA", "GDAL_DRIVER_PATH"):
        env.pop(key, None)
    env["GDAL_DATA"] = os.path.join(OSGEO4W_ROOT, "share", "gdal")
    env["PROJ_LIB"] = os.path.join(OSGEO4W_ROOT, "share", "proj")
    env["PATH"] = GDAL_BIN + os.pathsep + env.get("PATH", "")
    return env


def _run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True, env=_gdal_env())
    if result.returncode != 0:
        raise RuntimeError(f"{os.path.basename(cmd[0])} failed:\n{result.stderr.strip()}")
    return result.stdout


def clip(name, row, nodata_value, src_raster):
    """Clip one extent to raw_clips/<name>.tif and return its path."""
    # ArcGIS's raster-dataset name validation rejects spaces (and some other
    # characters) in the output name even when writing a plain .tif — e.g.
    # "Hoeksche Waard" fails with ERROR 000354. Clip under a safe name; the
    # finishing pass writes the real name.
    safe_name = "".join(c if (c.isalnum() or c in "-_") else "_" for c in name)
    raw_path = os.path.join(RAW_DIR, f"{safe_name}.tif")
    if arcpy.Exists(raw_path):
        # Possibly a partial clip from an interrupted run — never trust it.
        arcpy.management.Delete(raw_path)

    arcpy.management.Clip(
        in_raster=src_raster,
        rectangle=f"{row['xmin']} {row['ymin']} {row['xmax']} {row['ymax']}",
        out_raster=raw_path,
        nodata_value=nodata_value,
        clipping_geometry="NONE",
        maintain_clipping_extent="NO_MAINTAIN_EXTENT",
    )

    # Confirm pixel type survived the clip as 32-bit float; CopyRaster with
    # an explicit pixel_type is the reliable way to force it if not.
    pixel_type = arcpy.Describe(raw_path).pixelType
    if pixel_type != "F32":
        print(f"  WARNING: came out as {pixel_type}, forcing 32-bit float")
        tmp_path = os.path.join(RAW_DIR, f"{safe_name}_f32.tif")
        arcpy.management.CopyRaster(raw_path, tmp_path, pixel_type="32_BIT_FLOAT",
                                    nodata_value=nodata_value)
        arcpy.management.Delete(raw_path)
        raw_path = tmp_path
    return raw_path


def finish(raw_path, final_path, nodata_value):
    """gdal_translate the raw clip into the pipeline's standard TIFF layout,
    via a .partial file so an interrupted run never leaves a finished/ file
    that looks complete."""
    partial = final_path + ".partial.tif"
    if os.path.exists(partial):
        os.remove(partial)
    _run([GDAL_TRANSLATE, "-q", "-a_nodata", str(nodata_value), *FINISH_OPTIONS,
          raw_path, partial])

    info = _run([GDALINFO, partial])
    if "NoData Value=" not in info:
        raise RuntimeError(f"finished file has no NoData value set: {partial}")
    os.replace(partial, final_path)


def main():
    for tool in (GDAL_TRANSLATE, GDALINFO):
        if not os.path.exists(tool):
            raise SystemExit(f"Not found: {tool} — check OSGEO4W_ROOT")
    os.makedirs(RAW_DIR, exist_ok=True)
    os.makedirs(FINISHED_DIR, exist_ok=True)

    arcpy.env.overwriteOutput = True
    arcpy.env.pyramid = "NONE"
    arcpy.env.rasterStatistics = "NONE"

    src_raster = os.path.join(SDE_CONNECTION, SDE_RASTER_DATASET)

    # Use the source's own NoData value if it has one; only fall back to our
    # own sentinel if it genuinely doesn't. Void areas exported as literal
    # 0.0 instead of flagged NoData is exactly what broke the original files.
    source_nodata = getattr(arcpy.Describe(src_raster), "noDataValue", None)
    nodata_value = source_nodata if source_nodata not in (None, "") else NODATA_FALLBACK
    print(f"Source NoData: {source_nodata!r} -> using {nodata_value} for exports")

    with open(EXTENTS_CSV, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if ONLY:
        unknown = set(ONLY) - {r["name"] for r in rows}
        if unknown:
            raise SystemExit(f"Not in {EXTENTS_CSV}: {sorted(unknown)}")
        rows = [r for r in rows if r["name"] in ONLY]

    todo = [r for r in rows
            if not os.path.exists(os.path.join(FINISHED_DIR, f"{r['name']}.tif"))]
    print(f"{len(rows) - len(todo)} already finished, {len(todo)} to export from {src_raster}\n")

    failed = []
    for i, row in enumerate(todo, 1):
        name = row["name"]
        final_path = os.path.join(FINISHED_DIR, f"{name}.tif")
        print(f"[{i}/{len(todo)}] {name}  extent=({row['xmin']}, {row['ymin']}, {row['xmax']}, {row['ymax']})")
        t0 = time.time()
        try:
            raw_path = clip(name, row, nodata_value, src_raster)
            print(f"  clipped in {time.time() - t0:.0f}s, finishing…")
            finish(raw_path, final_path, nodata_value)
            arcpy.management.Delete(raw_path)
            size_gb = os.path.getsize(final_path) / 1e9
            print(f"  done in {time.time() - t0:.0f}s -> {final_path} ({size_gb:.2f} GB)")
        except Exception as exc:  # keep going; report at the end
            print(f"  FAILED: {exc}")
            failed.append(name)

    print(f"\nFinished: {len(todo) - len(failed)}/{len(todo)}")
    if failed:
        print("Failed (re-run the script to retry just these):", ", ".join(failed))
    print(f"Next: inspect {FINISHED_DIR}, replace the originals on R:\\, rebuild province_dem.vrt.")


main()
