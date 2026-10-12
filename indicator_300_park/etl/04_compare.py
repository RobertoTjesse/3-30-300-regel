"""
04_compare.py — The 300 from GRASS and from Valhalla, side by side (and the old FME run).

Per home it puts together:
  gr_m            walking distance with GRASS v.net.distance (03_grass.py)
  vh_m            walking distance with Valhalla expansion (02_valhalla.py)
  vh_iso_*        1 = inside Valhalla's merged isochrone polygons (the classic method)
  fme_5min/15min  1 = its building was inside the old FME/Valhalla zones (300.gdb),
                  0 = outside, empty = building not in that run

and reports, for every threshold, the share of homes within it per method,
how often GRASS and Valhalla agree, and how far their distances differ.

Output:
  config.RESULT_GPKG   layer homes with all of the above, plus diff_m (vh_m - gr_m)
                       and per threshold the agreement: beide / alleen_grass /
                       alleen_valhalla / geen
  config.RESULT_CSV    the summary table
  data/processed/explainer/300-vergelijking-*.png   a scatter of the two distances
                       and a map of where they disagree on 300 m

Usage (from the repository root, with the OSGeo4W Python):
    python indicator_300_park/etl/04_compare.py
"""
import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
import numpy as np  # noqa: E402
from osgeo import gdal, ogr, osr  # noqa: E402
gdal.UseExceptions()
ogr.UseExceptions()

FIG_DIR = config.PROCESSED_DIR / "explainer"
INK, MUTED, GRID, SURFACE = "#1f1f1d", "#6b6a66", "#e6e4df", "#fbfaf8"
AGREE = {"beide": ("#c9c7c0", "both within"), "geen": ("#efeeea", "both beyond"),
         "alleen_grass": ("#2a78d6", "only GRASS within"), "alleen_valhalla": ("#eb6834", "only Valhalla within")}


def read_homes(path, fields):
    ds = ogr.Open(str(path))
    out = {}
    for f in ds.GetLayerByName("homes"):
        out[f.GetField("home_id")] = [f.GetField(n) for n in fields] + [f.GetGeometryRef().GetX(), f.GetGeometryRef().GetY()]
    return out


def fme_flags(area):
    """{pand_id: (in 5-min zone, in 15-min zone)} from the old FME result, inside the study area."""
    path = config.FME_OUTPUT_DIR / "300.gdb"
    if not path.exists():
        return None
    ds = ogr.Open(str(path))
    minx, maxx, miny, maxy = area.GetEnvelope()
    out = {}
    for layer, i in (("_300regel", 0), ("_300regel_15", 1)):
        lyr = ds.GetLayerByName(layer)
        lyr.SetSpatialFilterRect(minx, miny, maxx, maxy)
        for f in lyr:
            pid = f.GetField("identificatie")
            flags = list(out.get(pid, (0, 0)))
            flags[i] = max(flags[i], int((f.GetField("_related_suppliers") or 0) >= 1))
            out[pid] = tuple(flags)
    return out


def agreement(g, v, m):
    gi = g is not None and g <= m
    vi = v is not None and v <= m
    return "beide" if gi and vi else "alleen_grass" if gi else "alleen_valhalla" if vi else "geen"


def scatter(gr, vh, path):
    both = ~np.isnan(gr) & ~np.isnan(vh)
    fig, ax = plt.subplots(figsize=(7.5, 7.5), dpi=130)
    ax.set_facecolor(SURFACE)
    hb = ax.hexbin(gr[both], vh[both], gridsize=70, extent=(0, config.MAX_DISTANCE_M, 0, config.MAX_DISTANCE_M),
                   bins="log", cmap="Blues", mincnt=1, linewidths=0)
    ax.plot([0, config.MAX_DISTANCE_M], [0, config.MAX_DISTANCE_M], color=INK, lw=1, ls="--")
    for m in (config.THRESHOLDS_M["300m"], config.THRESHOLDS_M["5min"]):
        ax.axvline(m, color=MUTED, lw=0.8)
        ax.axhline(m, color=MUTED, lw=0.8)
    ax.set_xlabel("GRASS v.net.distance (m)", color=INK)
    ax.set_ylabel("Valhalla expansion (m)", color=INK)
    ax.set_xlim(0, config.MAX_DISTANCE_M)
    ax.set_ylim(0, config.MAX_DISTANCE_M)
    ax.set_aspect("equal")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    cb = fig.colorbar(hb, ax=ax, shrink=0.7, pad=0.02)
    cb.set_label("homes per cell", color=MUTED)
    d = vh[both] - gr[both]
    fig.suptitle("Walking distance per home: GRASS against Valhalla"
                 + (f" (GRASS without {' and '.join(config.GRASS_EXCLUDE_HIGHWAYS)})" if config.VARIANT else ""), x=0.06, ha="left", fontsize=13,
                 fontweight="bold", color=INK)
    fig.text(0.06, 0.915, f"{', '.join(config.MUNICIPALITIES) or config.PROVINCE}: {both.sum():,} homes. "
             f"On the dashed line both agree; above it Valhalla is longer.\n"
             f"Median difference {np.median(d):+.0f} m; within 10 m for {np.mean(np.abs(d) <= 10):.0%} of homes. "
             f"Grey lines: 300 and 425 m.", fontsize=9, color=MUTED, va="top")
    fig.subplots_adjust(top=0.86)
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def disagreement_map(rows, area, threshold, path):
    fig, ax = plt.subplots(figsize=(8, 8.6), dpi=130)
    ax.set_facecolor(SURFACE)
    for ring in range(area.GetGeometryCount()):
        poly = area.GetGeometryRef(ring)
        r = poly.GetGeometryRef(0) if ogr.GT_Flatten(poly.GetGeometryType()) == ogr.wkbPolygon else poly
        ax.fill([r.GetX(i) for i in range(r.GetPointCount())], [r.GetY(i) for i in range(r.GetPointCount())],
                color="white", ec=GRID, lw=1, zorder=0)
    counts = {}
    for k in ("geen", "beide", "alleen_valhalla", "alleen_grass"):
        pts = [(r["x"], r["y"]) for r in rows if r[f"agree_{threshold}"] == k]
        counts[k] = len(pts)
        if pts:
            xs, ys = zip(*pts)
            big = k.startswith("alleen")
            ax.scatter(xs, ys, s=4 if big else 1.2, c=AGREE[k][0], lw=0, zorder=3 if big else 1)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(False)
    handles = [Line2D([0], [0], marker="o", ls="", ms=7, color=AGREE[k][0],
                      label=f"{AGREE[k][1]} ({counts[k]:,})") for k in ("alleen_grass", "alleen_valhalla", "beide", "geen")]
    ax.legend(handles=handles, loc="lower left", frameon=True, framealpha=0.92, edgecolor="none", fontsize=9)
    m = config.THRESHOLDS_M[threshold]
    fig.suptitle(f"Where GRASS and Valhalla disagree on {m} m", x=0.05, ha="left", fontsize=13,
                 fontweight="bold", color=INK)
    fig.text(0.05, 0.925, "Every dot is a home (BAG address). Coloured: one engine puts it within "
             f"{m} m of a park entrance, the other does not.", fontsize=9, color=MUTED)
    fig.subplots_adjust(left=0.03, right=0.97, top=0.9, bottom=0.03)
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def main():
    t0 = time.time()
    for p in (config.PREPARED_GPKG, config.VALHALLA_GPKG, config.GRASS_GPKG):
        if not p.exists():
            sys.exit(f"ERROR: {p} is missing; run 01, 02 and 03 first")
    names = list(config.THRESHOLDS_M)
    vh = read_homes(config.VALHALLA_GPKG, ["pand_id", "gemeente", "vh_m"] + [f"vh_iso_{n}" for n in names])
    gr = read_homes(config.GRASS_GPKG, ["gr_m", "gr_ent"])
    area_ds = ogr.Open(str(config.PREPARED_GPKG))
    area_f = area_ds.GetLayerByName("area").GetNextFeature()
    area = area_f.GetGeometryRef().Clone()
    fme = fme_flags(area)

    rows = []
    for hid, (pid, gem, vm, *rest) in vh.items():
        iso = rest[:len(names)]
        x, y = rest[len(names):]
        gm, gent = gr[hid][:2]
        r = {"home_id": hid, "pand_id": pid, "gemeente": gem, "gr_m": gm, "gr_ent": gent, "vh_m": vm,
             "diff_m": round(vm - gm, 1) if vm is not None and gm is not None else None, "x": x, "y": y}
        for n, v in zip(names, iso):
            r[f"vh_iso_{n}"] = v
        f = fme.get(pid) if fme is not None else None
        r["fme_5min"], r["fme_15min"] = (f if f else (None, None))
        for n, m in config.THRESHOLDS_M.items():
            r[f"agree_{n}"] = agreement(gm, vm, m)
        rows.append(r)

    # Layer homes
    if config.RESULT_GPKG.exists():
        config.RESULT_GPKG.unlink()
    rd = osr.SpatialReference()
    rd.ImportFromEPSG(28992)
    out = ogr.GetDriverByName("GPKG").CreateDataSource(str(config.RESULT_GPKG))
    lyr = out.CreateLayer("homes", rd, ogr.wkbPoint)
    cols = [k for k in rows[0] if k not in ("x", "y")]
    types = {"gr_m": ogr.OFTReal, "vh_m": ogr.OFTReal, "diff_m": ogr.OFTReal}
    for k in cols:
        lyr.CreateField(ogr.FieldDefn(k, types.get(k, ogr.OFTInteger if k.startswith(("vh_iso", "fme", "gr_ent"))
                                                     else ogr.OFTString)))
    out.StartTransaction()
    for r in rows:
        f = ogr.Feature(lyr.GetLayerDefn())
        for k in cols:
            if r[k] is not None:
                f.SetField(k, r[k])
        g = ogr.Geometry(ogr.wkbPoint)
        g.AddPoint_2D(r["x"], r["y"])
        f.SetGeometry(g)
        lyr.CreateFeature(f)
    out.CommitTransaction()

    # Summary
    n = len(rows)
    g_arr = np.array([r["gr_m"] if r["gr_m"] is not None else np.nan for r in rows])
    v_arr = np.array([r["vh_m"] if r["vh_m"] is not None else np.nan for r in rows])
    table = []
    for name, m in config.THRESHOLDS_M.items():
        a = {k: sum(1 for r in rows if r[f"agree_{name}"] == k) for k in AGREE}
        fme_col = {"5min": "fme_5min", "15min": "fme_15min"}.get(name)
        fme_known = [r[fme_col] for r in rows if fme_col and r[fme_col] is not None]
        table.append({
            "drempel": name, "meter": m, "woningen": n,
            "grass_pct": round(100 * np.sum(g_arr <= m) / n, 1),
            "valhalla_expansion_pct": round(100 * np.sum(v_arr <= m) / n, 1),
            "valhalla_isochroon_pct": round(100 * sum(r[f"vh_iso_{name}"] for r in rows) / n, 1),
            "fme_oud_pct": round(100 * sum(fme_known) / len(fme_known), 1) if fme_known else "",
            "eens_pct": round(100 * (a["beide"] + a["geen"]) / n, 1),
            "alleen_grass": a["alleen_grass"], "alleen_valhalla": a["alleen_valhalla"],
        })
    both = ~np.isnan(g_arr) & ~np.isnan(v_arr)
    d = v_arr[both] - g_arr[both]
    with open(config.RESULT_CSV, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(table[0]))
        w.writeheader()
        w.writerows(table)

    print(f"{', '.join(config.MUNICIPALITIES) or config.PROVINCE}: {n:,} homes")
    print(f"{'':>6} {'GRASS':>7} {'Valh.exp':>9} {'Valh.iso':>9} {'FME old':>8} {'agree':>7} {'only GR':>8} {'only VH':>8}")
    for t in table:
        print(f"{t['drempel']:>6} {t['grass_pct']:>6}% {t['valhalla_expansion_pct']:>8}% "
              f"{t['valhalla_isochroon_pct']:>8}% {str(t['fme_oud_pct']) + ('%' if t['fme_oud_pct'] != '' else ''):>8} "
              f"{t['eens_pct']:>6}% {t['alleen_grass']:>8,} {t['alleen_valhalla']:>8,}")
    print(f"Distance Valhalla - GRASS (both known, {both.sum():,} homes): median {np.median(d):+.1f} m, "
          f"mean {np.mean(d):+.1f} m; |diff| <= 10 m: {np.mean(np.abs(d) <= 10):.1%}, "
          f"<= 50 m: {np.mean(np.abs(d) <= 50):.1%}; Valhalla longer by > 50 m: {np.mean(d > 50):.1%}, "
          f"shorter by > 50 m: {np.mean(d < -50):.1%}")

    FIG_DIR.mkdir(parents=True, exist_ok=True)
    scatter(g_arr, v_arr, FIG_DIR / f"300-vergelijking-spreiding-{config.AREA_SLUG}{config.VARIANT}.png")
    disagreement_map(rows, area, "300m", FIG_DIR / f"300-vergelijking-kaart-{config.AREA_SLUG}{config.VARIANT}.png")
    print(f"Wrote {config.RESULT_GPKG}, {config.RESULT_CSV.name} and two figures in {FIG_DIR} "
          f"({time.time() - t0:.0f} s)")


if __name__ == "__main__":
    main()
