#!/usr/bin/env bash
# run_300.sh — Run steps of the 300 and log the run time of each step.
#
# Usage (from the repository root, in Git Bash):
#   bash indicator_300_park/etl/run_300.sh "01_prepare.py" "03_grass.py --homes-only" ...
#
# Every step runs with the OSGeo4W Python; its output goes to
# logs/300_<area>.log and its run time to logs/300_benchmark.csv
# (date, area, step, engine, seconds, exit code). Stops at the first error.
set -u
OSGEO4W="${OSGEO4W_ROOT:-/c/Users/bethrt/AppData/Local/Programs/OSGeo4W}"
export PATH="$OSGEO4W/bin:$PATH" GDAL_DATA="$OSGEO4W/apps/gdal/share/gdal" PROJ_DATA="$OSGEO4W/share/proj"
PY="$OSGEO4W/apps/Python312/python.exe"
ETL="indicator_300_park/etl"
AREA=$("$PY" -c "import sys; sys.path.insert(0, '$ETL'); import config; print(config.AREA_SLUG)" 2>/dev/null)
LOG="logs/300_${AREA}.log"
CSV="logs/300_benchmark.csv"
[ -f "$CSV" ] || echo "date,area,step,engine,seconds,exit" > "$CSV"
for step in "$@"; do
  case "$step" in
    02_valhalla*) engine=valhalla ;;
    03_grass*)    engine=grass ;;
    *)            engine=gdal ;;
  esac
  echo "== $step $(date +%H:%M:%S)" >> "$LOG"
  t0=$(date +%s)
  # shellcheck disable=SC2086
  "$PY" -u -W ignore "$ETL"/$step 2>&1 \
    | grep --line-buffered -v "WARN\]\|ERROR\]\|organizePol\|locking\|Self-inter\|INFO\]\|BigInteger" >> "$LOG"
  rc=${PIPESTATUS[0]}
  t1=$(date +%s)
  echo "exit $rc $(date +%H:%M:%S) ($((t1 - t0)) s)" >> "$LOG"
  echo "$(date +%F),$AREA,$step,$engine,$((t1 - t0)),$rc" >> "$CSV"
  [ "$rc" -ne 0 ] && break
done
echo FINISHED >> "$LOG"
