"""
explainer_gifs.py — animated GIFs for the documentation that show, on one
small neighbourhood, how the 3 is computed:

  3-1-input.gif     surface model, buildings and trees (and the trees left out)
  3-2-one-tree.gif  one tree's viewshed, ray by ray, with a side view
  3-3-add-up.gif    all trees added up into a count per 0.5 m cell
  3-4-score.gif     the facade ring and the score per home

It calls the pipeline's own functions (stage 1's NoData fill, stage 2's tree
query, canopy-top observer, ViewshedGenerate call and accumulator, stage 4's
home selection), so the pictures show what the pipeline computes.

Usage (OSGeo4W Python, from the repository root):
    python indicator_3_bomen/etl/explainer_gifs.py [--x0 83860 --y0 446880 --size 110]

Output: data/processed/explainer_gifs/3-*.gif, to be copied to the wiki's
images/ folder. The default block is a street of row houses in
Delft (RD New); --check compares the scores with a Delft_woningen.gpkg.
"""

import argparse
import importlib
import math
import sys
import tempfile
import warnings
from pathlib import Path

import numpy as np
from osgeo import gdal, ogr, osr

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap, LightSource  # noqa: E402
from matplotlib.patches import Circle, Polygon as MplPolygon, Rectangle  # noqa: E402
from PIL import Image  # noqa: E402

gdal.UseExceptions()
ogr.UseExceptions()
warnings.simplefilter("ignore", FutureWarning)

stage1 = importlib.import_module("01_tile_dem")
stage2 = importlib.import_module("02_compute_viewsheds")
stage4 = importlib.import_module("04_score_buildings")

OUT_DIR = config.BASE_DIR / "data" / "processed" / "explainer_gifs"
PX = 0.5   # DEM cell size (m)

# Colours: ink for text, one accent for "visible", one blue ramp for counts,
# and the web map's own classes for the score
INK, INK_2 = "#1f2328", "#59636e"
TREE_KEPT, TREE_OUT = "#1a7f37", "#24292f"
VISIBLE = "#f2b705"
COUNT_CMAP = LinearSegmentedColormap.from_list(
    "count", ["#c6dbef", "#9ecae1", "#4292c6", "#08519c", "#08306b"])
CLASS_COLOURS = {"0": "#d7191c", "1-2": "#fd8d3c", "3-5": "#74c476",
                 "6-7": "#31a354", "8+": "#006d2c"}
FIG_W, FIG_H, DPI = 9.6, 6.4, 100


# ---------------------------------------------------------------------------
# Data: the block plus a halo of MAX_DISTANCE, as one stage 2 tile
# ---------------------------------------------------------------------------

class Block:
    def __init__(self, x0, y0, size):
        self.x0, self.y0, self.size = x0, y0, size
        halo = math.ceil(config.MAX_DISTANCE + 5)
        self.rx0, self.ry0 = x0 - halo, y0 - halo
        self.rx1, self.ry1 = x0 + size + halo, y0 + size + halo
        self.gt = (self.rx0, PX, 0.0, self.ry1, 0.0, -PX)
        self.nx = int(round((self.rx1 - self.rx0) / PX))
        self.ny = int(round((self.ry1 - self.ry0) / PX))
        # the block itself, in region pixels
        self.c0 = int(round((x0 - self.rx0) / PX))
        self.r0 = int(round((self.ry1 - (y0 + size)) / PX))
        self.n = int(round(size / PX))
        self.extent = (x0, x0 + size, y0, y0 + size)

    def inner(self, arr):
        return arr[self.r0:self.r0 + self.n, self.c0:self.c0 + self.n]

    def in_block(self, x, y, margin=0.0):
        return (self.x0 - margin <= x < self.x0 + self.size + margin
                and self.y0 - margin <= y < self.y0 + self.size + margin)


def read_dem(b):
    """The region from the province DEM, NoData filled as stage 1 does."""
    src = gdal.Open(str(config.PROVINCE_DEM_VRT))
    sgt = src.GetGeoTransform()
    xoff = int(round((b.rx0 - sgt[0]) / sgt[1]))
    yoff = int(round((b.ry1 - sgt[3]) / sgt[5]))
    tmp = Path(tempfile.mkdtemp()) / "region.tif"
    gdal.Translate(str(tmp), src, srcWin=[xoff, yoff, b.nx, b.ny])
    stage1._fill_nodata(tmp)
    ds = gdal.Open(str(tmp))
    dem = ds.GetRasterBand(1).ReadAsArray().astype(np.float32)
    proj = ds.GetProjection()
    return dem, proj


def read_trees(b):
    """(kept, left out): lists of (x, y) in the region, by the status filter."""
    kept = list(stage2.iter_tree_points_in_bbox(
        config.PROVINCE_TREES_GPKG, b.rx0, b.ry0, b.rx1, b.ry1, config.TREES_LAYER))
    ds = ogr.Open(str(config.PROVINCE_TREES_GPKG), 0)
    layer = ds.GetLayer(0)
    layer.SetSpatialFilterRect(b.rx0, b.ry0, b.rx1, b.ry1)
    keep = config.tree_attribute_filter()
    out = []
    if keep:
        layer.SetAttributeFilter(f"NOT ({keep})")
        for f in layer:
            g = f.GetGeometryRef()
            out.append((g.GetX(), g.GetY()))
    return kept, out


def read_buildings(b):
    """[(id, ogr geometry)] for every footprint touching the region."""
    ds = ogr.Open(str(config.PROVINCE_BUILDINGS_GPKG), 0)
    layer = ds.GetLayer(0)
    layer.SetSpatialFilterRect(b.rx0, b.ry0, b.rx1, b.ry1)
    return [(f.GetField(config.BAG_BUILDING_ID), f.GetGeometryRef().Clone()) for f in layer]


def viewsheds(b, dem, proj, trees):
    """Per tree: (x, y, observer info, visible mask on the region grid), as stage 2."""
    buildings = stage2._building_mask(b.gt, b.nx, b.ny, proj)
    mem = gdal.GetDriverByName("MEM")
    result = []
    for x, y in trees:
        prepared = stage2._prepare_tree(dem, buildings, b.gt, x, y, None)
        if prepared is None:
            continue
        window, window_gt, h, info = prepared
        win_ds = mem.Create("", window.shape[1], window.shape[0], 1, gdal.GDT_Float32)
        win_ds.SetGeoTransform(window_gt)
        win_ds.GetRasterBand(1).WriteArray(window)
        arr, arr_gt = stage2._viewshed_python_api(win_ds.GetRasterBand(1), x, y, h)
        if arr is None:
            continue
        acc = np.zeros((b.ny, b.nx), dtype=np.uint32)
        stage2._paste_into_accumulator(acc, arr, arr_gt, b.gt)
        base_r = int(np.floor((y - window_gt[3]) / window_gt[5]))
        base_c = int(np.floor((x - window_gt[0]) / window_gt[1]))
        info["observer_z"] = float(window[base_r, base_c]) + h
        result.append((x, y, info, acc.astype(bool)))
    return result


def facade_rings(b, proj, buildings, homes):
    """{building id: ring mask on the region grid} for homes in the block, as stage 4."""
    srs = osr.SpatialReference()
    srs.ImportFromWkt(proj)
    mem = gdal.GetDriverByName("MEM")
    mdrv = ogr.GetDriverByName("Memory")
    all_ds = mdrv.CreateDataSource("")
    all_layer = all_ds.CreateLayer("all", srs, ogr.wkbPolygon)
    for _bid, geom in buildings:
        f = ogr.Feature(all_layer.GetLayerDefn())
        f.SetGeometry(geom)
        all_layer.CreateFeature(f)
    rings = {}
    for bid, geom in buildings:
        c = geom.Centroid()
        if bid not in homes or not b.in_block(c.GetX(), c.GetY()):
            continue
        ds = mem.Create("", b.nx, b.ny, 1, gdal.GDT_Byte)
        ds.SetGeoTransform(b.gt)
        ring_ds = mdrv.CreateDataSource("")
        ring_layer = ring_ds.CreateLayer("ring", srs, ogr.wkbPolygon)
        f = ogr.Feature(ring_layer.GetLayerDefn())
        f.SetGeometry(geom.Buffer(config.FACADE_RING_M))
        ring_layer.CreateFeature(f)
        gdal.RasterizeLayer(ds, [1], ring_layer, burn_values=[1])
        gdal.RasterizeLayer(ds, [1], all_layer, burn_values=[0])
        mask = ds.GetRasterBand(1).ReadAsArray().astype(bool)
        if mask.any():
            rings[bid] = mask
    return rings


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

def hillshade(dem):
    rel = dem - np.percentile(dem, 2)
    shade = LightSource(azdeg=315, altdeg=40).hillshade(dem, vert_exag=2, dx=PX, dy=PX)
    # lighter for higher surfaces, so roofs and crowns stand out a little
    lift = np.clip(rel / 20.0, 0, 1)
    return np.clip(0.55 + 0.35 * shade + 0.10 * lift, 0, 1)


def polygon_patches(geom):
    """Exterior rings of a (multi)polygon as matplotlib patch coordinates."""
    g = geom if geom.GetGeometryName() == "MULTIPOLYGON" else None
    parts = [geom.GetGeometryRef(i) for i in range(geom.GetGeometryCount())] if g else [geom]
    out = []
    for p in parts:
        ring = p.GetGeometryRef(0)
        out.append(np.array([(ring.GetX(i), ring.GetY(i)) for i in range(ring.GetPointCount())]))
    return out


class Frame:
    """One figure: a square map on the left and a text column on the right."""

    def __init__(self, b, base, title, text, profile=False):
        self.b = b
        self.fig = plt.figure(figsize=(FIG_W, FIG_H), dpi=DPI)
        self.fig.patch.set_facecolor("white")
        self.ax = self.fig.add_axes([0.02, 0.04, 0.6, 0.9])
        self.ax.imshow(b.inner(base), cmap="gray", vmin=0, vmax=1, extent=b.extent,
                       interpolation="nearest", zorder=0)
        self.ax.set_xlim(b.extent[0], b.extent[1])
        self.ax.set_ylim(b.extent[2], b.extent[3])
        self.ax.set_xticks([]), self.ax.set_yticks([])
        for s in self.ax.spines.values():
            s.set_color("#d0d7de")
        self.fig.text(0.02, 0.965, title, fontsize=15, weight="bold", color=INK, va="center")
        if profile:
            self.side = self.fig.add_axes([0.70, 0.10, 0.28, 0.30])
            self.text_y = 0.88
        else:
            self.side = None
            self.text_y = 0.88
        self.fig.text(0.65, self.text_y, text, fontsize=11, color=INK, va="top",
                      linespacing=1.45, wrap=False)
        self._scalebar()

    def _scalebar(self):
        x, y = self.b.x0 + 4, self.b.y0 + 4
        self.ax.add_patch(Rectangle((x, y), 20, 1.2, color="white", ec=INK, lw=0.8, zorder=20))
        self.ax.text(x + 10, y + 2.2, "20 m", ha="center", va="bottom", fontsize=9, color=INK,
                     zorder=20, bbox=dict(fc="white", ec="none", alpha=0.7, pad=1))

    def zoom(self, x0, x1, y0, y1):
        self.ax.set_xlim(x0, x1)
        self.ax.set_ylim(y0, y1)

    def note(self, y, text, colour=INK_2, size=10):
        self.fig.text(0.65, y, text, fontsize=size, color=colour, va="top", linespacing=1.4)

    def image(self):
        self.fig.canvas.draw()
        img = Image.frombuffer("RGBA", self.fig.canvas.get_width_height(),
                               self.fig.canvas.buffer_rgba()).convert("RGB")
        plt.close(self.fig)
        return img


def draw_buildings(ax, buildings, fill=None, ec="#6e7781", lw=0.6, alpha=1.0, z=2):
    for _bid, geom in buildings:
        for xy in polygon_patches(geom):
            ax.add_patch(MplPolygon(xy, closed=True, fc=fill or "none", ec=ec, lw=lw,
                                    alpha=alpha, zorder=z))


def draw_trees(ax, b, kept=(), out=(), kept_size=34, z=6):
    ko = [(x, y) for x, y in kept if b.in_block(x, y)]
    oo = [(x, y) for x, y in out if b.in_block(x, y)]
    if oo:
        ax.scatter(*zip(*oo), marker="x", s=46, c="white", linewidths=4.0, zorder=z)
        ax.scatter(*zip(*oo), marker="x", s=40, c=TREE_OUT, linewidths=1.8, zorder=z)
    if ko:
        ax.scatter(*zip(*ko), s=kept_size, c=TREE_KEPT, edgecolors="white", linewidths=1.2,
                   zorder=z + 1)


def overlay(ax, b, mask, colour, alpha, z=3):
    rgba = np.zeros(mask.shape + (4,))
    rgba[mask] = matplotlib.colors.to_rgba(colour, alpha)
    ax.imshow(b.inner(rgba), extent=b.extent, interpolation="nearest", zorder=z)


def count_layer(ax, b, counts, vmax, z=3, alpha=0.9):
    c = b.inner(counts).astype(float)
    shown = np.ma.masked_where(c == 0, c)
    return ax.imshow(shown, cmap=COUNT_CMAP, vmin=1, vmax=vmax, extent=b.extent,
                     interpolation="nearest", alpha=alpha, zorder=z)


def count_legend(fr, vmax, y=0.12):
    cax = fr.fig.add_axes([0.65, y, 0.3, 0.025])
    sm = plt.cm.ScalarMappable(cmap=COUNT_CMAP, norm=plt.Normalize(1, vmax))
    cb = fr.fig.colorbar(sm, cax=cax, orientation="horizontal")
    cb.outline.set_visible(False)
    cb.ax.tick_params(labelsize=9, colors=INK_2, length=0)
    fr.fig.text(0.65, y + 0.04, "trees visible from the cell (blank = 0)", fontsize=9,
                color=INK_2)


def save_gif(frames, name):
    """frames: [(PIL image, ms)]; one shared palette keeps the colours steady."""
    imgs = [f for f, _ in frames]
    # one palette from all frames together (from one frame only, it missed
    # colours that appear in other frames); unchanged pixels then compress
    w, h = imgs[0].size
    sheet = Image.new("RGB", (w, h * len(imgs)))
    for i, im in enumerate(imgs):
        sheet.paste(im, (0, i * h))
    # plus fixed slots for the accent colours, which cover too few pixels to
    # win a slot of their own (the red ray came out brown)
    fixed = [INK, INK_2, TREE_KEPT, VISIBLE, "#e0a800", "#cf222e", "#ffffff",
             *CLASS_COLOURS.values(), *COUNT_CMAP(np.linspace(0, 1, 8))]
    fixed = [tuple(int(round(c * 255)) for c in matplotlib.colors.to_rgb(f)) for f in fixed]
    n_free = 256 - len(fixed)
    pal = sheet.quantize(colors=n_free, method=Image.Quantize.MEDIANCUT).getpalette()[:3 * n_free]
    pal_img = Image.new("P", (1, 1))
    pal_img.putpalette(pal + [v for rgb in fixed for v in rgb])
    q = [im.quantize(palette=pal_img, dither=Image.Dither.NONE) for im in imgs]
    path = OUT_DIR / name
    q[0].save(path, save_all=True, append_images=q[1:], duration=[d for _, d in frames],
              loop=0, optimize=True, disposal=1)
    print(f"{path.relative_to(config.BASE_DIR)}: {len(frames)} frames, "
          f"{path.stat().st_size / 1e6:.1f} MB")


# ---------------------------------------------------------------------------
# The four GIFs
# ---------------------------------------------------------------------------

def gif_input(b, base, buildings, homes, kept, out):
    n_k = sum(b.in_block(x, y) for x, y in kept)
    n_o = sum(b.in_block(x, y) for x, y in out)
    home_geoms = [(i, g) for i, g in buildings if i in homes]
    steps = [
        ("1. The surface model",
         "AHN surface model, 0.5 m cells.\n\nIt holds everything that stands\n"
         "on the ground: roofs, crowns,\nhedges, cars. All of it can block\nthe view.",
         {}),
        ("1. Buildings and homes",
         "BAG footprints. Outlined in red:\nthe buildings with at least one\n"
         "home in use.\n\nThe 3 is scored for these.", {"bld": True}),
        ("1. All tree points",
         f"NEO tree register: {n_k + n_o} points\nin this block.", {"bld": True, "all": True}),
        ("1. Leave out gone or small trees",
         f"{n_o} of them are marked\n\"disappeared, small tree\" or\n"
         "\"not seen once, small tree\".\n\nThey are left out (cross):\n"
         "better to miss a tree than to\ncount one that is gone.",
         {"bld": True, "split": True}),
        ("1. The trees that count",
         f"{n_k} trees remain in the block.\n\nTrees up to 30 m outside the\n"
         "block count too: their view\nreaches in.", {"bld": True, "kept": True}),
    ]
    frames = []
    for title, text, opt in steps:
        fr = Frame(b, base, title, text)
        if opt.get("bld"):
            draw_buildings(fr.ax, buildings, ec="#57606a", lw=0.5)
            draw_buildings(fr.ax, home_geoms, ec="#cf222e", lw=0.9, z=3)
        if opt.get("all"):
            draw_trees(fr.ax, b, kept=kept + out)
        if opt.get("split"):
            draw_trees(fr.ax, b, kept=kept, out=out)
        if opt.get("kept"):
            draw_trees(fr.ax, b, kept=kept)
        if opt.get("split") or opt.get("kept"):
            fr.fig.text(0.65, 0.22, "●  tree that counts", color=TREE_KEPT, fontsize=10)
            if opt.get("split"):
                fr.fig.text(0.65, 0.18, "✕  left out", color=TREE_OUT, fontsize=10)
        frames.append((fr.image(), 2600))
    frames[-1] = (frames[-1][0], 4000)
    save_gif(frames, "3-1-input.gif")


def pick_tree(b, vs, buildings):
    """A tree near the middle whose view is partly blocked."""
    cx, cy = b.x0 + b.size / 2, b.y0 + b.size / 2
    yy, xx = np.indices((b.ny, b.nx))
    best = None
    for i, (x, y, info, vis) in enumerate(vs):
        if not info["plausible"] or not b.in_block(x, y, margin=-30):
            continue
        d = np.hypot((xx + 0.5) * PX + b.rx0 - x, b.ry1 - (yy + 0.5) * PX - y)
        share = vis[d <= config.MAX_DISTANCE].mean()
        if 0.25 <= share <= 0.6:
            score = math.hypot(x - cx, y - cy)
            if best is None or score < best[0]:
                best = (score, i)
    return best[1] if best else 0


def ray_profile(b, dem, vis, x, y, theta):
    """Distance, surface height and visibility along one ray from the tree."""
    d = np.arange(0.25, config.MAX_DISTANCE, 0.25)
    px = x + d * math.cos(theta)
    py = y + d * math.sin(theta)
    c = np.clip(((px - b.rx0) / PX).astype(int), 0, b.nx - 1)
    r = np.clip(((b.ry1 - py) / PX).astype(int), 0, b.ny - 1)
    return d, dem[r, c], vis[r, c]


def gif_one_tree(b, base, dem, buildings, vs):
    i = pick_tree(b, vs, buildings)
    x, y, info, vis = vs[i]
    oz = info["observer_z"]
    yy, xx = np.indices((b.ny, b.nx))
    cx = (xx + 0.5) * PX + b.rx0
    cy = b.ry1 - (yy + 0.5) * PX
    ang = np.mod(np.arctan2(cy - y, cx - x), 2 * math.pi)
    dist = np.hypot(cx - x, cy - y)
    inside = dist <= config.MAX_DISTANCE
    n_vis = int(vis.sum())
    zoom = (x - 36, x + 36, y - 36, y + 36)
    ground = info["ground"]
    text = (f"Observer on the canopy top,\n{oz - ground:.1f} m above the ground.\n"
            "Target: eye height, 1.8 m above\neach cell.\n\n"
            "For every cell within 30 m:\ncan the tree top see a person\nstanding there?")
    frames = []
    n_steps = 24
    for k in range(n_steps + 2):
        theta = min(k, n_steps) * 2 * math.pi / n_steps
        done = k > n_steps
        fr = Frame(b, base, "2. One tree: where can it be seen?", text, profile=True)
        fr.zoom(*zoom)
        draw_buildings(fr.ax, buildings, ec="#57606a", lw=0.6)
        swept = inside & (ang <= theta + 1e-9) if not done else inside
        overlay(fr.ax, b, swept & ~vis, "#24292f", 0.35)
        overlay(fr.ax, b, swept & vis, VISIBLE, 0.75)
        fr.ax.add_patch(Circle((x, y), config.MAX_DISTANCE, fc="none", ec=INK, lw=1.2,
                               ls="--", zorder=8))
        fr.ax.scatter([x], [y], s=90, c=TREE_KEPT, edgecolors="white", linewidths=1.5, zorder=10)
        fr.ax.text(x + config.MAX_DISTANCE * 0.71 + 1, y + config.MAX_DISTANCE * 0.71 + 1,
                   "30 m", fontsize=9, color=INK, zorder=9,
                   bbox=dict(fc="white", ec="none", alpha=0.8, pad=1))
        t_ray = theta if not done else math.radians(30)
        fr.ax.plot([x, x + 30 * math.cos(t_ray)], [y, y + 30 * math.sin(t_ray)],
                   color="#cf222e", lw=1.8, zorder=9)
        # side view along the current ray
        d, z, v = ray_profile(b, dem, vis, x, y, t_ray)
        ax = fr.side
        ax.fill_between(d, z.min() - 1, z, color="#d0d7de", lw=0)
        ax.plot(d, z, color="#6e7781", lw=1)
        for j in range(4, len(d), 8):
            ax.plot([0, d[j]], [oz, z[j] + config.TARGET_HEIGHT],
                    color=VISIBLE if v[j] else "#8c959f", lw=1.1 if v[j] else 0.7,
                    alpha=0.95 if v[j] else 0.6)
            ax.scatter([d[j]], [z[j] + config.TARGET_HEIGHT], s=10,
                       c=VISIBLE if v[j] else "#8c959f", zorder=5)
        ax.scatter([0], [oz], s=60, c=TREE_KEPT, edgecolors="white", zorder=6)
        ax.set_xlim(-1, 31)
        ax.set_ylim(z.min() - 1, max(oz, (z + config.TARGET_HEIGHT).max()) + 2)
        ax.set_xlabel("distance from the tree (m)", fontsize=9, color=INK_2)
        ax.set_ylabel("height (m NAP)", fontsize=9, color=INK_2)
        ax.tick_params(labelsize=8, colors=INK_2)
        for s in ("top", "right"):
            ax.spines[s].set_visible(False)
        ax.set_title("side view along the red ray",
                     fontsize=9, color=INK_2, loc="left")
        fr.note(0.58, "■", colour="#e0a800", size=11)
        fr.note(0.582, "    seen by the tree", size=10)
        fr.note(0.535, "■", colour="#6e7781", size=11)
        fr.note(0.537, "    within 30 m, but blocked", size=10)
        if done:
            fr.note(0.47, f"{n_vis:,} cells of 0.5 m see this tree.", colour=INK, size=11)
        frames.append((fr.image(), 4500 if done else (1400 if k == 0 else 220)))
    save_gif(frames, "3-2-one-tree.gif")
    return i


def gif_add_up(b, base, buildings, vs):
    total = np.zeros((b.ny, b.nx), dtype=np.uint32)
    vmax = max(8, int(np.percentile(b.inner(sum(v for *_, v in vs).astype(np.uint32)), 99)))
    # order: trees in the block first, nearest the middle first
    cx, cy = b.x0 + b.size / 2, b.y0 + b.size / 2
    order = sorted(range(len(vs)), key=lambda i: (not b.in_block(vs[i][0], vs[i][1]),
                                                  math.hypot(vs[i][0] - cx, vs[i][1] - cy)))
    n = len(order)
    # one tree at a time first, then ever larger batches
    cuts, k, step = [], 0, 1
    while k < n:
        k = min(n, k + step)
        cuts.append(k)
        if len(cuts) >= 5:
            step = max(step + 1, int(step * 1.6))
    frames, done = [], 0
    for k in cuts:
        new = order[done:k]
        current = np.zeros_like(total, dtype=bool)
        for i in new:
            current |= vs[i][3]
            total += vs[i][3]
        done = k
        fr = Frame(b, base, "3. Add up all trees",
                   "Every tree's viewshed adds 1\nto each cell it can see.\n\n"
                   "The result: per 0.5 m cell, the\nnumber of trees visible from\n"
                   "there at eye height.")
        count_layer(fr.ax, b, total, vmax)
        draw_buildings(fr.ax, buildings, ec="#57606a", lw=0.4)
        if k < n:
            overlay(fr.ax, b, current, VISIBLE, 0.45, z=4)
            pts = [(vs[i][0], vs[i][1]) for i in new if b.in_block(vs[i][0], vs[i][1])]
            if pts:
                fr.ax.scatter(*zip(*pts), s=60, c=VISIBLE, edgecolors=INK, linewidths=1.0,
                              zorder=8)
        fr.note(0.42, f"tree {k} of {n}", colour=INK, size=13)
        if k < n:
            fr.note(0.28, "yellow: what the newest tree(s)\nsee, just added", size=9)
        fr.note(0.36, f"{n - sum(b.in_block(v[0], v[1]) for v in vs)} of them stand up to\n"
                      "30 m outside the block.", size=9)
        count_legend(fr, vmax)
        ms = 900 if len(frames) < 4 else 450
        frames.append((fr.image(), 4500 if k == n else ms))
    save_gif(frames, "3-3-add-up.gif")
    return total, vmax


def gif_score(b, base, buildings, homes, rings, total, vmax):
    geoms = dict(buildings)
    scores = {bid: int(total[m].max()) for bid, m in rings.items()}
    cx, cy = b.x0 + b.size / 2, b.y0 + b.size / 2

    def centre(bid):
        c = geoms[bid].Centroid()
        return c.GetX(), c.GetY()

    # three example homes near the middle, with different classes
    by_class = {}
    for bid in sorted(scores, key=lambda i: math.hypot(centre(i)[0] - cx, centre(i)[1] - cy)):
        by_class.setdefault(stage4.klasse(scores[bid]), bid)
    examples = [by_class[c] for c in ("1-2", "3-5", "8+", "0", "6-7") if c in by_class][:3]

    frames = []
    fr = Frame(b, base, "4. Score per home",
               "Inside a footprint the surface\nmodel is the roof, so the score\n"
               "is taken just outside it.")
    count_layer(fr.ax, b, total, vmax)
    draw_buildings(fr.ax, buildings, ec="#57606a", lw=0.5)
    count_legend(fr, vmax)
    frames.append((fr.image(), 2500))
    for bid in examples:
        x, y = centre(bid)
        gx0, gx1, gy0, gy1 = geoms[bid].GetEnvelope()
        half = max(gx1 - gx0, gy1 - gy0) / 2 + 9
        ring = rings[bid]
        sc = scores[bid]
        rr, cc = np.where(ring & (total == sc))
        mx = b.rx0 + (cc[0] + 0.5) * PX
        my = b.ry1 - (rr[0] + 0.5) * PX
        label = stage4.klasse(sc)
        for phase in range(3):
            fr = Frame(b, base, "4. Score per home",
                       f"Ring of {config.FACADE_RING_M} m around the facade\n"
                       "(outlined cells). Neighbouring\nbuildings are cut out.\n\n"
                       "Score = the highest count in\nthe ring: somewhere in front of\n"
                       "the home, that many trees\nare visible.")
            fr.zoom(x - half, x + half, y - half, y + half)
            count_layer(fr.ax, b, total, vmax)
            draw_buildings(fr.ax, buildings, ec="#57606a", lw=0.6)
            draw_buildings(fr.ax, [(bid, geoms[bid])], ec=INK, lw=2.0, z=5,
                           fill=CLASS_COLOURS[label] if phase == 2 else None)
            overlay(fr.ax, b, ring, "#ffffff", 0.0, z=4)
            ys, xs = np.where(b.inner(ring))
            for r, c in zip(ys, xs):
                fr.ax.add_patch(Rectangle((b.x0 + c * PX, b.y0 + b.size - (r + 1) * PX), PX, PX,
                                          fc="none", ec=INK, lw=0.5, zorder=6))
            if phase >= 1:
                fr.ax.add_patch(Circle((mx, my), 1.1, fc="none", ec="#cf222e", lw=2.2, zorder=9))
                fr.ax.annotate(f"max = {sc}", (mx, my), xytext=(12, 12), textcoords="offset points",
                               fontsize=11, weight="bold", color=INK, zorder=10,
                               bbox=dict(fc="white", ec="#cf222e", pad=2),
                               arrowprops=dict(arrowstyle="-", color="#cf222e", lw=1.2))
            if phase == 2:
                fr.note(0.30, f"{sc} trees  →  class {label}", colour=INK, size=13)
                fr.note(0.24, f"{homes[bid]} home{'s' if homes[bid] != 1 else ''} in this building", size=10)
            count_legend(fr, vmax)
            frames.append((fr.image(), [1600, 1800, 2600][phase]))
    # all homes in the block, coloured by class
    fr = Frame(b, base, "4. Every home scored",
               "Homes with at least 3 visible\ntrees meet the rule (greens).")
    draw_buildings(fr.ax, buildings, fill="#eaeef2", ec="#8c959f", lw=0.4)
    for bid, sc in scores.items():
        draw_buildings(fr.ax, [(bid, geoms[bid])], fill=CLASS_COLOURS[stage4.klasse(sc)],
                       ec="white", lw=0.4, z=3)
    n_ok = sum(homes[i] for i, s in scores.items() if s >= 3)
    n_all = sum(homes[i] for i in scores)
    y = 0.62
    for label, colour in CLASS_COLOURS.items():
        fr.fig.patches.append(Rectangle((0.65, y - 0.012), 0.022, 0.03, color=colour,
                                        transform=fr.fig.transFigure, figure=fr.fig))
        fr.fig.text(0.68, y, f"{label} trees", fontsize=10, color=INK, va="center")
        y -= 0.045
    fr.fig.patches.append(Rectangle((0.65, y - 0.012), 0.022, 0.03, color="#eaeef2", ec="#8c959f",
                                    transform=fr.fig.transFigure, figure=fr.fig))
    fr.fig.text(0.68, y, "no homes", fontsize=10, color=INK, va="center")
    fr.note(0.28, f"Here: {n_ok} of {n_all} homes\n({n_ok / n_all:.0%}) see 3 or more trees.",
            colour=INK, size=12)
    frames.append((fr.image(), 5000))
    save_gif(frames, "3-4-score.gif")
    return scores


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--x0", type=float, default=83860)
    ap.add_argument("--y0", type=float, default=446880)
    ap.add_argument("--size", type=float, default=110)
    ap.add_argument("--check", type=Path, help="Delft_woningen.gpkg to compare scores with")
    a = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    b = Block(a.x0, a.y0, a.size)
    dem, proj = read_dem(b)
    kept, out = read_trees(b)
    buildings = read_buildings(b)
    homes = stage4.residential_building_ids(b.rx0, b.ry0, b.rx1, b.ry1)
    print(f"{len(kept)} trees kept, {len(out)} left out, {len(buildings)} buildings in the region")
    vs = viewsheds(b, dem, proj, kept)
    base = hillshade(dem)

    gif_input(b, base, buildings, homes, kept, out)
    gif_one_tree(b, base, dem, buildings, vs)
    total, vmax = gif_add_up(b, base, buildings, vs)
    rings = facade_rings(b, proj, buildings, homes)
    scores = gif_score(b, base, buildings, homes, rings, total, vmax)

    if a.check:
        ds = ogr.Open(str(a.check), 0)
        layer = ds.GetLayer(0)
        same = diff = 0
        for f in layer:
            bid = f.GetField("pand_id")
            if bid in scores:
                if f.GetField("bomen_zichtbaar") == scores[bid]:
                    same += 1
                else:
                    diff += 1
        print(f"check against {a.check.name}: {same} scores equal, {diff} differ")


if __name__ == "__main__":
    main()
