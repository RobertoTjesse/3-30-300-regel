"""
02_valhalla.py — Walking distance from every home to the nearest park entrance, with Valhalla.

Valhalla (pyvalhalla, local, no server) builds its walking network from the
same OSM extract as 01_prepare.py (tiles in config.VALHALLA_DIR, made once with
valhalla_build_tiles). valhalla_worker.py asks Valhalla:

  expansion   for every park entrance, every street Valhalla walks along
              from it, with the walking distance at its far end. From this,
              the shortest walking distance to each junction, over all
              entrances and both directions of every street.
  locate      for every home, the street Valhalla itself puts it on, and how
              far along that street. The home's distance is the shorter way
              round along that street to one of its two junctions, plus that
              junction's distance, plus the straight line from the home to
              the street.

A home takes the shorter of two walks: via the street Valhalla puts it on
(locate), or via the nearest point on any street the expansion reached, plus
the straight line to it (as 03_grass.py does). Each alone goes wrong somewhere:
the expansion never walks into private or destination-only roads, so a home
on one is far from every reached street, while a route to it can use its own
road; and where such a road leads only to more private roads, its junctions
get no distance at all.
  isochrone   the classic method: a polygon around what is
              reachable within 300 m, 5 and 15 minutes from each entrance. A
              home inside the merged polygons counts as reached. Kept for
              comparison: a polygon also covers blocks, water and railway
              land that the walk goes round.

Output config.VALHALLA_GPKG:
  homes       home_id, pand_id, gemeente, vh_m (walking distance, empty when
              beyond config.MAX_DISTANCE_M), vh_snap_m (home to its street),
              vh_iso_300m / vh_iso_5min / vh_iso_15min (1 = inside the isochrones)
  edges       the streets reached, with the walking distance at both junctions
  isochrones  the merged isochrone polygons per contour

Usage (from the repository root, with the OSGeo4W Python):
    python indicator_300_park/etl/02_valhalla.py [--reuse | --parks-only | --homes-only]

  --parks-only   phase 1: expansion and isochrones from the entrances only
  --homes-only   phase 2: Valhalla's snapping of the homes and their distances,
                 reusing the expansion of phase 1
  --reuse        skip Valhalla when its output is newer than 01_prepare.py's
"""
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402

import numpy as np  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402  nearest-point lookup only; the routing is Valhalla's
from sampling import along  # noqa: E402
from osgeo import gdal, ogr, osr  # noqa: E402
gdal.UseExceptions()
ogr.UseExceptions()

RD = osr.SpatialReference()
RD.ImportFromEPSG(28992)
WGS84 = osr.SpatialReference()
WGS84.ImportFromEPSG(4326)
for s in (RD, WGS84):
    s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
TO_WGS = osr.CoordinateTransformation(RD, WGS84)
TO_RD = osr.CoordinateTransformation(WGS84, RD)


def contours():
    """Valhalla contours for config.THRESHOLDS_M: metres as distance, minutes as time."""
    out = []
    for name, metres in config.THRESHOLDS_M.items():
        if name.endswith("min"):
            out.append({"time": int(name[:-3])})
        else:
            out.append({"distance": metres / 1000})
    return out


def wgs(lyr, id_field=None):
    """[[id or fid, lon, lat], …] of a point layer."""
    out = []
    for f in lyr:
        g = f.GetGeometryRef().Clone()
        g.Transform(TO_WGS)
        out.append([f.GetField(id_field) if id_field else f.GetFID(), round(g.GetX(), 7), round(g.GetY(), 7)])
    return out


def run_worker(prepared, work, parks=True, homes=True):
    """Write the job and run valhalla_worker.py in the pyvalhalla Python: the expansion and
    isochrones from the entrances (parks), Valhalla's snapping of the homes (homes), or both."""
    entrances = wgs(prepared.GetLayerByName("entrances"), "ent_id") if parks else []
    homes = wgs(prepared.GetLayerByName("homes")) if homes else []
    job = {"config": str(config.VALHALLA_DIR / "valhalla.json"), "max_km": config.MAX_DISTANCE_M / 1000,
           "contours": contours(), "workers": config.VALHALLA_WORKERS, "entrances": entrances, "homes": homes}
    work.mkdir(parents=True, exist_ok=True)
    (work / "job.json").write_text(json.dumps(job))
    print(f"Valhalla: {len(entrances):,} entrances, {len(homes):,} homes, {config.VALHALLA_WORKERS} workers")
    subprocess.run([str(config.VALHALLA_PYTHON), str(Path(__file__).parent / "valhalla_worker.py"),
                    str(work / "job.json"), str(work)], check=True)


def decode(shape):
    """Valhalla's encoded polyline (precision 6) as [(lon, lat), …]."""
    coords, i, lat, lon = [], 0, 0, 0
    while i < len(shape):
        for k in (0, 1):
            shift = result = 0
            while True:
                b = ord(shape[i]) - 63
                i += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            d = ~(result >> 1) if result & 1 else result >> 1
            if k == 0:
                lat += d
            else:
                lon += d
        coords.append((lon / 1e6, lat / 1e6))
    return coords


def node(lon, lat):
    """A junction's identity: its coordinates as Valhalla gives them (1e-6 degree, about 10 cm)."""
    return round(lon, 6), round(lat, 6)


def street_key(coords):
    """(undirected key, coords in key order) of a Valhalla edge."""
    a, b = node(*coords[0]), node(*coords[-1])
    return ((a, b), coords) if a <= b else ((b, a), coords[::-1])


def rd_line(coords):
    g = ogr.Geometry(ogr.wkbLineString)
    for lon, lat in coords:
        g.AddPoint_2D(lon, lat)
    g.Transform(TO_RD)
    return g


class Walk:
    """The walking distance to every junction, from the expansion of all entrances.

    Valhalla stores every street twice, once per direction, and reports the
    walking distance at the far end of each directed edge; per junction the
    shortest is kept. The street an entrance lies on comes back whole, with
    only the distance from the entrance to its far end; so the entrance sits
    that far before the end, and along that street the distance runs both
    ways from it."""

    def __init__(self, walk):
        self.shapes = {}   # street key: (RD line, length)
        self.best = {}     # junction: shortest walking distance (m)
        self.starts = {}   # street key: positions (m from its first junction) of entrances on it
        for d_end, coords in walk["edges"].values():
            k = self.shape(coords)
            length = self.shapes[k][1]
            a, b = node(*coords[0]), node(*coords[-1])
            self.best[b] = min(self.best.get(b, np.inf), d_end)
            self.best[a] = min(self.best.get(a, np.inf), max(d_end - length, 0.0))
        for d_end, coords in walk["origins"]:
            k = self.shape(coords)
            length = self.shapes[k][1]
            to_end = min(d_end, length)
            s = length - to_end if node(*coords[-1]) == k[1] else to_end
            self.starts.setdefault(k, []).append(s)
            self.best[k[1]] = min(self.best.get(k[1], np.inf), length - s)
            self.best[k[0]] = min(self.best.get(k[0], np.inf), s)

    def shape(self, coords):
        k, ordered = street_key(coords)
        if k not in self.shapes:
            g = rd_line(ordered)
            self.shapes[k] = (g, g.Length())
        return k

    def at(self, k, length, s):
        """Walking distance to the point s metres along street k (from its first junction)."""
        da, db = self.best.get(k[0], np.inf), self.best.get(k[1], np.inf)
        return min([da + s, db + length - s] + [abs(s - e) for e in self.starts.get(k, [])])

    def points(self, step=2.0):
        """Points every step metres along the reached streets, with their walking distance."""
        xs, ys, ds = [], [], []
        for k, (g, _) in self.shapes.items():
            x, y, s, length = along(g, step)
            d = np.minimum(self.best.get(k[0], np.inf) + s, self.best.get(k[1], np.inf) + length - s)
            for e in self.starts.get(k, []):
                d = np.minimum(d, np.abs(s - e))
            ok = np.isfinite(d)
            xs.append(x[ok])
            ys.append(y[ok])
            ds.append(d[ok])
        x, y, d = np.concatenate(xs), np.concatenate(ys), np.concatenate(ds)
        return cKDTree(np.column_stack([x, y])), d

    def write_edges(self, lyr):
        for k, (g, length) in self.shapes.items():
            f = ogr.Feature(lyr.GetLayerDefn())
            for name, d in (("d_start", self.best.get(k[0])), ("d_end", self.best.get(k[1]))):
                if d is not None and np.isfinite(d):
                    f.SetField(name, round(d, 1))
            f.SetGeometry(g)
            lyr.CreateFeature(f)


def polygons_of(g):
    """The polygons in a geometry (a polygon, multipolygon or collection)."""
    t = ogr.GT_Flatten(g.GetGeometryType())
    if t == ogr.wkbPolygon:
        return [g]
    if t in (ogr.wkbMultiPolygon, ogr.wkbGeometryCollection):
        return [p for i in range(g.GetGeometryCount()) for p in polygons_of(g.GetGeometryRef(i))]
    return []


def merged_isochrones(path, out_lyr):
    """{contour name: merged polygon} from isochrones.jsonl; also writes them to out_lyr."""
    by_name = {name: [] for name in config.THRESHOLDS_M}
    names = {("distance" if "distance" in c else "time", c.get("distance") or c.get("time")): n
             for c, n in zip(contours(), config.THRESHOLDS_M)}
    for line in open(path):
        f = json.loads(line)
        p = f["properties"]
        name = names.get((p["metric"], p["contour"]))
        if name:
            g = ogr.CreateGeometryFromJson(json.dumps(f["geometry"]))
            g.Transform(TO_RD)
            by_name[name].append(g if g.IsValid() else g.MakeValid())
    merged = {}
    for name, geoms in by_name.items():
        coll = ogr.Geometry(ogr.wkbMultiPolygon)
        for g in geoms:
            for part in polygons_of(g):
                coll.AddGeometry(part)
        u = coll.UnionCascaded() if coll.GetGeometryCount() else coll
        merged[name] = u
        f = ogr.Feature(out_lyr.GetLayerDefn())
        f.SetField("contour", name)
        f.SetGeometry(ogr.ForceToMultiPolygon(u))
        out_lyr.CreateFeature(f)
    return merged


def main():
    t0 = time.time()
    if not config.PREPARED_GPKG.exists():
        sys.exit(f"ERROR: run 01_prepare.py first ({config.PREPARED_GPKG} is missing)")
    prepared = ogr.Open(str(config.PREPARED_GPKG))
    work = config.WORK_DIR / "valhalla"
    done = work / "homes.json"
    expanded = work / "edges.json"
    parks_only = "--parks-only" in sys.argv
    if "--homes-only" in sys.argv:   # phase 2: the expansion of phase 1 stays, only the homes are added
        if not expanded.exists():
            sys.exit(f"ERROR: {expanded} is missing; run with --parks-only first")
        run_worker(prepared, work, parks=False, homes=True)
    elif "--reuse" in sys.argv and done.exists() and done.stat().st_mtime > config.PREPARED_GPKG.stat().st_mtime:
        print(f"Valhalla: reusing {work}")
    else:
        run_worker(prepared, work, parks=True, homes=not parks_only)
    walk = Walk(json.loads(expanded.read_text()))
    print(f"Streets reached: {len(walk.shapes):,}; junctions with a distance: {len(walk.best):,}")

    if config.VALHALLA_GPKG.exists():
        config.VALHALLA_GPKG.unlink()
    out = ogr.GetDriverByName("GPKG").CreateDataSource(str(config.VALHALLA_GPKG))
    el = out.CreateLayer("edges", RD, ogr.wkbLineString)
    for name in ("d_start", "d_end"):
        el.CreateField(ogr.FieldDefn(name, ogr.OFTReal))
    out.StartTransaction()
    walk.write_edges(el)
    out.CommitTransaction()

    il = out.CreateLayer("isochrones", RD, ogr.wkbMultiPolygon)
    il.CreateField(ogr.FieldDefn("contour", ogr.OFTString))
    out.StartTransaction()
    merged = merged_isochrones(work / "isochrones.jsonl", il)
    out.CommitTransaction()
    for name, geom in merged.items():
        print(f"  isochrones {name:>5}: {geom.GetArea() / 1e6:,.1f} km2")
    if parks_only:
        print(f"Phase 1 done (parks only): wrote edges and isochrones to {config.VALHALLA_GPKG} "
              f"in {time.time() - t0:.0f} s. The homes follow with --homes-only.")
        return
    located = {r[0]: r for r in json.loads(done.read_text())}
    print(f"Homes located on the network: {len(located):,}")

    tree, tree_d = walk.points()
    homes = prepared.GetLayerByName("homes")
    inside = {name: set() for name in config.THRESHOLDS_M}
    for name, geom in merged.items():   # which homes lie inside the merged isochrones
        for part in polygons_of(geom):
            homes.SetSpatialFilter(part)
            inside[name].update(f.GetFID() for f in homes)
    homes.SetSpatialFilter(None)

    hl = out.CreateLayer("homes", RD, ogr.wkbPoint)
    fields = [("home_id", ogr.OFTString), ("pand_id", ogr.OFTString), ("gemeente", ogr.OFTString),
              ("vh_m", ogr.OFTReal), ("vh_snap_m", ogr.OFTReal)]
    fields += [(f"vh_iso_{name}", ogr.OFTInteger) for name in config.THRESHOLDS_M]
    for name, t in fields:
        hl.CreateField(ogr.FieldDefn(name, t))
    walks, n, no_street = [], 0, 0
    out.StartTransaction()
    for f in homes:
        n += 1
        fid = f.GetFID()
        g = f.GetGeometryRef()
        nf = ogr.Feature(hl.GetLayerDefn())
        for name in ("home_id", "pand_id", "gemeente"):
            nf.SetField(name, f.GetField(name))
        d = np.nan
        snap_near, i = tree.query((g.GetX(), g.GetY()), distance_upper_bound=config.HOME_SNAP_M)
        if np.isfinite(snap_near):
            d = tree_d[i] + snap_near
        loc = located.get(fid)
        if loc:
            _, lon, lat, shape, pct = loc
            coords = decode(shape)
            k, ordered = street_key(coords)
            line = rd_line(ordered)
            length = line.Length()
            s = pct * length if node(*ordered[0]) == node(*coords[0]) else (1 - pct) * length
            sx, sy, _ = TO_RD.TransformPoint(lon, lat)
            snap = float(np.hypot(sx - g.GetX(), sy - g.GetY()))
            via_own = walk.at(k, length, s) + snap
            if not np.isfinite(d) or via_own < d:
                d = via_own
                nf.SetField("vh_snap_m", round(snap, 1))
            else:
                nf.SetField("vh_snap_m", round(float(snap_near), 1))
        else:
            no_street += 1
        if np.isfinite(d) and d <= config.MAX_DISTANCE_M:
            nf.SetField("vh_m", round(float(d), 1))
            walks.append(d)
        for name in config.THRESHOLDS_M:
            nf.SetField(f"vh_iso_{name}", int(fid in inside[name]))
        nf.SetGeometry(g.Clone())
        hl.CreateFeature(nf)
    out.CommitTransaction()

    walks = np.array(walks)
    for name, m in config.THRESHOLDS_M.items():
        print(f"  within {name:>5} ({m:>4} m): expansion {np.sum(walks <= m) / n:6.1%}   "
              f"isochrone {len(inside[name]) / n:6.1%}")
    print(f"Homes: {n:,}; not located on a street: {no_street:,}")
    print(f"Wrote {config.VALHALLA_GPKG} in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
