"""
valhalla_worker.py — Runs Valhalla for every park entrance (called by 02_valhalla.py).

Runs in the pyvalhalla environment (config.VALHALLA_PYTHON), which has no GDAL,
so it only speaks JSON:

  in   a JSON file: {"config": valhalla.json, "max_km": …, "contours": […],
       "workers": n, "entrances": [[ent_id, lon, lat], …]}
  out  edges.json     {"edges": {edge_id: [distance_m, [[lon, lat], …]]},
                      "origins": [[distance_m, [[lon, lat], …]], …]}: every
                      directed street edge reached from any entrance, with
                      the shortest walking distance to its far end (Valhalla
                      "expansion"); origins are the edges the entrances lie
                      on, whose distance only covers the part after the entrance
       isochrones.jsonl  one line per entrance and contour: the isochrone
                      polygon as GeoJSON (the classic method, for comparison)
       homes.json     per home (job "homes": [[idx, lon, lat], …]) the street
                      Valhalla puts it on (locate): [idx, lon, lat of the
                      point on the street, encoded street shape, percent along]

Usage: <valhalla python> valhalla_worker.py job.json out_dir
"""
import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path

NO_EDGE = 70368744177663   # Valhalla's "no edge": the edge has no predecessor, the walk starts on it
_actor = None
_job = None


def _init(job):
    global _actor, _job
    from valhalla import Actor
    _job = job
    _actor = Actor(job["config"])


def _one(entrance):
    """Expansion and isochrones from one entrance."""
    ent_id, lon, lat = entrance
    loc = [{"lat": lat, "lon": lon}]
    edges = {}
    origins = []
    exp = json.loads(_actor.expansion(json.dumps({
        "locations": loc, "costing": "pedestrian", "action": "isochrone",
        "contours": [{"distance": _job["max_km"]}], "skip_opposites": False,
        "expansion_properties": ["distance", "edge_id", "pred_edge_id"]})))
    for f in exp.get("features", []):
        p = f["properties"]
        d = p["distance"]
        if p["pred_edge_id"] == NO_EDGE:
            origins.append((d, f["geometry"]["coordinates"]))
        elif d <= _job["max_km"] * 1000:
            edges[p["edge_id"]] = (d, f["geometry"]["coordinates"])
    iso = json.loads(_actor.isochrone(json.dumps({
        "locations": loc, "costing": "pedestrian", "contours": _job["contours"], "polygons": True})))
    lines = []
    for f in iso.get("features", []):
        f["properties"] = {"ent_id": ent_id, "metric": f["properties"].get("metric"),
                           "contour": f["properties"].get("contour")}
        lines.append(json.dumps(f))
    return edges, origins, lines


def _locate(chunk):
    """Valhalla's own snapping of a batch of homes to its walking network."""
    locs = [{"lon": lon, "lat": lat} for _, lon, lat in chunk]
    res = json.loads(_actor.locate(json.dumps({"locations": locs, "costing": "pedestrian", "verbose": True})))
    out = []
    for (idx, _, _), r in zip(chunk, res):
        edges = r.get("edges") or []
        if edges:
            e = edges[0]
            out.append([idx, e["correlated_lon"], e["correlated_lat"], e["edge_info"]["shape"], e["percent_along"]])
    return out


def main():
    job = json.loads(Path(sys.argv[1]).read_text())
    out = Path(sys.argv[2])
    best = {}
    origins = []
    n = len(job["entrances"])
    t = time.time()
    with open(out / "isochrones.jsonl", "w") as iso_out, \
            Pool(job["workers"], initializer=_init, initargs=(job,)) as pool:
        for i, (edges, starts, lines) in enumerate(pool.imap_unordered(_one, job["entrances"], chunksize=8), 1):
            for eid, (d, coords) in edges.items():
                if eid not in best or d < best[eid][0]:
                    best[eid] = (d, coords)
            origins += starts
            for line in lines:
                iso_out.write(line + "\n")
            if i % 500 == 0 or i == n:
                print(f"  {i:,}/{n:,} entrances, {len(best):,} edges, {time.time() - t:.0f} s", flush=True)
    (out / "edges.json").write_text(json.dumps({"edges": best, "origins": origins}))
    homes = job.get("homes", [])
    chunks = [homes[i:i + 50] for i in range(0, len(homes), 50)]
    located = []
    t = time.time()
    with Pool(job["workers"], initializer=_init, initargs=(job,)) as pool:
        for part in pool.imap_unordered(_locate, chunks, chunksize=4):
            located += part
    print(f"  located {len(located):,}/{len(homes):,} homes on the network, {time.time() - t:.0f} s", flush=True)
    (out / "homes.json").write_text(json.dumps(located))


if __name__ == "__main__":
    main()
