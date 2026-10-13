"""
kaart_zonder_ingang.py — Example maps of green areas that get no entrance (issue #3).

Four panels around chosen green areas (park_id in 01_prepare.py's parks layer):
the green area without an entrance (red), other green (green), open water
cut out of the green (blue), the walkable OSM network (grey), the 10 m zone
along the edge in which a street would give an entrance (dotted), and the
entrances of the other green areas (orange). Written to
data/processed/explainer/300-zonder-ingang.png.

Usage (from the repository root, with the OSGeo4W Python, after a run):
    python indicator_300_park/etl/kaart_zonder_ingang.py [park_id ...]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.collections import LineCollection, PolyCollection  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from osgeo import ogr  # noqa: E402

ogr.UseExceptions()
OUT = config.PROCESSED_DIR / "explainer" / "300-zonder-ingang.png"
DEFAULT = [2620, 1931, 274, 1406]   # Biesbosch islet, Kralingse Bos (heemtuin), volkstuin De Noort Bonnen, Westerpark
INK, MUTED, SURFACE = "#1f1f1d", "#6b6a66", "#fbfaf8"


def rings(g):
    t = ogr.GT_Flatten(g.GetGeometryType())
    if t == ogr.wkbPolygon:
        return [[(r.GetX(k), r.GetY(k)) for k in range(r.GetPointCount())]
                for r in (g.GetGeometryRef(i) for i in range(g.GetGeometryCount()))]
    return [x for i in range(g.GetGeometryCount()) for x in rings(g.GetGeometryRef(i))]


def lines(g):
    t = ogr.GT_Flatten(g.GetGeometryType())
    if t == ogr.wkbLineString:
        return [[(g.GetX(k), g.GetY(k)) for k in range(g.GetPointCount())]]
    return [x for i in range(g.GetGeometryCount()) for x in lines(g.GetGeometryRef(i))]


def in_box(lyr, box, where=None):
    lyr.SetAttributeFilter(where)
    lyr.SetSpatialFilterRect(*box)
    out = [f for f in lyr]
    lyr.SetSpatialFilter(None)
    lyr.SetAttributeFilter(None)
    return out


def panel(ax, prep, water, park_id):
    parks = prep.GetLayerByName("parks")
    target = in_box(parks, (-1e9, -1e9, 1e9, 1e9), f"park_id = {park_id}")[0]
    g = target.GetGeometryRef().Clone()
    x0, x1, y0, y1 = g.GetEnvelope()
    half = max(x1 - x0, y1 - y0) / 2 + 120
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    box = (cx - half, cy - half, cx + half, cy + half)
    ax.set_facecolor(SURFACE)
    wet = [r for lyr in (water.GetLayerByName("cbs"), water.GetLayerByName("osm"))
           for f in in_box(lyr, box) for r in rings(f.GetGeometryRef())]
    ax.add_collection(PolyCollection(wet, facecolors="#cfe1f2", edgecolors="none", zorder=1))
    other = [r for f in in_box(parks, box) if f.GetField("park_id") != park_id for r in rings(f.GetGeometryRef())]
    ax.add_collection(PolyCollection(other, facecolors="#9ccf8f", edgecolors="#2f7d32", linewidths=0.6, zorder=2))
    ax.add_collection(PolyCollection(rings(g), facecolors="#f2a7a0", edgecolors="#c0392b", linewidths=1.4, zorder=3))
    zone = g.Boundary().Buffer(config.EDGE_STREET_M)
    ax.add_collection(PolyCollection(rings(zone), facecolors="none", edgecolors="#c0392b", linewidths=0.6,
                                     linestyles=":", zorder=3))
    net = [ln for f in in_box(prep.GetLayerByName("network"), box) for ln in lines(f.GetGeometryRef())]
    ax.add_collection(LineCollection(net, colors="#6b6a66", linewidths=0.8, zorder=4))
    ents = in_box(prep.GetLayerByName("entrances"), box)
    if ents:
        ax.scatter([f.GetGeometryRef().GetX() for f in ents], [f.GetGeometryRef().GetY() for f in ents],
                   s=14, c="#e67e22", edgecolors="white", linewidths=0.5, zorder=5)
    nearest = min([f.GetGeometryRef().Distance(g) for f in in_box(prep.GetLayerByName("network"), box)] or [None]) \
        if net else None
    ax.set_xlim(box[0], box[2])
    ax.set_ylim(box[1], box[3])
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_edgecolor("#d9d6cf")
    name = (target.GetField("namen") or "").split(";")[0] or "zonder naam"
    near = f"nearest path or street {nearest:.0f} m from the edge" if nearest is not None else "no path or street nearby"
    ax.set_title(f"{name} ({target.GetField('soorten')}, {target.GetField('m2') / 1e4:.1f} ha)\n{near}",
                 fontsize=9.5, color=INK, loc="left")


def main():
    ids = [int(a) for a in sys.argv[1:]] or DEFAULT
    prep = ogr.Open(str(config.PREPARED_GPKG))
    water = ogr.Open(str(config.WATER_GPKG))
    fig, axes = plt.subplots(2, 2, figsize=(11, 11.5), dpi=120)
    for ax, pid in zip(axes.flat, ids):
        panel(ax, prep, water, pid)
    handles = [Patch(facecolor="#f2a7a0", edgecolor="#c0392b", label="green area without an entrance"),
               Patch(facecolor="#9ccf8f", edgecolor="#2f7d32", label="other green"),
               Patch(facecolor="#cfe1f2", label="open water (cut out of the green)"),
               Line2D([0], [0], color="#6b6a66", lw=1, label="walkable path or street (OpenStreetMap)"),
               Line2D([0], [0], color="#c0392b", lw=0.8, ls=":", label=f"{config.EDGE_STREET_M:.0f} m along the edge"),
               Line2D([0], [0], marker="o", ls="", ms=6, color="#e67e22", label="entrance (of other green)")]
    fig.legend(handles=handles, loc="lower center", ncol=3, frameon=False, fontsize=9)
    fig.suptitle("Why some green areas get no entrance", x=0.04, ha="left", fontsize=14, fontweight="bold", color=INK)
    fig.text(0.04, 0.945, "An entrance needs a path that crosses the edge, an OSM gate on the edge, or a street within "
             f"{config.EDGE_STREET_M:.0f} m of the edge.\nWithout one, homes cannot reach the green in the calculation.",
             fontsize=9.5, color=MUTED, va="top")
    fig.subplots_adjust(left=0.03, right=0.97, top=0.88, bottom=0.09, hspace=0.18, wspace=0.06)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, facecolor=SURFACE)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
