"""
00_valhalla_tiles.py — Build Valhalla's walking network from the OSM extract (once per extract).

Writes config.VALHALLA_DIR/valhalla.json (pyvalhalla's default settings, tiles
in config.VALHALLA_DIR/tiles, concurrency config.VALHALLA_WORKERS) and runs
valhalla_build_tiles on config.OSM_PBF with config.VALHALLA_LUA as tag rules:
Valhalla's own graph.lua with cycle paths open to pedestrians (see the head
of that file). Zuid-Holland takes under a minute.

pyvalhalla 3.9.1's "python -m valhalla valhalla_build_tiles" cannot find its
executables on Windows, so the .exe is called directly, with the package's
DLL folder on PATH.

Usage (from the repository root, with the OSGeo4W Python):
    python indicator_300_park/etl/00_valhalla_tiles.py
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402

WRITE_CONFIG = r'''
import json, sys
from valhalla import get_config
tile_dir, lua, workers, out = sys.argv[1:5]
c = get_config(tile_extract="", tile_dir=tile_dir, verbose=True)
c["mjolnir"]["concurrency"] = int(workers)
c["mjolnir"]["graph_lua_name"] = lua
for k in ("timezone", "admin"):
    c["mjolnir"][k] = ""
json.dump(c, open(out, "w"), indent=1)
'''


def main():
    t0 = time.time()
    site = config.VALHALLA_PYTHON.parent.parent / "Lib" / "site-packages"
    exe = site / "valhalla" / "bin" / "valhalla_build_tiles.exe"
    if not exe.exists():
        sys.exit(f"ERROR: {exe} not found; install pyvalhalla in {config.VALHALLA_PYTHON.parent.parent} first")
    tiles = config.VALHALLA_DIR / "tiles"
    if tiles.exists():
        shutil.rmtree(tiles)
    tiles.mkdir(parents=True)
    cfg = config.VALHALLA_DIR / "valhalla.json"
    subprocess.run([str(config.VALHALLA_PYTHON), "-c", WRITE_CONFIG, str(tiles), str(config.VALHALLA_LUA),
                    str(config.VALHALLA_WORKERS), str(cfg)], check=True)
    env = dict(os.environ, PATH=str(site / "pyvalhalla.libs") + os.pathsep + os.environ["PATH"])
    log = config.VALHALLA_DIR / "build.log"
    print(f"Building tiles from {config.OSM_PBF.name} with {config.VALHALLA_LUA.name} (log: {log}) …", flush=True)
    with open(log, "w") as fh:
        subprocess.run([str(exe), "-c", str(cfg), str(config.OSM_PBF)], check=True, env=env, stdout=fh,
                       stderr=subprocess.STDOUT)
    size = sum(f.stat().st_size for f in tiles.rglob("*") if f.is_file()) / 1e6
    print(f"Tiles: {size:.0f} MB in {tiles} ({time.time() - t0:.0f} s)")


if __name__ == "__main__":
    main()
