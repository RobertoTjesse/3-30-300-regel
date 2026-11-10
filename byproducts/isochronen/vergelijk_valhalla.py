"""
vergelijk_valhalla.py — Where may you go: Valhalla against isochronen_osm.py, per profile.

Both read the same OSM extract. For every OSM way this compares whether a
profile may use it, and in which direction. Profiles: lopen (pedestrian),
fiets (bicycle), auto, vracht (truck), motor (motorcycle), bus.

  ours      rule() of isochronen_osm.py on the way's tags
  valhalla  the profile's access bits of Valhalla's graph tiles (built by
            indicator_300_park/etl/00_valhalla_tiles.py), read straight from
            the .gph files: pyvalhalla gives the shape of an edge but not its
            access. The layout (48-byte directed edges after the nodes and
            transitions, the edge-info section at header byte 104, the way id
            as the first 4 bytes of an edge's info) was checked against the
            edge lengths and against way ids from Valhalla's own locate.

Output (EPSG:28992), default data/processed/<profile>_valhalla_vs_ons.gpkg:
  valhalla_<mode> Valhalla edges the mode may use: way_id, fwd, bwd (1 = may
                  ride in / against the way's drawing direction), use
                  (Valhalla's road use), m
  ons_<mode>      OSM ways rule() allows (as Valhalla, private ways closed): way_id, highway, fwd, bwd, m
  verschillen     OSM ways where the two disagree: verschil (prive = private, closed on purpose; alleen_valhalla,
                  alleen_ons, richting), the tags that decide it, m

Usage (from the repository root, with the OSGeo4W Python; pyvalhalla from
the venv of indicator_300_park/README.md):
    python byproducts/isochronen/vergelijk_valhalla.py lopen|fiets|auto|vracht|motor|bus [out.gpkg]
"""
import ast
import collections
import glob
import json
import os
import re
import struct
import sys
import time
from pathlib import Path

from osgeo import gdal, ogr, osr

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "indicator_300_park" / "etl"))
import config  # noqa: E402

sys.path.insert(0, str(Path(os.environ.get("USERPROFILE", "~")) / "valhalla-venv" / "Lib" / "site-packages"))
from valhalla import _valhalla as V  # noqa: E402

ogr.UseExceptions()
PBF = config.OSM_PBF
VALHALLA = ROOT / "data" / "interim" / "valhalla"
# profile: (rule() mode, the profile's own OSM tag, Valhalla access bit: baldr/graphconstants.h kXxxAccess)
PROFILES = {"lopen": ("Walking", "foot", 2), "fiets": ("Cycling", "bicycle", 4), "auto": ("Car", "motorcar", 1),
            "vracht": ("Truck", "hgv", 8), "motor": ("Motorcycle", "motorcycle", 1024), "bus": ("Bus", "bus", 64)}
MODE = sys.argv[1] if len(sys.argv) > 1 else "fiets"
if MODE == "stats":            # stats <pbf> <valhalla dir>: all profiles at once, numbers only
    if len(sys.argv) > 2:
        PBF = Path(sys.argv[2])
    if len(sys.argv) > 3:
        VALHALLA = Path(sys.argv[3])
    RULE_MODE, TAG, ACCESS_BIT, MOTOR = None, None, None, None
elif MODE not in PROFILES:
    sys.exit("usage: vergelijk_valhalla.py " + "|".join(PROFILES) + " [out.gpkg]\n"
             "       vergelijk_valhalla.py stats [pbf] [valhalla dir]")
else:
    RULE_MODE, TAG, ACCESS_BIT = PROFILES[MODE]
    MOTOR = RULE_MODE not in ("Walking", "Cycling")
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 and MODE != "stats" else \
    ROOT / "data" / "processed" / f"{MODE}_valhalla_vs_ons.gpkg"
EDGE_BYTES, NODE_BYTES, TRANSITION_BYTES = 48, 32, 8
EDGEINFO_OFFSET_AT = 104       # header byte that holds the edge-info section's offset
TAGS_SHOWN = ("highway", "bicycle", "foot", "motor_vehicle", "motorcar", "hgv", "motorcycle", "bus", "psv",
              "access", "vehicle", "motorroad", "junction", "oneway",
              "oneway:bicycle", "oneway:foot",
              "cycleway", "cycleway:left", "cycleway:right", "area")
USE = {0: "road", 1: "ramp", 2: "turn_channel", 3: "track", 4: "driveway", 5: "alley", 6: "parking_aisle",
       7: "emergency_access", 8: "drive_through", 9: "culdesac", 10: "living_street", 11: "service_road",
       20: "cycleway", 21: "mountain_bike", 24: "sidewalk", 25: "footway", 26: "elevator", 27: "steps",
       28: "escalator", 29: "path", 30: "pedestrian", 31: "bridleway", 32: "pedestrian_crossing",
       40: "other", 41: "ferry", 42: "rail_ferry", 43: "construction"}


def load_rule():
    """rule() and what it needs from isochronen_osm.py, without importing QGIS."""
    src = (Path(__file__).parent / "isochronen_osm.py").read_text(encoding="utf-8")
    keep = [n for n in ast.parse(src).body if isinstance(n, (ast.Assign, ast.FunctionDef))]
    ns = {"re": re, "os": os}
    exec(compile(ast.Module(keep, []), "isochronen_osm.py", "exec"), ns)
    return ns["rule"]


def valhalla_edges():
    """{way_id: [(fwd, bwd, use, shape)]} for every non-shortcut edge pair, in the way's direction."""
    cfg = json.loads((VALHALLA / "valhalla.json").read_text())
    gu = V._GraphUtils(json.dumps(cfg))
    tile_dir = cfg["mjolnir"]["tile_dir"]
    ways = collections.defaultdict(list)
    shortcuts = 0
    for path in sorted(glob.glob(os.path.join(tile_dir, "**", "*.gph"), recursive=True)):
        data = open(path, "rb").read()
        h = V.GraphTileHeader.from_bytes(data[:V.GraphTileHeader.byte_size()])
        if not h.directededgecount:
            continue
        gid = V.GraphId.from_tile_path(os.path.relpath(path, tile_dir).replace(os.sep, "/"))
        start = V.GraphTileHeader.byte_size() + NODE_BYTES * h.nodecount + TRANSITION_BYTES * h.transitioncount
        info = struct.unpack_from("<I", data, EDGEINFO_OFFSET_AT)[0]
        for i in range(h.directededgecount):
            w = struct.unpack_from("<6Q", data, start + EDGE_BYTES * i)
            if (w[5] >> 60) & 1:                     # shortcut (levels 0 and 1): a copy of other edges
                shortcuts += 1
                continue
            if not (w[0] >> 61) & 1:                 # keep one edge of each pair: the one along the way
                continue
            fwd, bwd = bool(w[3] & ACCESS_BIT), bool((w[3] >> 12) & ACCESS_BIT)
            way_id = struct.unpack_from("<I", data, info + (w[1] & 0x1FFFFFF))[0]
            use = (w[2] >> 40) & 0x3F
            ways[way_id].append((fwd, bwd, use, gu.get_edge_shape(gid + i)))
    return ways, shortcuts


def osm_ways():
    """{way_id: (tags, geometry WGS84)} for every OSM way with a highway tag."""
    tag = re.compile(r'"([^"]+)"=>"((?:[^"\\]|\\.)*)"')
    cache = ROOT / "data" / "interim" / "osm" / (PBF.name.split(".")[0] + "_highways.gpkg")
    if not cache.exists() or cache.stat().st_mtime < PBF.stat().st_mtime:
        gdal.VectorTranslate(str(cache), str(PBF), format="GPKG", layers=["lines"], where="highway IS NOT NULL")
    ds = ogr.Open(str(cache))
    lyr = ds.GetLayer(0)
    out = {}
    for f in lyr:
        tags = dict(tag.findall(f.GetField("other_tags") or ""))
        for i in range(f.GetFieldCount()):
            name = f.GetFieldDefnRef(i).GetName()
            if name not in ("other_tags", "osm_id") and f.IsFieldSet(i):
                tags[name] = f.GetField(i)
        out[int(f.GetField("osm_id"))] = (tags, f.GetGeometryRef().Clone())
    return out


def is_private(tags, mode, tag):
    keys = ("access", tag) + (("vehicle", "motor_vehicle") if mode not in ("Walking", "Cycling") else ())
    return any("private" in (tags.get(k) or "").split(";") for k in keys)


def main_stats():
    """Agreement per profile for a whole extract: ways and km per kind, no geometry kept.
    Valhalla's side per way: of its edges' own lengths, the share open forward / backward (> half = open)."""
    t0 = time.time()
    rule = load_rule()
    cfg = json.loads((VALHALLA / "valhalla.json").read_text())
    tile_dir = cfg["mjolnir"]["tile_dir"]
    names = list(PROFILES)
    bits = [PROFILES[p][2] for p in names]
    vw = {}   # way_id -> [total m, fwd m per profile..., bwd m per profile...]
    print(f"Reading Valhalla tiles in {tile_dir} ...", flush=True)
    for path in sorted(glob.glob(os.path.join(tile_dir, "**", "*.gph"), recursive=True)):
        data = open(path, "rb").read()
        h = V.GraphTileHeader.from_bytes(data[:V.GraphTileHeader.byte_size()])
        if not h.directededgecount:
            continue
        start = V.GraphTileHeader.byte_size() + NODE_BYTES * h.nodecount + TRANSITION_BYTES * h.transitioncount
        info = struct.unpack_from("<I", data, EDGEINFO_OFFSET_AT)[0]
        for i in range(h.directededgecount):
            w = struct.unpack_from("<6Q", data, start + EDGE_BYTES * i)
            if (w[5] >> 60) & 1 or not (w[0] >> 61) & 1:
                continue
            way_id = struct.unpack_from("<I", data, info + (w[1] & 0x1FFFFFF))[0]
            length = (w[4] >> 32) & 0xFFFFFF
            a = vw.get(way_id)
            if a is None:
                a = vw[way_id] = [0] * (1 + 2 * len(bits))
            a[0] += length
            fa, ra = w[3] & 0xFFF, (w[3] >> 12) & 0xFFF
            for k, b in enumerate(bits):
                if fa & b:
                    a[1 + k] += length
                if ra & b:
                    a[1 + len(bits) + k] += length
    print(f"  {len(vw):,} ways, {time.time() - t0:.0f} s", flush=True)

    tag_re = re.compile(r'"([^"]+)"=>"((?:[^"\\]|\\.)*)"')
    cache = ROOT / "data" / "interim" / "osm" / (PBF.name.split(".")[0] + "_highways.gpkg")
    if not cache.exists() or cache.stat().st_mtime < PBF.stat().st_mtime:
        print(f"Extracting highways from {PBF.name} ...", flush=True)
        gdal.VectorTranslate(str(cache), str(PBF), format="GPKG", layers=["lines"], where="highway IS NOT NULL")
    wgs = osr.SpatialReference()
    wgs.ImportFromEPSG(4326)
    wgs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    rd = osr.SpatialReference()
    rd.ImportFromEPSG(28992)
    to_rd = osr.CoordinateTransformation(wgs, rd)
    stats = {p: collections.Counter() for p in names}
    km = {p: collections.Counter() for p in names}
    ds = ogr.Open(str(cache))
    n = 0
    import csv
    diff_path = ROOT / "data" / "processed" / f"valhalla_vs_ons_{PBF.name.split('.')[0]}_verschillen.csv"
    diff_fh = open(diff_path, "w", newline="", encoding="utf-8")
    diffs = csv.writer(diff_fh)   # every way that differs (private ways left out): profile, kind, tags
    diffs.writerow(["profiel", "verschil", "way_id", "m", "ons_heen/terug", "valhalla_heen/terug"] + list(TAGS_SHOWN))
    for f in ds.GetLayer(0):
        tags = dict(tag_re.findall(f.GetField("other_tags") or ""))
        for i in range(f.GetFieldCount()):
            name = f.GetFieldDefnRef(i).GetName()
            if name not in ("other_tags", "osm_id") and f.IsFieldSet(i):
                tags[name] = f.GetField(i)
        g = f.GetGeometryRef()
        if g.GetPointCount() > 2 and g.GetPoint_2D(0) == g.GetPoint_2D(g.GetPointCount() - 1):
            tags["_closed"] = "yes"   # as in isochronen_osm.py network()
        g.Transform(to_rd)
        m = g.Length() / 1000
        a = vw.get(int(f.GetField("osm_id")))
        for k, p in enumerate(names):
            mode, tag, _ = PROFILES[p]
            r = rule(tags, mode, 15.0, 5.0)
            ours = (bool(r[0]), bool(r[1])) if r else (False, False)
            theirs = (a[1 + k] * 2 > a[0], a[1 + len(bits) + k] * 2 > a[0]) if a and a[0] else (False, False)
            if ours == theirs:
                kind = "gelijk"
            elif not any(theirs):
                kind = "alleen_ons"
            elif not any(ours):
                kind = "prive" if is_private(tags, mode, tag) else "alleen_valhalla"
            else:
                kind = "richting"
            stats[p][kind] += 1
            km[p][kind] += m
            if kind not in ("gelijk", "prive"):
                diffs.writerow([p, kind, f.GetField("osm_id"), round(m * 1000, 1),
                                "/".join("ja" if x else "nee" for x in ours),
                                "/".join("ja" if x else "nee" for x in theirs)]
                               + [tags.get(t, "") for t in TAGS_SHOWN])
        n += 1
        if n % 1_000_000 == 0:
            print(f"  {n:,} OSM ways, {time.time() - t0:.0f} s", flush=True)
    diff_fh.close()
    kinds = ("gelijk", "prive", "alleen_valhalla", "alleen_ons", "richting")
    out = ROOT / "data" / "processed" / f"valhalla_vs_ons_{PBF.name.split('.')[0]}.csv"
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("profiel," + ",".join(f"{k}_ways,{k}_km" for k in kinds) + "\n")
        for p in names:
            fh.write(p + "," + ",".join(f"{stats[p][k]},{km[p][k]:.1f}" for k in kinds) + "\n")
    print(f"\n{n:,} OSM ways with a highway tag, {time.time() - t0:.0f} s; written to {out}")
    for p in names:
        print(f"  {p:7s} " + "  ".join(f"{k} {km[p][k]:,.1f} km ({stats[p][k]:,})" for k in kinds))


def layer(ds, name, gtype, fields):
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(28992)
    lyr = ds.CreateLayer(name, srs, gtype)
    for fname, ftype in fields:
        lyr.CreateField(ogr.FieldDefn(fname, ftype))
    return lyr


def main():
    t0 = time.time()
    rule = load_rule()
    print("Reading Valhalla tiles ...", flush=True)
    vw, shortcuts = valhalla_edges()
    print(f"  {sum(map(len, vw.values())):,} edges on {len(vw):,} ways ({shortcuts:,} shortcuts skipped), "
          f"{time.time() - t0:.0f} s", flush=True)
    print(f"Reading OSM ways from {PBF.name} ...", flush=True)
    ow = osm_ways()
    print(f"  {len(ow):,} ways, {time.time() - t0:.0f} s", flush=True)

    wgs = osr.SpatialReference()
    wgs.ImportFromEPSG(4326)
    wgs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    rd = osr.SpatialReference()
    rd.ImportFromEPSG(28992)
    to_rd = osr.CoordinateTransformation(wgs, rd)

    if OUT.exists():
        OUT.unlink()
    ds = ogr.GetDriverByName("GPKG").CreateDataSource(str(OUT))
    I, S, R = ogr.OFTInteger64, ogr.OFTString, ogr.OFTReal
    lv = layer(ds, f"valhalla_{MODE}", ogr.wkbLineString,
               [("way_id", I), ("fwd", ogr.OFTInteger), ("bwd", ogr.OFTInteger), ("use", S), ("m", R)])
    lo = layer(ds, f"ons_{MODE}", ogr.wkbLineString,
               [("way_id", I), ("highway", S), ("fwd", ogr.OFTInteger), ("bwd", ogr.OFTInteger), ("m", R)])
    ld = layer(ds, "verschillen", ogr.wkbLineString,
               [("way_id", I), ("verschil", S), ("ons", S), ("valhalla", S), ("valhalla_use", S), ("m", R)]
               + [(t.replace(":", "_"), S) for t in TAGS_SHOWN])

    ds.StartTransaction()
    for way_id, edges in vw.items():
        for fwd, bwd, use, shape in edges:
            if not (fwd or bwd) or len(shape) < 2:
                continue
            g = ogr.Geometry(ogr.wkbLineString)
            for x, y in shape:
                g.AddPoint_2D(x, y)
            g.Transform(to_rd)
            f = ogr.Feature(lv.GetLayerDefn())
            f.SetField("way_id", way_id)
            f.SetField("fwd", int(fwd))
            f.SetField("bwd", int(bwd))
            f.SetField("use", USE.get(use, str(use)))
            f.SetField("m", round(g.Length(), 1))
            f.SetGeometry(g)
            lv.CreateFeature(f)
    ds.CommitTransaction()
    print(f"valhalla_{MODE} written, {time.time() - t0:.0f} s", flush=True)

    def label(fwd, bwd):
        return "beide" if fwd and bwd else "heen" if fwd else "terug" if bwd else "nee"

    stats = collections.Counter()
    km = collections.Counter()
    by_hw = collections.Counter()
    ds.StartTransaction()
    for way_id, (tags, geom) in ow.items():
        g = geom.Clone()
        if g.GetPointCount() > 2 and g.GetPoint_2D(0) == g.GetPoint_2D(g.GetPointCount() - 1):
            tags["_closed"] = "yes"
        g.Transform(to_rd)
        m = g.Length()
        r = rule(tags, RULE_MODE, 15.0, 5.0)
        ours = (bool(r[0]), bool(r[1])) if r else (False, False)
        if any(ours):
            f = ogr.Feature(lo.GetLayerDefn())
            f.SetField("way_id", way_id)
            f.SetField("highway", tags.get("highway"))
            f.SetField("fwd", int(ours[0]))
            f.SetField("bwd", int(ours[1]))
            f.SetField("m", round(m, 1))
            f.SetGeometry(g)
            lo.CreateFeature(f)
        edges = vw.get(way_id, [])
        if edges:   # by length: a way can be cut into several edges
            lens = [len(e[3]) for e in edges]
            vf = sum(n for e, n in zip(edges, lens) if e[0]) * 2 > sum(lens)
            vb = sum(n for e, n in zip(edges, lens) if e[1]) * 2 > sum(lens)
            use = collections.Counter(e[2] for e in edges).most_common(1)[0][0]
        else:
            vf = vb = False
            use = None
        theirs = (vf, vb)
        if ours == theirs:
            kind = "gelijk"
        elif not any(theirs):
            kind = "alleen_ons"
        elif not any(ours):   # private ways: closed on purpose in isochronen_osm.py, open in Valhalla
            keys = ("access", TAG) + (("vehicle", "motor_vehicle") if MOTOR else ())
            private = any("private" in (tags.get(k) or "").split(";") for k in keys)
            kind = "prive" if private else "alleen_valhalla"
        else:
            kind = "richting"
        stats[kind] += 1
        km[kind] += m / 1000
        if kind == "gelijk":
            continue
        by_hw[(kind, tags.get("highway"))] += m / 1000
        f = ogr.Feature(ld.GetLayerDefn())
        f.SetField("way_id", way_id)
        f.SetField("verschil", kind)
        f.SetField("ons", label(*ours))
        f.SetField("valhalla", label(*theirs) if edges else "geen edge")
        if use is not None:
            f.SetField("valhalla_use", USE.get(use, str(use)))
        f.SetField("m", round(m, 1))
        for t in TAGS_SHOWN:
            if t in tags:
                f.SetField(t.replace(":", "_"), tags[t])
        f.SetGeometry(g)
        ld.CreateFeature(f)
    ds.CommitTransaction()

    print(f"\nWrote {OUT} in {time.time() - t0:.0f} s")
    for k in ("gelijk", "prive", "alleen_valhalla", "alleen_ons", "richting"):
        print(f"  {k:16s} {stats[k]:>9,} ways  {km[k]:>9,.1f} km")
    for kind in ("alleen_valhalla", "alleen_ons", "richting"):
        top = sorted(((v, hw) for (k, hw), v in by_hw.items() if k == kind), reverse=True)[:8]
        print(f"  {kind}: " + ", ".join(f"{hw} {v:,.0f} km" for v, hw in top))


if __name__ == "__main__":
    main_stats() if MODE == "stats" else main()
