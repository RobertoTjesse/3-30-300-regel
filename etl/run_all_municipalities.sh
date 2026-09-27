#!/usr/bin/env bash
# run_all_municipalities.sh — Run the full pipeline one municipality at a
# time (tile -> compute -> merge -> benchmark), so each municipality fully
# completes before the next one starts. Prints one MUNICIPALITY_DONE line
# per completed municipality to stdout; everything else goes to
# logs/full_run.log.
#
# Usage:
#   ./etl/run_all_municipalities.sh [name1 name2 ...]
#
# With no arguments, runs every municipality found (respecting
# config.MUNICIPALITIES / config_local.py). Pass explicit names to run (or
# resume) only those. WORKERS=N sets stage 2's parallel processes (default 4).
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
QGIS_PY="${QGIS_PY:-/c/Users/bethrt/AppData/Local/Programs/OSGeo4W/bin/python-qgis-ltr.bat}"
PYEXE="${PYEXE:-/c/Users/bethrt/AppData/Local/Programs/OSGeo4W/apps/Python312/python.exe}"
LOGFILE="logs/full_run.log"
mkdir -p logs

if [ "$#" -gt 0 ]; then
  names=("$@")
else
  mapfile -t names < <("$PYEXE" -c "
import sys; sys.path.insert(0, 'etl')
import config
for name, _, _ in config.municipality_pairs():
    print(name)
" | tr -d '\r')  # Windows Python ends lines with CR LF
fi

echo "Processing ${#names[@]} municipalities" >> "$LOGFILE"

for name in "${names[@]}"; do
  start_line=$(( $(wc -l < "$LOGFILE") + 1 ))
  start_marker="$(mktemp)"
  {
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') START $name ==="
    MUNICIPALITY_OVERRIDE="$name" "$PYEXE" etl/01_tile_dem.py \
      && MUNICIPALITY_OVERRIDE="$name" "$PYEXE" etl/02_compute_viewsheds.py --workers "${WORKERS:-4}" --resume \
      && MUNICIPALITY_OVERRIDE="$name" "$PYEXE" etl/03_merge_tiles.py
  } >> "$LOGFILE" 2>&1
  status=$?

  # Delete this municipality's intermediate tiles once its final output is
  # safely written — province-wide they'd need ~250GB, and 01_tile_dem.py
  # reuses any tile it finds, so leftovers from an older run could silently
  # end up in a newer one. Tiles are kept (for inspection / --resume) if any
  # stage failed, the output wasn't freshly written, or stage 2 logged tile
  # errors (it exits 0 even then). KEEP_TILES=1 keeps them regardless.
  output="data/processed/${name}_viewshed.tif"
  if [ "${KEEP_TILES:-0}" != "1" ] && [ "$status" -eq 0 ] \
     && [ "$output" -nt "$start_marker" ] \
     && tail -n +"$start_line" "$LOGFILE" | grep -q "\[$name\] Finished\. .* 0 errors"; then
    rm -rf "data/interim/dem_tiles/$name" "data/interim/viewshed_tiles/$name" \
           "data/processed/${name}_mosaic.vrt"
    echo "[$name] intermediate tiles removed" >> "$LOGFILE"
  else
    echo "[$name] intermediate tiles KEPT (status=$status) — check $LOGFILE" | tee -a "$LOGFILE"
  fi
  rm -f "$start_marker"

  "$PYEXE" etl/generate_benchmark_report.py >> "$LOGFILE" 2>&1
  "$PYEXE" etl/print_municipality_summary.py "$name"
  # Add any new output layers to the QGIS validation project (never fatal).
  # SKIP_QGIS=1 when several runners run in parallel: they must not write
  # the same .qgz at once — update the project once afterwards instead.
  if [ "${SKIP_QGIS:-0}" != "1" ]; then
    cmd.exe //c "$(cygpath -w "$QGIS_PY")" qgis_validation/build_project.py >> "$LOGFILE" 2>&1 \
      || echo "[$name] WARNING: QGIS project update failed — see $LOGFILE"
  fi
done

echo "ALL_MUNICIPALITIES_DONE"
