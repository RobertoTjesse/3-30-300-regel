"""
replace_on_share.py — Replace the municipality DEMs on the share
(config.VIEWANALYSE_DIR) with the re-exported ones in sde_reexport/finished/.

For every finished/<name>.tif (skipping any still being written):
  1. Move the original <name>.tif and its side files (.tif.aux, .tif.aux.xml,
     .ovr, .tfw, ...) into VIEWANALYSE_DIR/_origineel_<date>/ — a rename on
     the same share, instant. The side files must go too: overviews and
     statistics built from the old data would otherwise sit next to the new
     file.
  2. Copy the new file in as <name>.tif.copying, then rename it to <name>.tif,
     so a half-copied file never looks complete.
  3. Verify: same size as the source, opens in GDAL, NoData set.
Municipalities already replaced (backup present, target equals source size)
are skipped, so this can be re-run once the last exports have finished.

Usage:
    python indicator_3_bomen/sde_reexport/replace_on_share.py
"""

import datetime
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "etl"))
import config  # noqa: E402

from osgeo import gdal  # noqa: E402
gdal.UseExceptions()

FINISHED = Path(__file__).resolve().parent / "finished"      # the re-exported DEMs
TARGET = config.VIEWANALYSE_DIR                              # the share they replace
BACKUP = TARGET / f"_origineel_{datetime.date.today():%Y-%m-%d}"   # originals go here


def verify(src: Path, dst: Path):
    """Raise if the copy differs in size from its source or has no NoData value."""
    if dst.stat().st_size != src.stat().st_size:
        raise RuntimeError(f"{src.stem}: size mismatch after copy")
    ds = gdal.Open(str(dst))          # keep ds referenced while using the band
    if ds.GetRasterBand(1).GetNoDataValue() is None:
        raise RuntimeError(f"{src.stem}: no NoData value on the copied file")


def replace(src: Path):
    """Replace one municipality's DEM on the share with its re-export.
    Safe to re-run: a DEM that was already replaced is only verified."""
    name = src.stem
    dst = TARGET / f"{name}.tif"
    # Already done in an earlier run (backup exists, same size): just verify
    if (BACKUP / f"{name}.tif").exists() and dst.exists() and dst.stat().st_size == src.stat().st_size:
        verify(src, dst)
        return f"{name}: already replaced (verified)"

    # Move the original .tif and its side files (.tfw, .ovr, .aux.xml, ...)
    # into the backup folder — moved, never deleted
    BACKUP.mkdir(exist_ok=True)
    moved = []
    if not (BACKUP / f"{name}.tif").exists():
        for f in TARGET.iterdir():
            if f.is_file() and (f.name == f"{name}.tif" or f.name.startswith(f"{name}.tif.")
                                or f.name in (f"{name}.tfw", f"{name}.ovr", f"{name}.rrd")):
                os.replace(f, BACKUP / f.name)
                moved.append(f.name)

    # Copy under a temporary name and rename when complete, so the share never
    # holds a half-copied <name>.tif
    tmp = TARGET / f"{name}.tif.copying"
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)

    verify(src, dst)
    return f"{name}: replaced ({src.stat().st_size / 1e9:.2f} GB; moved {len(moved)} original file(s) to backup)"


def main():
    """Replace every finished DEM (skipping unfinished .partial files)."""
    sources =sorted(p for p in FINISHED.glob("*.tif") if not p.name.endswith(".partial.tif"))
    print(f"{len(sources)} finished DEMs -> {TARGET}\nOriginals -> {BACKUP}", flush=True)
    for src in sources:
        print("  " + replace(src), flush=True)
    print("Done. Rebuild province_dem.vrt next (see README).")


if __name__ == "__main__":
    main()
