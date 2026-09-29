#!/usr/bin/env bash
# watch_and_finish.sh — watches raw_clips/ for each target file, and as soon
# as one is fully written (stable size, no .autolock), immediately runs the
# gdal_translate finishing pass and deletes the raw clip. Combines
# watching + processing into one loop so raw clips never pile up.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
# OSGeo4W's gdal_translate; set GDAL_TRANSLATE to override on another machine
GDAL_TRANSLATE="${GDAL_TRANSLATE:-/c/Users/bethrt/AppData/Local/Programs/OSGeo4W/bin/gdal_translate.exe}"
RAW_DIR="sde_reexport/raw_clips"
FINISHED_DIR="sde_reexport/finished"
mkdir -p "$FINISHED_DIR"

targets=("$@")
declare -A done

process_one() {
  local name="$1"
  local src="$RAW_DIR/$name.tif"
  local dst="$FINISHED_DIR/$name.tif"
  echo "PROCESSING: $name"
  "$GDAL_TRANSLATE" -a_nodata -9999 \
    -co COMPRESS=DEFLATE -co PREDICTOR=3 -co ZLEVEL=6 \
    -co TILED=YES -co BLOCKXSIZE=512 -co BLOCKYSIZE=512 \
    -co BIGTIFF=IF_SAFER \
    "$src" "$dst" >/dev/null 2>&1
  if [ -f "$dst" ]; then
    rm -f "$src" "$RAW_DIR/$name.tfw" "$RAW_DIR/$name.tif.aux.xml" "$RAW_DIR/$name.tif.ovr" "$RAW_DIR/$name.tif.xml"
    echo "FINISHED: $name"
  else
    echo "FAILED: $name (gdal_translate did not produce output)"
  fi
}

while true; do
  for name in "${targets[@]}"; do
    f="$RAW_DIR/$name.tif"
    lock="$RAW_DIR/$name.tif.autolock"
    if [ -f "$f" ] && [ ! -f "$lock" ] && [ -z "${done[$name]:-}" ]; then
      sz1=$(stat -c%s "$f" 2>/dev/null)
      sleep 3
      sz2=$(stat -c%s "$f" 2>/dev/null)
      if [ "$sz1" = "$sz2" ] && [ -n "$sz1" ]; then
        process_one "$name"
        done[$name]=1
      fi
    fi
  done
  all_done=1
  for name in "${targets[@]}"; do
    [ -z "${done[$name]:-}" ] && all_done=0
  done
  if [ "$all_done" = "1" ]; then
    echo "ALL_DONE"
    break
  fi
  sleep 15
done
