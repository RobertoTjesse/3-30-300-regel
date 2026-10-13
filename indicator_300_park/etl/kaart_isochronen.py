"""
kaart_isochronen.py — Maps of the 300 parks phase: the province and a zoom on The Hague and Delft.

Shows the green areas (01_prepare.py, layer parks), the Valhalla isochrones of
300 m, 5 and 15 minutes (02_valhalla.py, layer isochrones) and, in the zoom, the
entrances. Written to data/processed/explainer/300-isochronen-*.png.

Usage (from the repository root, with the OSGeo4W Python, after a province run):
    python indicator_300_park/etl/kaart_isochronen.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.collections import PolyCollection  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch, PathPatch  # noqa: E402
from matplotlib.path import Path as MPath  # noqa: E402
from osgeo import ogr  # noqa: E402

ogr.UseExceptions()
OUT_DIR = config.PROCESSED_DIR / "explainer"
INK, MUTED, SURFACE = "#1f1f1d", "#6b6a66", "#fbfaf8"
BANDS = {"15min": "#cde2fb", "5min": "#6da7ec", "300m": "#1c5cab"}
LABELS = {"300m": "within 300 m", "5min": "within 5 min (425 m)", "15min": "within 15 min (1,275 m)"}
PARK, ENTRANCE, OUTLINE = "#2f7d32", "#c4521f", "#9b9890"
PROVINCE_BOX = (43000, 406000, 131000, 484000)
ZOOM_BOX = (76000, 438000, 92000, 460000)


def rings(g):
    """All rings (outer and holes) of the polygons in g, as coordinate lists."""
    t = ogr.GT_Flatten(g.GetGeometryType())
    if t == ogr.wkbPolygon:
        return [[(r.GetX(k), r.GetY(k)) for k in range(r.GetPointCount())]
                for r in (g.GetGeometryRef(i) for i in range(g.GetGeometryCount()))]
    return [ring for i in range(g.GetGeometryCount()) for ring in rings(g.GetGeometryRef(i))]


def outer_rings(g):
    t = ogr.GT_Flatten(g.GetGeometryType())
    if t == ogr.wkbPolygon:
        r = g.GetGeometryRef(0)
        return [[(r.GetX(k), r.GetY(k)) for k in range(r.GetPointCount())]]
    return [ring for i in range(g.GetGeometryCount()) for ring in outer_rings(g.GetGeometryRef(i))]


def holed_patch(ring_list, colour):
    """One patch for many rings, holes left open (even-odd fill)."""
    verts, codes = [], []
    for r in ring_list:
        if len(r) >= 3:
            verts += r
            codes += [MPath.MOVETO] + [MPath.LINETO] * (len(r) - 2) + [MPath.CLOSEPOLY]
    return PathPatch(MPath(verts, codes), facecolor=colour, edgecolor="none", zorder=1) if verts else None


def draw(ax, box, simplify, show_entrances):
    iso_ds = ogr.Open(str(config.VALHALLA_GPKG))
    iso = {}
    for f in iso_ds.GetLayerByName("isochrones"):
        iso[f.GetField("contour")] = rings(f.GetGeometryRef().SimplifyPreserveTopology(simplify))
    for name in ("15min", "5min", "300m"):
        patch = holed_patch(iso.get(name, []), BANDS[name])
        if patch:
            ax.add_patch(patch)
    prep = ogr.Open(str(config.PREPARED_GPKG))
    parks = prep.GetLayerByName("parks")
    n_parks = parks.GetFeatureCount()
    park_rings = [r for f in parks for r in rings(f.GetGeometryRef().SimplifyPreserveTopology(simplify))]
    ax.add_collection(PolyCollection(park_rings, facecolors=PARK, edgecolors="none", zorder=2))
    gem_ds = ogr.Open(str(config.GEBIEDEN_GPKG))
    gem = [r for f in gem_ds.GetLayerByName("gemeenten")
           for r in outer_rings(f.GetGeometryRef().SimplifyPreserveTopology(max(simplify * 2, 5)))]
    ax.add_collection(PolyCollection(gem, facecolors="none", edgecolors=OUTLINE, linewidths=0.5, zorder=3))
    entrances = prep.GetLayerByName("entrances")
    n_entrances = entrances.GetFeatureCount()
    if show_entrances:
        entrances.SetSpatialFilterRect(*box)
        pts = [(f.GetGeometryRef().GetX(), f.GetGeometryRef().GetY()) for f in entrances]
        if pts:
            xs, ys = zip(*pts)
            ax.scatter(xs, ys, s=7, c=ENTRANCE, lw=0, zorder=4)
    ax.set_xlim(box[0], box[2])
    ax.set_ylim(box[1], box[3])
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_facecolor(SURFACE)
    return n_parks, n_entrances


def figure(box, simplify, show_entrances, title, path):
    fig, ax = plt.subplots(figsize=(10, 9.5), dpi=130)
    n_parks, n_entrances = draw(ax, box, simplify, show_entrances)
    handles = [Patch(color=PARK, label=f"park or green area ({n_parks:,})")]
    handles += [Patch(color=BANDS[n], label=LABELS[n]) for n in ("300m", "5min", "15min")]
    if show_entrances:
        handles.append(Line2D([0], [0], marker="o", ls="", ms=5, color=ENTRANCE, label="entrance"))
    ax.legend(handles=handles, loc="lower left", frameon=True, framealpha=0.92, edgecolor="none", fontsize=9)
    sources = ", ".join(src["bron"] for src in config.PARKS_SOURCES)
    fig.suptitle(title, x=0.04, ha="left", fontsize=14, fontweight="bold", color=INK)
    fig.text(0.04, 0.925, f"Walking distance from the {n_entrances:,} entrances of {n_parks:,} green areas "
             f"({sources}).\nValhalla isochrones over OpenStreetMap, cycle paths included.",
             fontsize=9.5, color=MUTED, va="top")
    fig.subplots_adjust(left=0.02, right=0.98, top=0.88, bottom=0.02)
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    print("wrote", path)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    figure(PROVINCE_BOX, 15, False, "The 300: green within walking distance, Zuid-Holland",
           OUT_DIR / "300-isochronen-provincie.png")
    figure(ZOOM_BOX, 2, True, "The 300: The Hague, Rijswijk and Delft",
           OUT_DIR / "300-isochronen-den-haag-delft.png")


if __name__ == "__main__":
    main()
