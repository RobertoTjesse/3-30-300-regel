"""
03_grass.py — Walking distance from every home to the nearest park entrance, with GRASS.

The second engine, to compare with Valhalla (02_valhalla.py). It uses the
network of 01_prepare.py: the walkable OSM ways, cut at every shared OSM node,
so junctions are where OSM says they are (a path on a bridge does not meet the
street below it).

In a temporary GRASS project (EPSG:28992):
  v.in.ogr          network and entrances from config.PREPARED_GPKG
  v.net connect     links every entrance to the nearest way (within
                    config.EDGE_STREET_M + 5 m) with a straight connector
  v.net nodes       puts a point on every junction and dead end
  v.net.distance    for every junction, the shortest walk over the network to
                    any entrance (Dijkstra from all entrances at once,
                    cost = length in metres, both directions)
  v.out.ogr         the network as GRASS cut it (at the entrances too)

Then per home, in Python: the nearest point on the network (within
config.HOME_SNAP_M), and from there the shorter way to either end of that
segment, plus the straight line from the home to the network. Linking every
home in GRASS as well (v.net connect) gives the same answer, but took 20
minutes for Delft alone.

Output config.GRASS_GPKG, layer homes: home_id, pand_id, gemeente, gr_m (walking
distance, empty beyond config.MAX_DISTANCE_M), gr_snap_m, gr_ent (ent_id of
the nearest entrance).

Usage (from the repository root, with the OSGeo4W Python):
    python indicator_300_park/etl/03_grass.py [--reuse | --parks-only | --homes-only]

  --parks-only   phase 1: GRASS only (walking distance to every junction)
  --homes-only   phase 2: the homes, reusing the GRASS result of phase 1
"""
import csv
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402

import numpy as np  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402  nearest-point lookup only; the routing is GRASS's
from osgeo import gdal, ogr, osr  # noqa: E402
gdal.UseExceptions()
ogr.UseExceptions()

STEP_M = 2.0     # spacing of the points along the network, for the nearest-point lookup

# Runs inside GRASS (grass84 --exec python …).
GRASS_JOB = r'''
import sys
import grass.script as gs

gpkg, work, ent_snap, where = sys.argv[1:5]
where = "" if where == "-" else where
gs.run_command("v.in.ogr", input=gpkg, layer="network", output="net", flags="o", quiet=True,
               **({"where": where} if where else {}))
gs.run_command("v.in.ogr", input=gpkg, layer="entrances", output="ent", flags="o", quiet=True)
print(gs.read_command("v.info", map="net", flags="t").replace("\n", " "), flush=True)
gs.run_command("v.net", input="net", points="ent", output="net_e", operation="connect",
               threshold=ent_snap, arc_layer=1, node_layer=2, quiet=True)
gs.run_command("v.net", input="net_e", output="net_n", operation="nodes", node_layer=3, flags="c", quiet=True)
gs.run_command("v.net.distance", input="net_n", output="dist", from_layer=3, to_layer=2, arc_layer=1, quiet=True)
gs.run_command("v.db.select", map="dist", columns="cat,tcat,dist", format="csv", separator="comma",
               file=work + "/node_distance.csv", overwrite=True, quiet=True)
gs.run_command("v.out.ascii", input="net_n", layer=3, type="point", separator="comma",
               output=work + "/nodes.csv", overwrite=True, quiet=True)
gs.run_command("v.out.ascii", input="net_n", layer=2, type="point", separator="comma",
               output=work + "/entrance_nodes.csv", overwrite=True, quiet=True)
gs.run_command("v.out.ogr", input="net_n", layer=1, type="line", output=work + "/arcs.gpkg",
               format="GPKG", flags="c", overwrite=True, quiet=True)
'''


def key(x, y):
    """A node's identity: its coordinates to the millimetre."""
    return round(x, 3), round(y, 3)


def exclude_where():
    """SQL filter for v.in.ogr that drops config.GRASS_EXCLUDE_HIGHWAYS ('' = keep all)."""
    if not config.GRASS_EXCLUDE_HIGHWAYS:
        return ""
    return "highway NOT IN (" + ",".join(f"'{h}'" for h in config.GRASS_EXCLUDE_HIGHWAYS) + ")"


def run_grass(work):
    job = work / "grass_job.py"
    job.write_text(GRASS_JOB)
    for name in ("node_distance.csv", "nodes.csv", "entrance_nodes.csv", "arcs.gpkg"):
        if (work / name).exists():
            (work / name).unlink()
    print("GRASS: v.in.ogr, v.net connect, v.net nodes, v.net.distance …", flush=True)
    env = dict(os.environ, GRASS_MESSAGE_FORMAT="plain")
    cmd = ["cmd", "/c", str(config.GRASS_BAT), "--tmp-project", "EPSG:28992", "--exec", "python", str(job),
           str(config.PREPARED_GPKG), work.as_posix(), str(config.EDGE_STREET_M + 5), exclude_where() or "-"]
    subprocess.run(cmd, check=True, env=env, stdin=subprocess.DEVNULL)
    if not (work / "arcs.gpkg").exists():
        sys.exit("ERROR: GRASS did not finish; see the messages above")


def node_distances(work, prepared):
    """{node key: (walking distance m, ent_id)} for every junction, and the entrances themselves (0 m)."""
    prepared_ent = {f.GetFID(): f.GetField("ent_id") for f in prepared.GetLayerByName("entrances")}
    best = {}
    with open(work / "node_distance.csv", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["tcat"] and row["dist"] and float(row["dist"]) >= 0:
                best[int(row["cat"])] = (float(row["dist"]), prepared_ent.get(int(row["tcat"])))
    out = {}
    with open(work / "nodes.csv", newline="") as fh:
        for x, y, cat in csv.reader(fh):
            if int(cat) in best:
                out[key(float(x), float(y))] = best[int(cat)]
    with open(work / "entrance_nodes.csv", newline="") as fh:
        for x, y, cat in csv.reader(fh):
            out[key(float(x), float(y))] = (0.0, prepared_ent.get(int(cat)))
    return out


def network_points(work, nodes):
    """Points every STEP_M along the GRASS network, each with the walking distance and
    entrance of the shorter way to either end of its arc: arrays (x, y, d, ent)."""
    xs, ys, ds, es = [], [], [], []
    ds_arcs = ogr.Open(str(work / "arcs.gpkg"))
    for f in ds_arcs.GetLayer(0):
        g = f.GetGeometryRef()
        n = g.GetPointCount()
        if n < 2:
            continue
        a = nodes.get(key(g.GetX(0), g.GetY(0)))
        b = nodes.get(key(g.GetX(n - 1), g.GetY(n - 1)))
        if a is None and b is None:
            continue   # an arc that reaches no entrance
        length = g.Length()
        steps = max(int(length // STEP_M), 1)
        for i in range(steps + 1):
            s = length * i / steps
            via_a = a[0] + s if a else np.inf
            via_b = b[0] + length - s if b else np.inf
            p = g.Value(s)
            xs.append(p.GetX())
            ys.append(p.GetY())
            if via_a <= via_b:
                ds.append(via_a)
                es.append(a[1])
            else:
                ds.append(via_b)
                es.append(b[1])
    return np.array(xs), np.array(ys), np.array(ds), np.array(es)


def main():
    t0 = time.time()
    if not config.PREPARED_GPKG.exists():
        sys.exit(f"ERROR: run 01_prepare.py first ({config.PREPARED_GPKG} is missing)")
    work = config.WORK_DIR / f"grass{config.VARIANT}"
    work.mkdir(parents=True, exist_ok=True)
    done = work / "arcs.gpkg"
    reuse = "--reuse" in sys.argv or "--homes-only" in sys.argv
    if reuse and done.exists() and done.stat().st_mtime > config.PREPARED_GPKG.stat().st_mtime:
        print(f"GRASS: reusing {work}")
    elif "--homes-only" in sys.argv:
        sys.exit(f"ERROR: no GRASS result in {work} newer than {config.PREPARED_GPKG.name}; run with --parks-only first")
    else:
        run_grass(work)
        print(f"GRASS done in {time.time() - t0:.0f} s")

    prepared = ogr.Open(str(config.PREPARED_GPKG))
    nodes = node_distances(work, prepared)
    if "--parks-only" in sys.argv:
        print(f"Phase 1 done (parks only): walking distance to {len(nodes):,} junctions in {work}. "
              f"The homes follow with --homes-only.")
        return
    x, y, d, e = network_points(work, nodes)
    print(f"Junctions with a distance: {len(nodes):,}; network points every {STEP_M:g} m: {len(d):,}")
    tree = cKDTree(np.column_stack([x, y]))

    homes = prepared.GetLayerByName("homes")
    rows = [(f.GetField("home_id"), f.GetField("pand_id"), f.GetField("gemeente"),
             f.GetGeometryRef().GetX(), f.GetGeometryRef().GetY()) for f in homes]
    snap, idx = tree.query(np.array([(r[3], r[4]) for r in rows]), distance_upper_bound=config.HOME_SNAP_M)
    found = np.isfinite(snap)
    walk = np.full(len(rows), np.nan)
    walk[found] = d[idx[found]] + snap[found]
    walk[walk > config.MAX_DISTANCE_M] = np.nan

    if config.GRASS_GPKG.exists():
        config.GRASS_GPKG.unlink()
    rd = osr.SpatialReference()
    rd.ImportFromEPSG(28992)
    out = ogr.GetDriverByName("GPKG").CreateDataSource(str(config.GRASS_GPKG))
    hl = out.CreateLayer("homes", rd, ogr.wkbPoint)
    for name, t in (("home_id", ogr.OFTString), ("pand_id", ogr.OFTString), ("gemeente", ogr.OFTString),
                    ("gr_m", ogr.OFTReal), ("gr_snap_m", ogr.OFTReal), ("gr_ent", ogr.OFTInteger)):
        hl.CreateField(ogr.FieldDefn(name, t))
    out.StartTransaction()
    for i, (hid, pid, gem, px, py) in enumerate(rows):
        f = ogr.Feature(hl.GetLayerDefn())
        f.SetField("home_id", hid)
        f.SetField("pand_id", pid)
        f.SetField("gemeente", gem)
        if found[i]:
            f.SetField("gr_snap_m", round(float(snap[i]), 1))
        if np.isfinite(walk[i]):
            f.SetField("gr_m", round(float(walk[i]), 1))
            if e[idx[i]] is not None:
                f.SetField("gr_ent", int(e[idx[i]]))
        g = ogr.Geometry(ogr.wkbPoint)
        g.AddPoint_2D(px, py)
        f.SetGeometry(g)
        hl.CreateFeature(f)
    out.CommitTransaction()

    n = len(rows)
    for name, m in config.THRESHOLDS_M.items():
        print(f"  within {name:>5} ({m:>4} m): {int(np.sum(walk <= m)) / n:6.1%}")
    print(f"Homes: {n:,}; no walkable way within {config.HOME_SNAP_M:g} m: {int(np.sum(~found)):,}")
    print(f"Wrote {config.GRASS_GPKG} in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
