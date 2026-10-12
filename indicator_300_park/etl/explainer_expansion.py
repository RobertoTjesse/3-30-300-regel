"""
explainer_expansion.py — Pictures that explain how the 300 measures walking distance.

From one starting point (default: the Provinciehuis Zuid-Holland, Zuid-
Hollandplein 1, The Hague) Valhalla walks out over the OSM network:

  300-expansion.png   every street and path reached within 15 minutes,
                      coloured by walking distance (Valhalla "expansion")
  300-isochroon.png   the same walk up to 5 minutes (425 m), with the 5-minute
                      isochrone polygon over it: the polygon also covers blocks
                      and water the walk never reaches

Background: BAG buildings (the 3's province_buildings.gpkg), OSM water and the
parks of 01_prepare.py's source. Written to data/processed/explainer/; copy
them to web/uitleg/img/ and the wiki's images/ folder.

Usage (from the repository root, with the OSGeo4W Python):
    python indicator_300_park/etl/explainer_expansion.py [--lon 4.31707 --lat 52.08710 --name Provinciehuis]
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.collections import LineCollection, PolyCollection  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from osgeo import gdal, ogr, osr  # noqa: E402
gdal.UseExceptions()
ogr.UseExceptions()

OUT_DIR = config.PROCESSED_DIR / "explainer"
BUILDINGS = config.INTERIM_DIR / "province_buildings.gpkg"
WATER = config.FME_INPUT_DIR / "localeversie_osm" / "gis_osm_water_a_free_1.shp"

# Walking-distance bands (m) and their colours: one blue ramp, darkest = nearest.
BANDS = [(0, 150, "#0d366b"), (150, 300, "#1c5cab"), (300, 425, "#3987e5"),
         (425, 850, "#86b6ef"), (850, 1275, "#b7d3f6")]
INK, MUTED = "#1f1f1d", "#6b6a66"
BUILDING, WATER_C, PARK, SURFACE = "#e2e0db", "#d6e4ef", "#dfeccf", "#fbfaf8"
ISO_EDGE, ISO_FILL = "#c4521f", (0.87, 0.45, 0.2, 0.12)

RD = osr.SpatialReference()
RD.ImportFromEPSG(28992)
WGS84 = osr.SpatialReference()
WGS84.ImportFromEPSG(4326)
for s in (RD, WGS84):
    s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
TO_RD = osr.CoordinateTransformation(WGS84, RD)

VALHALLA_CALL = r'''
import json, sys
from valhalla import Actor
cfg, lon, lat, km = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
a = Actor(cfg)
loc = [{"lat": lat, "lon": lon}]
exp = json.loads(a.expansion(json.dumps({"locations": loc, "costing": "pedestrian", "action": "isochrone",
    "contours": [{"distance": km}], "skip_opposites": True, "expansion_properties": ["distance"]})))
iso = json.loads(a.isochrone(json.dumps({"locations": loc, "costing": "pedestrian",
    "contours": [{"time": 5}], "polygons": True})))
print(json.dumps({"expansion": exp, "isochrone": iso}))
'''


def walk(lon, lat):
    """Valhalla's expansion (up to 15 min) and 5-minute isochrone from one point."""
    out = subprocess.run([str(config.VALHALLA_PYTHON), "-c", VALHALLA_CALL, str(config.VALHALLA_DIR / "valhalla.json"),
                          str(lon), str(lat), str(config.MAX_DISTANCE_M / 1000)],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def rd_line(coords):
    g = ogr.Geometry(ogr.wkbLineString)
    for lon, lat in coords:
        g.AddPoint_2D(lon, lat)
    g.Transform(TO_RD)
    return [(g.GetX(i), g.GetY(i)) for i in range(g.GetPointCount())], g.Length()


def rings(g):
    """Exterior rings of all polygons in g, as coordinate lists (holes are drawn by the background)."""
    t = ogr.GT_Flatten(g.GetGeometryType())
    if t == ogr.wkbPolygon:
        r = g.GetGeometryRef(0)
        return [[(r.GetX(i), r.GetY(i)) for i in range(r.GetPointCount())]]
    return [ring for i in range(g.GetGeometryCount()) for ring in rings(g.GetGeometryRef(i))]


def polygons(path, layer, box, where=None, srs_in=None):
    """Exterior rings of the polygons of a layer inside box (RD)."""
    ds = ogr.Open(str(path))
    lyr = ds.GetLayerByName(layer) if layer else ds.GetLayer(0)
    if where:
        lyr.SetAttributeFilter(where)
    minx, miny, maxx, maxy = box
    to_rd = None
    srs = lyr.GetSpatialRef()
    if srs is not None and not srs.IsSame(RD):
        srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        to_rd = osr.CoordinateTransformation(srs, RD)
        back = osr.CoordinateTransformation(RD, srs)
        (x0, y0, _), (x1, y1, _) = back.TransformPoint(minx, miny), back.TransformPoint(maxx, maxy)
        lyr.SetSpatialFilterRect(x0, y0, x1, y1)
    else:
        lyr.SetSpatialFilterRect(minx, miny, maxx, maxy)
    out = []
    for f in lyr:
        g = f.GetGeometryRef()
        if g is None:
            continue
        g = g.Clone()
        if to_rd:
            g.Transform(to_rd)
        out += rings(g)
    return out


def segments(expansion, max_m):
    """[(coords, start distance, end distance)] of the reached segments up to max_m."""
    out = []
    for f in expansion["features"]:
        d_end = f["properties"]["distance"]
        coords, length = rd_line(f["geometry"]["coordinates"])
        if d_end - length <= max_m:
            out.append((coords, max(d_end - length, 0.0), d_end))
    return out


def colour(d):
    for lo, hi, c in BANDS:
        if d < hi:
            return c
    return BANDS[-1][2]


def base_map(ax, x0, y0, half):
    box = (x0 - half, y0 - half, x0 + half, y0 + half)
    ax.set_facecolor(SURFACE)
    layers = [
        (polygons(WATER, None, box), WATER_C),
        (polygons(config.PARKS_PATH, config.PARKS_LAYER, box, config.PARKS_WHERE), PARK),
        (polygons(BUILDINGS, None, box), BUILDING),
    ]
    for rs, c in layers:
        ax.add_collection(PolyCollection(rs, facecolors=c, edgecolors="none", zorder=1))
    ax.set_xlim(box[0], box[2])
    ax.set_ylim(box[1], box[3])
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)


def start_marker(ax, x0, y0, name):
    ax.plot([x0], [y0], "o", ms=11, color=INK, mec="white", mew=2.5, zorder=6)
    ax.annotate(name, (x0, y0), xytext=(10, 10), textcoords="offset points", fontsize=11,
                fontweight="bold", color=INK, zorder=7,
                bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="none", alpha=0.85))


def scale_bar(ax, x0, y0, half):
    x = x0 - half * 0.92
    y = y0 - half * 0.92
    ax.plot([x, x + 500], [y, y], color=INK, lw=2.5, solid_capstyle="butt", zorder=6)
    ax.text(x + 250, y + half * 0.025, "500 m", ha="center", va="bottom", fontsize=9, color=INK, zorder=6)


def figure_expansion(segs, x0, y0, name, path):
    half = config.MAX_DISTANCE_M + 100
    fig, ax = plt.subplots(figsize=(9, 9.6), dpi=130)
    base_map(ax, x0, y0, half)
    for lo, hi, c in reversed(BANDS):   # nearest drawn last, on top
        lines = [s[0] for s in segs if lo <= (s[1] + s[2]) / 2 < hi]
        ax.add_collection(LineCollection(lines, colors=c, linewidths=1.6 if hi <= 425 else 1.2,
                                         capstyle="round", zorder=3))
    start_marker(ax, x0, y0, name)
    scale_bar(ax, x0, y0, half)
    handles = [Line2D([0], [0], color=c, lw=3, label=f"{lo}–{hi} m" + (" (5 min)" if hi == 425 else "")
                      + (" (15 min)" if hi == 1275 else "")) for lo, hi, c in BANDS]
    ax.legend(handles=handles, title="Walking distance over streets and paths", loc="upper left",
              frameon=True, framealpha=0.92, edgecolor="none", fontsize=9, title_fontsize=9.5)
    fig.suptitle(f"Valhalla expansion from the {name}", x=0.05, ha="left", fontsize=14, fontweight="bold", color=INK)
    fig.text(0.05, 0.925, f"Every street and path reachable on foot within 15 minutes ({BANDS[-1][1]:,} m at "
             f"{config.WALK_SPEED_KMH} km/h), coloured by walking distance.", fontsize=9.5, color=MUTED)
    fig.text(0.05, 0.03, "Network: OpenStreetMap (Geofabrik, Zuid-Holland). Buildings: BAG. Parks: groenkaart 2024.",
             fontsize=8, color=MUTED)
    fig.subplots_adjust(left=0.04, right=0.96, top=0.9, bottom=0.06)
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def figure_isochrone(segs, iso, x0, y0, name, path):
    limit = config.THRESHOLDS_M["5min"]
    half = limit + 200
    fig, ax = plt.subplots(figsize=(9, 9.6), dpi=130)
    base_map(ax, x0, y0, half)
    polys = []
    for f in iso["features"]:
        g = ogr.CreateGeometryFromJson(json.dumps(f["geometry"]))
        g.Transform(TO_RD)
        polys += rings(g)
    ax.add_collection(PolyCollection(polys, facecolors=[ISO_FILL], edgecolors=ISO_EDGE, linewidths=1.8,
                                     linestyles="--", zorder=2))
    near = [s[0] for s in segs if s[2] <= limit]
    ax.add_collection(LineCollection(near, colors=BANDS[1][2], linewidths=2.0, capstyle="round", zorder=3))
    start_marker(ax, x0, y0, name)
    scale_bar(ax, x0, y0, half)
    handles = [Line2D([0], [0], color=BANDS[1][2], lw=3, label="Streets and paths reached within 425 m (expansion)"),
               Line2D([0], [0], color=ISO_EDGE, lw=2, ls="--", label="5-minute isochrone polygon")]
    ax.legend(handles=handles, loc="upper left", frameon=True, framealpha=0.92, edgecolor="none", fontsize=9)
    fig.suptitle("Isochrone polygon against the walk itself", x=0.05, ha="left", fontsize=14, fontweight="bold", color=INK)
    fig.text(0.05, 0.925, "The polygon is drawn around the reached streets, so it also covers the inside of blocks, "
             "water and\nrailway land. Homes there count as reached even when their door is further away.",
             fontsize=9.5, color=MUTED, va="top")
    fig.text(0.05, 0.03, "Network: OpenStreetMap (Geofabrik, Zuid-Holland). Buildings: BAG. Parks: groenkaart 2024.",
             fontsize=8, color=MUTED)
    fig.subplots_adjust(left=0.04, right=0.96, top=0.88, bottom=0.06)
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


VALHALLA_ROUTE = r'''
import json, sys
from valhalla import Actor
a = Actor(sys.argv[1])
locs = [{"lon": float(sys.argv[2]), "lat": float(sys.argv[3])}, {"lon": float(sys.argv[4]), "lat": float(sys.argv[5])}]
r = json.loads(a.route(json.dumps({"locations": locs, "costing": "pedestrian"})))
enc, i, lat, lon, pts = r["trip"]["legs"][0]["shape"], 0, 0, 0, []
while i < len(enc):   # encoded polyline, precision 6
    for k in (0, 1):
        shift = res = 0
        while True:
            b = ord(enc[i]) - 63; i += 1; res |= (b & 0x1F) << shift; shift += 5
            if b < 0x20:
                break
        d = ~(res >> 1) if res & 1 else res >> 1
        if k == 0:
            lat += d
        else:
            lon += d
    pts.append((lon / 1e6, lat / 1e6))
print(json.dumps({"length": r["trip"]["summary"]["length"] * 1000, "shape": pts}))
'''


def network_path(net_lyr, home, entrance):
    """The shortest walk over the 01_prepare.py network (what GRASS measures), as RD coordinates.
    Only for the picture: networkx on the ways around the two points."""
    import networkx as nx
    pad = 900
    minx, maxx = min(home[0], entrance[0]) - pad, max(home[0], entrance[0]) + pad
    miny, maxy = min(home[1], entrance[1]) - pad, max(home[1], entrance[1]) + pad
    net_lyr.SetSpatialFilterRect(minx, miny, maxx, maxy)
    graph = nx.Graph()
    for f in net_lyr:
        g = f.GetGeometryRef()
        pts = [(round(g.GetX(i), 2), round(g.GetY(i), 2)) for i in range(g.GetPointCount())]
        graph.add_edge(pts[0], pts[-1], weight=g.Length(), shape=pts, highway=f.GetField("highway"))
    net_lyr.SetSpatialFilter(None)

    def nearest(p):
        return min(graph.nodes, key=lambda n: (n[0] - p[0]) ** 2 + (n[1] - p[1]) ** 2)
    route = nx.shortest_path(graph, nearest(home), nearest(entrance), weight="weight")
    out, kinds = [], []
    for a, b in zip(route, route[1:]):
        e = graph.edges[a, b]
        shape = e["shape"] if e["shape"][0] == a else e["shape"][::-1]
        out.append(shape)
        kinds.append(e["highway"])
    return out, kinds


def figure_example(home_id, path):
    """One home where the engines differ because of a cycle path: both walks on one map."""
    ds = ogr.Open(str(config.RESULT_GPKG))
    lyr = ds.GetLayerByName("homes")
    lyr.SetAttributeFilter(f"home_id = '{home_id}'")
    h = lyr.GetNextFeature()
    if h is None:
        sys.exit(f"ERROR: home {home_id} not in {config.RESULT_GPKG}")
    hx, hy = h.GetGeometryRef().GetX(), h.GetGeometryRef().GetY()
    gr_m, vh_m, ent_id = h.GetField("gr_m"), h.GetField("vh_m"), h.GetField("gr_ent")
    prep = ogr.Open(str(config.PREPARED_GPKG))
    ent = prep.GetLayerByName("entrances")
    ent.SetAttributeFilter(f"ent_id = {ent_id}")
    e = ent.GetNextFeature()
    ex, ey = e.GetGeometryRef().GetX(), e.GetGeometryRef().GetY()
    net = prep.GetLayerByName("network")
    gr_path, kinds = network_path(net, (hx, hy), (ex, ey))
    to_wgs = osr.CoordinateTransformation(RD, WGS84)
    (hlon, hlat, _), (elon, elat, _) = to_wgs.TransformPoint(hx, hy), to_wgs.TransformPoint(ex, ey)
    out = subprocess.run([str(config.VALHALLA_PYTHON), "-c", VALHALLA_ROUTE, str(config.VALHALLA_DIR / "valhalla.json"),
                          str(hlon), str(hlat), str(elon), str(elat)], capture_output=True, text=True, check=True)
    vr = json.loads(out.stdout.strip().splitlines()[-1])
    vh_line, _ = rd_line(vr["shape"])

    cx, cy = (hx + ex) / 2, (hy + ey) / 2
    xs = [p[0] for p in vh_line] + [p[0] for s in gr_path for p in s]
    ys = [p[1] for p in vh_line] + [p[1] for s in gr_path for p in s]
    half = max(max(xs) - min(xs), max(ys) - min(ys)) / 2 + 60
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    fig, ax = plt.subplots(figsize=(9, 9.6), dpi=130)
    base_map(ax, cx, cy, half)
    net.SetSpatialFilterRect(cx - half, cy - half, cx + half, cy + half)
    cyc = []
    for f in net:
        if f.GetField("highway") in ("cycleway", "bridleway"):
            g = f.GetGeometryRef()
            cyc.append([(g.GetX(i), g.GetY(i)) for i in range(g.GetPointCount())])
    ax.add_collection(LineCollection(cyc, colors="#9b9890", linewidths=1.2, linestyles=(0, (3, 2)), zorder=2))
    ax.add_collection(LineCollection([vh_line], colors="#eb6834", linewidths=3, zorder=4, capstyle="round"))
    ax.add_collection(LineCollection(gr_path, colors="#2a78d6", linewidths=3, zorder=5, capstyle="round"))
    on_cycle = [s for s, k in zip(gr_path, kinds) if k in ("cycleway", "bridleway")]
    ax.add_collection(LineCollection(on_cycle, colors="#0d366b", linewidths=4.5, zorder=6, capstyle="round"))
    ax.plot([hx], [hy], "o", ms=10, color=INK, mec="white", mew=2, zorder=7)
    ax.annotate("home", (hx, hy), xytext=(8, -14), textcoords="offset points", fontsize=10, fontweight="bold", color=INK, zorder=8)
    ax.plot([ex], [ey], "s", ms=10, color="#3a8a3a", mec="white", mew=2, zorder=7)
    ax.annotate("park entrance", (ex, ey), xytext=(8, 6), textcoords="offset points", fontsize=10, fontweight="bold",
                color=INK, zorder=8)
    handles = [Line2D([0], [0], color="#2a78d6", lw=3, label=f"GRASS: {gr_m:.0f} m (dark: over a cycle path)"),
               Line2D([0], [0], color="#eb6834", lw=3, label=f"Valhalla to the same entrance: {vr['length']:,.0f} m "
                      f"(its nearest entrance: {vh_m:.0f} m)"),
               Line2D([0], [0], color="#9b9890", lw=1.2, ls=(0, (3, 2)), label="cycle paths in OpenStreetMap")]
    ax.legend(handles=handles, loc="upper left", frameon=True, framealpha=0.92, edgecolor="none", fontsize=9)
    scale_bar(ax, cx, cy, half)
    fig.suptitle("Why GRASS and Valhalla differ: the cycle path", x=0.05, ha="left", fontsize=14, fontweight="bold", color=INK)
    fig.text(0.05, 0.925, "GRASS crosses the water over a cycle bridge. Valhalla's walking profile leaves cycle paths "
             "out, so it walks round.\nDutch traffic law lets you walk on a cycle path when there is no footpath.",
             fontsize=9.5, color=MUTED, va="top")
    fig.text(0.05, 0.03, f"{h.GetField('gemeente')}. Network: OpenStreetMap (Geofabrik, Zuid-Holland). Buildings: BAG.",
             fontsize=8, color=MUTED)
    fig.subplots_adjust(left=0.04, right=0.96, top=0.88, bottom=0.06)
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lon", type=float, default=4.31707)   # Zuid-Hollandplein 1 (PDOK locatieserver)
    ap.add_argument("--lat", type=float, default=52.08710)
    ap.add_argument("--name", default="Provinciehuis")
    ap.add_argument("--home", default="0503010000027768",   # Delft: GRASS 147 m, Valhalla 453 m, GRASS without cycleways 459 m
                    help="home_id for 300-fietspad-voorbeeld.png (needs 04_compare.py's result)")
    a = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if config.RESULT_GPKG.exists():
        figure_example(a.home, OUT_DIR / "300-fietspad-voorbeeld.png")
    x0, y0, _ = TO_RD.TransformPoint(a.lon, a.lat)
    result = walk(a.lon, a.lat)
    segs = segments(result["expansion"], config.MAX_DISTANCE_M)
    figure_expansion(segs, x0, y0, a.name, OUT_DIR / "300-expansion.png")
    figure_isochrone(segs, result["isochrone"], x0, y0, a.name, OUT_DIR / "300-isochroon.png")
    print(f"{len(segs):,} segments; wrote {OUT_DIR / '300-expansion.png'} and 300-isochroon.png")


if __name__ == "__main__":
    main()
