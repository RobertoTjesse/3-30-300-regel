"""
kroon_500m_woningen.py — The 30 per home: canopy cover on land within 500 m
of the facade, and from the homes up per buurt, wijk and gemeente.

Per residential building of the 3 (<Province>_woningen.gpkg):
  1. The footprint's convex hull is buffered by config.KROON_BUFFER_M (500 m
     from the facade). A buffer that large is convex, and the hull only
     differs from the footprint by recesses that the buffer covers anyway.
  2. On the 5 m grid of kroon_raster_5m.py, the cells whose centre lies in
     the buffer are, per grid row, one unbroken run of columns. With running
     sums along each row, a run adds up in one subtraction, so a building
     costs about 200 lookups however large its buffer.
  3. Canopy cover = crown pixels on land / land pixels in the buffer: water,
     bridges over water and the sea do not count, as the CBS land area does
     not for the 30 per area.

Per gemeente, wijk and buurt (assigned as in the 3's 06_area_summaries.py:
the buurt that holds the footprint's centroid, its wijk, and the gemeente
from stage 5), weighted by the number of homes per building:
  pct_30    share of homes with at least 30% canopy within 500 m
  gem_kroon mean canopy cover within 500 m over the homes

Buildings are processed in 5 km tiles in parallel; each tile reads only its
window of the grid (plus the buffer).

Outputs (data/processed/):
  <Province>_kroon500_woningen.csv   pand_id, woningen, kroon (%), land_ha
  <Province>_kroon500_gebieden.csv   niveau, code, naam, gemeente, gebouwen,
                                     woningen, woningen_30, pct_30, gem_kroon

Usage (from the repository root, with the OSGeo4W Python; after
kroon_raster_5m.py and the 3's stage 6):
    python indicator_30_kroonbedekking/etl/kroon_500m_woningen.py [--workers 5] [--gemeente Delft]
"""

import argparse
import csv
import importlib.util
import sys
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ETL3 = Path(__file__).resolve().parents[2] / "indicator_3_bomen" / "etl"
sys.path.insert(0, str(ETL3))
import config  # noqa: E402

from osgeo import gdal, ogr  # noqa: E402
gdal.UseExceptions()
ogr.UseExceptions()

TILE_M = 5000
QUAD_SEGS = 16          # buffer arc: 64 points per circle, at most 0.6 m inside the true 500 m
THRESHOLD = 30.0        # the 30


def area_summaries():
    """The 3's stage 6 as a module (its file name starts with a digit)."""
    spec = importlib.util.spec_from_file_location("area_summaries", ETL3 / "06_area_summaries.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_w = {}


def _init():
    ds = gdal.Open(str(config.KROON_GRID_TIF))
    _w.update(ds=ds, gt=ds.GetGeoTransform())


def _runs(poly, ys):
    """x-interval [xl, xr] of a convex polygon (closed ring, k x 2) at each y; nan outside."""
    x1, y1, x2, y2 = poly[:-1, 0], poly[:-1, 1], poly[1:, 0], poly[1:, 1]
    lo, hi = np.minimum(y1, y2), np.maximum(y1, y2)
    yy = ys[:, None]
    hit = (yy >= lo) & (yy < hi)
    with np.errstate(divide="ignore", invalid="ignore"):
        x = x1 + (yy - y1) * (x2 - x1) / (y2 - y1)
    x = np.where(hit, x, np.nan)
    with np.errstate(all="ignore"):
        return np.nanmin(x, axis=1), np.nanmax(x, axis=1)


def _tile(buildings):
    """[(pand_id, crown pixels, land pixels)] for one tile's buildings [(pand_id, ring)]."""
    ds, gt = _w["ds"], _w["gt"]
    res = gt[1]
    pad = config.KROON_BUFFER_M + 2 * res
    xmin = min(r[:, 0].min() for _, r in buildings) - pad
    xmax = max(r[:, 0].max() for _, r in buildings) + pad
    ymin = min(r[:, 1].min() for _, r in buildings) - pad
    ymax = max(r[:, 1].max() for _, r in buildings) + pad
    c0 = max(int((xmin - gt[0]) // res), 0)
    r0 = max(int((gt[3] - ymax) // res), 0)
    c1 = min(int(-(-(xmax - gt[0]) // res)), ds.RasterXSize)
    r1 = min(int(-(-(gt[3] - ymin) // res)), ds.RasterYSize)
    x0, y0 = gt[0] + c0 * res, gt[3] - r0 * res        # top-left of the window
    sums = []
    for b in (1, 2):
        a = ds.GetRasterBand(b).ReadAsArray(c0, r0, c1 - c0, r1 - r0).astype(np.int64)
        s = np.zeros((a.shape[0], a.shape[1] + 1), dtype=np.int64)
        np.cumsum(a, axis=1, out=s[:, 1:])
        sums.append(s)
    crown_s, land_s = sums
    nrow, ncol = crown_s.shape[0], crown_s.shape[1] - 1

    out = []
    for pid, ring in buildings:
        hull = ogr.Geometry(ogr.wkbLinearRing)
        for x, y in ring:
            hull.AddPoint_2D(float(x), float(y))
        poly = ogr.Geometry(ogr.wkbPolygon)
        poly.AddGeometry(hull)
        buf = np.array(poly.ConvexHull().Buffer(config.KROON_BUFFER_M, QUAD_SEGS).GetGeometryRef(0).GetPoints())[:, :2]
        # Rows whose cell centre lies within the buffer's y-range
        ra = max(int(np.ceil((y0 - buf[:, 1].max()) / res - 0.5)), 0)
        rb = min(int(np.floor((y0 - buf[:, 1].min()) / res - 0.5)), nrow - 1)
        rows = np.arange(ra, rb + 1)
        xl, xr = _runs(buf, y0 - (rows + 0.5) * res)
        ok = ~np.isnan(xl)
        rows, xl, xr = rows[ok], xl[ok], xr[ok]
        ja = np.clip(np.ceil((xl - x0) / res - 0.5).astype(np.int64), 0, ncol)
        jb = np.clip(np.floor((xr - x0) / res - 0.5).astype(np.int64) + 1, 0, ncol)
        jb = np.maximum(jb, ja)
        crown = int((crown_s[rows, jb] - crown_s[rows, ja]).sum())
        land = int((land_s[rows, jb] - land_s[rows, ja]).sum())
        out.append((pid, crown, land))
    return out


def load_buildings(where):
    """[(pand_id, n_woningen, gemeente, centroid x, y, hull ring)] of the 3's residential buildings."""
    ds = ogr.Open(str(config.PROCESSED_DIR / f"{config.PROVINCE_SLUG}_woningen.gpkg"))
    lyr = ds.GetLayer(0)
    if where:
        lyr.SetAttributeFilter(where)
    out = []
    for f in lyr:
        g = f.GetGeometryRef()
        hull = g.ConvexHull()
        if hull.GetGeometryType() != ogr.wkbPolygon:      # degenerate footprint
            hull = g.Centroid().Buffer(0.1, 1)
        c = g.Centroid()
        out.append((f.GetField("pand_id"), f.GetField("n_woningen") or 0, f.GetField("gemeente"),
                    c.GetX(), c.GetY(), np.array(hull.GetGeometryRef(0).GetPoints())[:, :2]))
    return out


def per_building(buildings, workers):
    """{pand_id: (crown pixels, land pixels)}, tiles of TILE_M in parallel."""
    tiles = defaultdict(list)
    for pid, _, _, x, y, ring in buildings:
        tiles[(int(x // TILE_M), int(y // TILE_M))].append((pid, ring))
    t0 = time.perf_counter()
    print(f"{len(buildings):,} buildings in {len(tiles)} tiles of {TILE_M / 1000:g} km, {workers} workers", flush=True)
    result, done = {}, 0
    with ProcessPoolExecutor(workers, initializer=_init) as pool:
        for part in pool.map(_tile, list(tiles.values())):
            for pid, crown, land in part:
                result[pid] = (crown, land)
            done += 1
            if done % 50 == 0 or done == len(tiles):
                print(f"  {done}/{len(tiles)} tiles, {len(result):,} buildings, {time.perf_counter() - t0:.0f} s", flush=True)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--gemeente", help="only this gemeente (a test); the outputs get its name")
    args = ap.parse_args()
    t0 = time.perf_counter()
    if not config.KROON_GRID_TIF.exists():
        sys.exit(f"ERROR: {config.KROON_GRID_TIF} is missing; run kroon_raster_5m.py first")

    where = f"gemeente = '{args.gemeente}'" if args.gemeente else None
    buildings = load_buildings(where)
    counts = per_building(buildings, args.workers)
    px_ha = 0.25 * 0.25 / 10_000
    slug = args.gemeente.replace(" ", "") if args.gemeente else config.PROVINCE_SLUG

    # Per building
    kroon = {}
    with open(config.PROCESSED_DIR / f"{slug}_kroon500_woningen.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["pand_id", "woningen", "kroon", "land_ha"])
        for pid, n, *_ in buildings:
            crown, land = counts[pid]
            kroon[pid] = 100 * crown / land if land else None
            w.writerow([pid, n, None if kroon[pid] is None else round(kroon[pid], 2), round(land * px_ha, 2)])

    # Up to buurt, wijk and gemeente, as the 3's stage 6 assigns buildings
    s6 = area_summaries()
    municipalities = {g for _, _, g, *_ in buildings}
    wijken, srs = s6.load_areas("wijken", "wijkcode", municipalities)
    buurten, _ = s6.load_areas("buurten", "buurtcode", municipalities)
    xs, ys = [b[3] for b in buildings], [b[4] for b in buildings]
    lookup, gt, codes = s6.buurt_lookup(buurten, srs, (min(xs) - 10, max(xs) + 10, min(ys) - 10, max(ys) + 10))
    stats = defaultdict(lambda: [0, 0, 0, 0.0])        # gebouwen, woningen, woningen >= 30%, sum of homes x kroon
    for pid, n, gemeente, x, y, _ in buildings:
        k = kroon[pid]
        if k is None:
            continue
        col, row = int((x - gt[0]) / gt[1]), int((y - gt[3]) / gt[5])
        idx = lookup[row, col] if 0 <= row < lookup.shape[0] and 0 <= col < lookup.shape[1] else 0
        targets = [("gemeente", gemeente)]
        if idx:
            b = codes[idx]
            targets += [("buurt", b), ("wijk", buurten[b]["wijkcode"])]
        for t in targets:
            s = stats[t]
            s[0] += 1
            s[1] += n
            s[2] += n if k >= THRESHOLD else 0
            s[3] += n * k
    names = {("gemeente", g): g for g in municipalities}
    names.update({("wijk", c): (w_["wijknaam"], w_["gemeentenaam"]) for c, w_ in wijken.items()})
    names.update({("buurt", c): (b["buurtnaam"], b["gemeentenaam"]) for c, b in buurten.items()})
    with open(config.PROCESSED_DIR / f"{slug}_kroon500_gebieden.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["niveau", "code", "naam", "gemeente", "gebouwen", "woningen", "woningen_30", "pct_30", "gem_kroon"])
        for (level, code), (nb, nw, n30, ksum) in sorted(stats.items()):
            naam, gem = (code, code) if level == "gemeente" else names[(level, code)]
            w.writerow([level, "" if level == "gemeente" else code, naam, gem, nb, nw, n30,
                        round(100 * n30 / nw, 1) if nw else None, round(ksum / nw, 1) if nw else None])

    tot = [s for (lvl, _), s in stats.items() if lvl == "gemeente"]
    nw, n30 = sum(s[1] for s in tot), sum(s[2] for s in tot)
    print(f"{slug}: {nw:,} homes, {100 * n30 / nw:.1f}% with >= {THRESHOLD:g}% canopy within "
          f"{config.KROON_BUFFER_M:g} m, mean {sum(s[3] for s in tot) / nw:.1f}%  ({time.perf_counter() - t0:.0f} s)")


if __name__ == "__main__":
    main()
