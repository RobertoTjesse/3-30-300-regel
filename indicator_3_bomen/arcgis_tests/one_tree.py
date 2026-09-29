"""
one_tree.py — Viewshed of one tree, or of an isolated group of trees,
computed by GDAL (this pipeline), by ArcGIS Pro and by an exact
line-of-sight reference on identical inputs, and compared with the
benchmark run (visibility_Delft) cut out around the same trees
(GitHub issue #2).

Cases (CASES below; fids in bomen_Delft_met_hoogte_uit_AHN05ruw, which
numbers the trees differently from bomen_Delft; NEO tree data):
  tree_68418  one 16 m tree, no other tree within 56 m, open surroundings
  group_5     a row of 5 trees (3-7 m, ~7 m apart), no other tree within 50 m
  tree_58448  the first test tree: 26 m, city centre, trees around it
Every result is a count: the number of the case's trees that see a cell
(the benchmark's FREQUENCY output). For a single tree that is 0/1.

The observer is set as in the benchmark: RASTERVALU (bilinear AHN5 DSM at
the tree point) + 1 m. Target 1.8 m above the DSM, 30 m radius (2D), flat
earth.

Written to arcgis_tests/one_tree/<case>/ by `prepare`:
  dem.tif      AHN5 raw DSM 0.5 m (the benchmark's DEM), the trees + 60 m,
               NoData filled as in stage 1
  dem_raw.tif  the same without the fill: exactly what the benchmark saw
  tree.shp     the trees: OBS_Z = observer elevation (m NAP), RASTERVALU
               (as in the benchmark), and the classic Viewshed tool's
               SPOT/OFFSETA/OFFSETB/RADIUS2 (2D radius); tree_3d.shp the
               same with RADIUS2 = +30
  gdal.tif, exact_bilinear.tif, exact_nearest.tif
  gdal_bench_obs.tif, exact_bench_obs.tif  the same with the observer the
               benchmark turned out to use: RASTERVALU as the observer
               OFFSET, so surface + RASTERVALU (see WORKLOG, section 9)
  pipeline_ahn5.tif  this pipeline's own per-tree logic (canopy-top
               observer, etl/02_compute_viewsheds.py) on this AHN5 dem.tif
  bench_visibility_Delft.tif  the benchmark result, cut out
  pipeline_Delft.tif          this pipeline's Delft_viewshed.tif, cut out
                              (production DEM, not AHN5)
  others.tif   1 = within 30 m of a tree NOT in the case: the benchmark and
               pipeline counts include that tree there, so compare skips it
  case.json    the trees and their observer elevations
`compare` adds diff_<result>.tif (vs the exact test) and tree_ring.gpkg
for the QGIS validation project (qgis_validation/build_project.py).

Usage:
    python indicator_3_bomen/arcgis_tests/one_tree.py prepare [case|all]   # default: all
    (run arcgis_tests/one_tree_arcgis.py in ArcGIS Pro — writes arc_*.tif)
    python indicator_3_bomen/arcgis_tests/one_tree.py compare [case|all]
"""

import json
import sys
from pathlib import Path

import numpy as np
from scipy.ndimage import map_coordinates

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "indicator_3_bomen" / "etl"))
import config  # noqa: E402,F401  (GDAL environment)

from osgeo import gdal, ogr, osr  # noqa: E402
gdal.UseExceptions()
ogr.UseExceptions()

REFERENCE_GDB = r"R:/ESRI/DATA/RUIMTELIJKE ONTWIKKELING/PERSOONLIJK/Chris/test/data.gdb"
BENCHMARK = f'OpenFileGDB:"{REFERENCE_GDB}":visibility_Delft'   # the reference Visibility run
PIPELINE = REPO / "data" / "processed" / "Delft_viewshed.tif"   # this pipeline's Delft result
BENCH_NODATA = -2147483647                   # visibility_Delft's undeclared NoData
ROOT = REPO / "indicator_3_bomen" / "arcgis_tests" / "one_tree"
# Test cases: folder name -> tree fids in bomen_Delft_met_hoogte_uit_AHN05ruw
CASES = {
    "tree_68418": [68418],
    "group_5": [15689, 17046, 17707, 17708, 17717],
    "tree_58448": [58448],
}
HALF = 60.0                                  # DEM clip: the trees +/- 60 m
CELL = 0.5
RADIUS = 30.0
TARGET_HEIGHT = 1.8
OBSERVER_OFFSET = 1.0                        # the Visibility tool's default, as in the benchmark


def write_raster(path, array, gt, wkt, dtype=gdal.GDT_Byte):
    """Save a numpy array as a compressed GeoTIFF on the given grid."""
    ds =gdal.GetDriverByName("GTiff").Create(str(path), array.shape[1], array.shape[0], 1, dtype,
                                              options=["COMPRESS=DEFLATE"])
    ds.SetGeoTransform(gt)
    ds.SetProjection(wkt)
    ds.GetRasterBand(1).WriteArray(array)
    ds = None


def load_dem(folder):
    """The case's filled DEM: (heights as float64, geotransform, projection)."""
    ds =gdal.Open(str(folder / "dem.tif"))
    return ds.GetRasterBand(1).ReadAsArray().astype(np.float64), ds.GetGeoTransform(), ds.GetProjection()


def load_case(case):
    """The case's trees as [{fid, x, y, rastervalu, obs_z}], from case.json
    or (first time) from bomen_Delft_met_hoogte_uit_AHN05ruw."""
    folder = ROOT / case
    info = folder / "case.json"
    if info.exists():
        return json.loads(info.read_text())
    ds = ogr.Open(REFERENCE_GDB)
    layer = ds.GetLayerByName("bomen_Delft_met_hoogte_uit_AHN05ruw")
    trees = []
    for fid in CASES[case]:
        f = layer.GetFeature(fid)
        g = f.GetGeometryRef()
        rv = f.GetField("RASTERVALU")
        if rv is None:
            raise SystemExit(f"Tree {fid} has no RASTERVALU (NoData): the benchmark skipped it")
        trees.append({"fid": fid, "x": g.GetX(), "y": g.GetY(), "rastervalu": rv,
                      "obs_z": rv + OBSERVER_OFFSET})
    ds = None
    folder.mkdir(parents=True, exist_ok=True)
    info.write_text(json.dumps(trees, indent=1))
    return trees


def cell_centres(shape, gt):
    """x and y of every cell centre of a grid, as two arrays."""
    rows, cols = np.mgrid[0:shape[0], 0:shape[1]]
    return gt[0] + (cols + 0.5) * CELL, gt[3] - (rows + 0.5) * CELL


def exact_visibility(dem, gt, tree, order):
    """Exact sightline test from one tree to every cell centre within
    RADIUS; order 1 = bilinear surface between cell centres, 0 = every cell
    a flat square. Samples every <= 0.1 m; the target cell does not block."""
    # Observer (o) and every target cell within RADIUS (t)
    ox, oy, oz = tree["x"], tree["y"], tree["obs_z"]
    cx, cy = cell_centres(dem.shape, gt)
    rows, cols = np.mgrid[0:dem.shape[0], 0:dem.shape[1]]
    d = np.hypot(cx - ox, cy - oy)
    inside = d <= RADIUS
    rows, cols, tx, ty, d = rows[inside], cols[inside], cx[inside], cy[inside], d[inside]
    # Sample points along every sightline: t runs from just after the
    # observer (0) to just before the target (1); one row per target
    n = int(RADIUS / 0.1)
    t = (np.arange(1, n) / n)[None, :]
    sx, sy = ox + t * (tx - ox)[:, None], oy + t * (ty - oy)[:, None]
    # The surface height under each sample point
    scol, srow = (sx - gt[0]) / CELL - 0.5, (gt[3] - sy) / CELL - 0.5
    if order == 0:
        scol, srow = np.floor(scol + 0.5), np.floor(srow + 0.5)
    surf = map_coordinates(dem, [srow.ravel(), scol.ravel()], order=order, mode="nearest").reshape(sx.shape)
    # The sightline's height at each sample point, towards eye height above
    # the target; a target is visible if the surface never rises above it
    # (except within the target cell itself)
    tz = dem[rows, cols] + TARGET_HEIGHT
    line = oz + t * (tz - oz)[:, None]
    near_target = (1 - t) * d[:, None] < CELL / 2
    visible = ~((surf > line) & ~near_target).any(axis=1)
    out = np.zeros(dem.shape, np.uint8)
    out[rows[visible], cols[visible]] = 1
    return out


def gdal_visibility(folder, dem, gt, tree):
    """GDAL, as the pipeline calls it (observerHeight is added to the
    observer cell's DEM value), on the DEM grid."""
    ds = gdal.Open(str(folder / "dem.tif"))
    c, r = int((tree["x"] - gt[0]) / CELL), int((gt[3] - tree["y"]) / CELL)
    out = gdal.ViewshedGenerate(srcBand=ds.GetRasterBand(1), driverName="MEM", targetRasterName="",
                                creationOptions=[], observerX=tree["x"], observerY=tree["y"],
                                observerHeight=tree["obs_z"] - dem[r, c], targetHeight=TARGET_HEIGHT,
                                visibleVal=1, invisibleVal=0, outOfRangeVal=0, noDataVal=0,
                                dfCurvCoeff=0, mode=gdal.GVM_Edge, maxDistance=RADIUS)
    # GDAL returns a window around the observer; place it on the full clip
    v = out.GetRasterBand(1).ReadAsArray()
    ogt = out.GetGeoTransform()
    full = np.zeros(dem.shape, np.uint8)
    co, ro = round((ogt[0] - gt[0]) / CELL), round((gt[3] - ogt[3]) / CELL)
    full[ro:ro + v.shape[0], co:co + v.shape[1]] = v
    return full


def pipeline_visibility(folder, dem, gt, wkt, trees):
    """This pipeline's own per-tree logic (etl/02_compute_viewsheds.py:
    canopy-top observer ignoring building pixels, plausibility check, GDAL
    ViewshedGenerate), run on this case's AHN5 dem.tif instead of the
    production DEM — so it is comparable with the benchmark's surface."""
    import importlib
    mod = importlib.import_module("02_compute_viewsheds")
    buildings = mod._building_mask(gt, dem.shape[1], dem.shape[0], wkt)
    total = np.zeros(dem.shape, np.uint8)
    for t in trees:
        # Stage 2's steps for one tree: small DEM window + observer offset,
        # viewshed, add the result to the running count
        prepared = mod._prepare_tree(dem, buildings, gt, t["x"], t["y"])
        window, wgt, obs_h, info = prepared
        mem = gdal.GetDriverByName("MEM").Create("", window.shape[1], window.shape[0], 1, gdal.GDT_Float32)
        mem.SetGeoTransform(wgt)
        mem.SetProjection(wkt)
        mem.GetRasterBand(1).WriteArray(window)
        arr, agt = mod._viewshed_python_api(mem.GetRasterBand(1), t["x"], t["y"], obs_h)
        co, ro = round((agt[0] - gt[0]) / CELL), round((gt[3] - agt[3]) / CELL)
        total[ro:ro + arr.shape[0], co:co + arr.shape[1]] += (arr > 0).astype(np.uint8)
        top = f"{info['canopy_top']:.2f}" if info["canopy_top"] is not None else "-"
        print(f"  pipeline observer, tree {t['fid']}: canopy top {top} m NAP, offset {obs_h:.2f} m"
              f"{'' if info['plausible'] else ' (implausible: default offset)'}"
              f" — benchmark-style observer {t['obs_z']:.2f}")
    write_raster(folder / "pipeline_ahn5.tif", total, gt, wkt)


def prepare(case):
    """Write everything for one case: the DEM clip (filled and raw), the
    benchmark and pipeline cut-outs, the other-trees mask, the tree points
    for ArcGIS, and the GDAL, exact and pipeline results."""
    folder = ROOT / case
    trees = load_case(case)
    print(f"\n=== {case}: {len(trees)} tree(s), observer = RASTERVALU + {OBSERVER_OFFSET:.0f} m: "
          + ", ".join(f"{t['fid']} {t['obs_z']:.2f}" for t in trees) + " m NAP")

    # DEM clip from the benchmark's DEM, snapped to its grid
    src = gdal.Open(f'OpenFileGDB:"{REFERENCE_GDB}":AHN5ruw05m_Delft')
    sgt = src.GetGeoTransform()
    xs, ys = [t["x"] for t in trees], [t["y"] for t in trees]
    x0 = sgt[0] + round((min(xs) - HALF - sgt[0]) / CELL) * CELL
    y1 = sgt[3] - round((sgt[3] - (max(ys) + HALF)) / CELL) * CELL
    nx = int(round((max(xs) + HALF - x0) / CELL))
    ny = int(round((y1 - (min(ys) - HALF)) / CELL))
    window = [x0, y1, x0 + nx * CELL, y1 - ny * CELL]
    for name in ("dem.tif", "dem_raw.tif"):
        gdal.Translate(str(folder / name), src, projWin=window, outputType=gdal.GDT_Float32,
                       creationOptions=["COMPRESS=DEFLATE"])
    # dem.tif: fill NoData (water) exactly as stage 1 does, and drop the
    # flag, so GDAL and the exact test see a surface without a sentinel
    ds = gdal.Open(str(folder / "dem.tif"), gdal.GA_Update)
    b = ds.GetRasterBand(1)
    nd = b.GetNoDataValue()
    raw = b.ReadAsArray()
    n_nodata = int((raw == np.float32(nd)).sum()) if nd is not None else 0
    if n_nodata:
        gdal.FillNodata(targetBand=b, maskBand=None, maxSearchDist=50, smoothingIterations=1)
        a = b.ReadAsArray()
        rest = a == np.float32(nd)
        if rest.any():
            a[rest] = a[~rest].min()
            b.WriteArray(a)
    if nd is not None:
        b.DeleteNoDataValue()
    ds = None
    dem, gt, wkt = load_dem(folder)
    print(f"dem.tif {dem.shape}, {n_nodata} NoData cells filled (dem_raw.tif keeps them), "
          f"range {dem.min():.2f} .. {dem.max():.2f} m")

    # The benchmark and this pipeline's result, cut out on the same grid
    gdal.Translate(str(folder / "bench_visibility_Delft.tif"), gdal.Open(BENCHMARK), projWin=window,
                   noData=BENCH_NODATA, creationOptions=["COMPRESS=DEFLATE"])
    if PIPELINE.exists():
        gdal.Translate(str(folder / "pipeline_Delft.tif"), gdal.Open(str(PIPELINE)), projWin=window,
                       creationOptions=["COMPRESS=DEFLATE"])

    # Cells within RADIUS of a tree outside the case: the counts there include it
    cx, cy = cell_centres(dem.shape, gt)
    others = np.zeros(dem.shape, bool)
    tds = ogr.Open(REFERENCE_GDB)
    layer = tds.GetLayerByName("bomen_Delft")
    layer.SetSpatialFilterRect(window[0] - RADIUS, window[3] - RADIUS, window[2] + RADIUS, window[1] + RADIUS)
    n_others = 0
    for f in layer:
        g = f.GetGeometryRef()
        if any(abs(g.GetX() - t["x"]) < 0.01 and abs(g.GetY() - t["y"]) < 0.01 for t in trees):
            continue                     # a case tree (fids differ between the two tree layers)
        others |= np.hypot(cx - g.GetX(), cy - g.GetY()) <= RADIUS
        n_others += 1
    tds = None
    write_raster(folder / "others.tif", others.astype(np.uint8), gt, wkt)
    cover = np.zeros(dem.shape, bool)
    for t in trees:
        cover |= np.hypot(cx - t["x"], cy - t["y"]) <= RADIUS
    print(f"{n_others} other trees near the clip; {100 * others[cover].mean():.1f}% of the case's "
          f"{RADIUS:.0f} m area is also within {RADIUS:.0f} m of one of them (skipped in compare)")

    # Tree points. OBS_Z is passed to Viewshed2 / Visibility, RASTERVALU to
    # the benchmark variant; the classic Viewshed tool reads its fixed field
    # names SPOT (observer elevation), OFFSETA (observer offset), OFFSETB
    # (target offset) and RADIUS2 (outer radius) — hence tree.shp (-30) and
    # tree_3d.shp (+30), although it turned out to ignore the sign.
    srs = osr.SpatialReference()
    srs.ImportFromWkt(wkt)
    drv = ogr.GetDriverByName("ESRI Shapefile")
    for fname, radius2 in (("tree.shp", -RADIUS), ("tree_3d.shp", RADIUS)):
        shp = folder / fname
        if shp.exists():
            drv.DeleteDataSource(str(shp))
        vds = drv.CreateDataSource(str(shp))
        layer = vds.CreateLayer(shp.stem, srs, ogr.wkbPoint)
        names = ["FID_BOOM", "OBS_Z", "RASTERVALU", "SPOT", "OFFSETA", "OFFSETB", "RADIUS2"]
        for k in names:
            layer.CreateField(ogr.FieldDefn(k, ogr.OFTInteger if k == "FID_BOOM" else ogr.OFTReal))
        for t in trees:
            f = ogr.Feature(layer.GetLayerDefn())
            for k, v in zip(names, (t["fid"], t["obs_z"], t["rastervalu"], t["obs_z"], 0.0,
                                    TARGET_HEIGHT, radius2)):
                f.SetField(k, v)
            f.SetGeometry(ogr.CreateGeometryFromWkt(f"POINT ({t['x']} {t['y']})"))
            layer.CreateFeature(f)
        vds = None

    # GDAL and the exact test, summed over the trees (= FREQUENCY)
    write_raster(folder / "gdal.tif", sum(gdal_visibility(folder, dem, gt, t) for t in trees), gt, wkt)
    for name, order in (("exact_bilinear", 1), ("exact_nearest", 0)):
        write_raster(folder / f"{name}.tif", sum(exact_visibility(dem, gt, t, order) for t in trees), gt, wkt)
    # The same with the observer the benchmark turned out to use: RASTERVALU
    # as the observer OFFSET, i.e. surface (bilinear) + RASTERVALU
    as_run = [dict(t, obs_z=2 * t["rastervalu"]) for t in trees]
    write_raster(folder / "gdal_bench_obs.tif", sum(gdal_visibility(folder, dem, gt, t) for t in as_run),
                 gt, wkt)
    write_raster(folder / "exact_bench_obs.tif", sum(exact_visibility(dem, gt, t, 1) for t in as_run),
                 gt, wkt)
    pipeline_visibility(folder, dem, gt, wkt, trees)
    print(f"Wrote the inputs, gdal.tif, exact_bilinear.tif, exact_nearest.tif and pipeline_ahn5.tif in {folder}")


def read_counts(path, shape, gt):
    """A result as counts on the DEM grid; NoData = 0 (Viewshed2 writes 'not
    visible' as NoData). Also returns the number of NoData cells."""
    rds = gdal.Open(str(path))
    rgt = rds.GetGeoTransform()
    b = rds.GetRasterBand(1)
    a = b.ReadAsArray().astype(np.int64)
    nd = b.GetNoDataValue()
    nodata = np.zeros(a.shape, bool) if nd is None else a == nd
    a[nodata] = 0
    # The result may cover a different extent: copy the overlapping part onto
    # the DEM grid (cells it doesn't cover stay 0)
    full, full_nd = np.zeros(shape, np.int64), np.zeros(shape, bool)
    co, ro = round((rgt[0] - gt[0]) / CELL), round((gt[3] - rgt[3]) / CELL)
    r0, c0 = max(ro, 0), max(co, 0)
    r1, c1 = min(ro + a.shape[0], shape[0]), min(co + a.shape[1], shape[1])
    full[r0:r1, c0:c1] = a[r0 - ro:r1 - ro, c0 - co:c1 - co]
    full_nd[r0:r1, c0:c1] = nodata[r0 - ro:r1 - ro, c0 - co:c1 - co]
    return full, full_nd


def compare(case):
    """Print how every result of one case compares with the exact test and
    with the benchmark, and write the difference maps for QGIS."""
    folder = ROOT / case
    if not (folder / "dem.tif").exists():
        print(f"\n=== {case}: not prepared — run `one_tree.py prepare {case}` first")
        return
    trees = load_case(case)
    dem, gt, wkt = load_dem(folder)
    cx, cy = cell_centres(dem.shape, gt)
    d = np.min([np.hypot(cx - t["x"], cy - t["y"]) for t in trees], axis=0)   # to the nearest case tree
    circle = d <= RADIUS
    others = read_counts(folder / "others.tif", dem.shape, gt)[0] > 0
    clean = circle & ~others           # cells only the case's trees can see
    raw_nd = read_counts(folder / "dem_raw.tif", dem.shape, gt)[1]

    # Every result raster in the case folder (not the inputs or diff maps)
    results, nodata = {}, {}
    for path in sorted(folder.glob("*.tif")):
        if path.stem in ("dem", "dem_raw", "others") or path.stem.startswith("diff_"):
            continue
        results[path.stem], nd = read_counts(path, dem.shape, gt)
        nodata[path.stem] = nd
    ref, bench = results.get("exact_bilinear"), results.get("bench_visibility_Delft")
    counts = {"bench_visibility_Delft", "pipeline_Delft"}     # include trees outside the case
    rings = [(0, 5), (5, 10), (10, 20), (20, 30)]

    def row(name, v, area, compare_to):
        """Print one table row: share seen overall and per distance ring,
        mean count, and agreement with each raster in compare_to."""
        seen = v > 0
        ring = " ".join(f"{100 * seen[area & (d >= a) & (d < b)].mean():6.1f}%"
                        if (area & (d >= a) & (d < b)).any() else f"{'-':>7s}" for a, b in rings)
        eq = " ".join(f"{100 * (v == c)[area].mean():6.1f}%" if c is not None else f"{'-':>7s}"
                      for c in compare_to)
        notes = []
        outside = int((seen & ~circle).sum())
        if outside and name not in counts:
            notes.append(f"{outside} cells seen beyond {RADIUS:.0f} m")
        if (nodata[name] & area).any():
            notes.append(f"{int((nodata[name] & area).sum())} NoData cells")
        print(f"{name:24s} {100 * seen[area].mean():5.1f}% {ring} {v[area].mean():5.2f} {eq}"
              + (f"   ({'; '.join(notes)})" if notes else ""))

    header = f"{'':24s} {'>=1':>6s} " + " ".join(f"{f'{a}-{b} m':>7s}" for a, b in rings) + f" {'mean':>5s}"
    print(f"\n=== {case}: {len(trees)} tree(s), {circle.sum():,} cells within {RADIUS:.0f} m; "
          f"{int((raw_nd & circle).sum())} of them NoData in the raw DSM (filled in dem.tif)")
    print(f"\n1. The tools vs the exact test, all {circle.sum():,} cells: share of cells seen by "
          f">= 1 tree (by distance to the nearest tree), mean count, share with the same count as exact\n")
    print(header + f" {'=exact':>7s}")
    for name, v in results.items():
        if name not in counts:
            row(name, v, circle, [ref])

    print(f"\n2. Reproducing the benchmark: the {clean.sum():,} cells that no tree outside the case "
          f"can see ({int((circle & others).sum()):,} skipped); same columns + share with the same "
          f"count as the benchmark\n")
    if not clean.any() or bench is None:
        print("   (no such cells — every cell is also within 30 m of another tree)" if not clean.any()
              else "   (bench_visibility_Delft.tif missing — run prepare)")
    else:
        print(header + f" {'=exact':>7s} {'=bench':>7s}")
        for name, v in results.items():
            row(name, v, clean, [ref, bench])

    # For the QGIS validation project: per result a difference map vs the
    # exact test (1 = the result counts more trees, 2 = fewer, 0 = same),
    # plus the trees and their 30 m circles
    if ref is not None:
        for name, v in results.items():
            if name != "exact_bilinear":
                diff = np.where(v > ref, 1, np.where(v < ref, 2, 0)).astype(np.uint8)
                write_raster(folder / f"diff_{name}.tif", diff, gt, wkt)
    write_tree_layers(folder / "tree_ring.gpkg", trees, wkt)


def write_tree_layers(path, trees, wkt):
    """The tree points and the outline of their 30 m circles, as two layers."""
    drv = ogr.GetDriverByName("GPKG")
    if path.exists():
        drv.DeleteDataSource(str(path))
    ds = drv.CreateDataSource(str(path))
    srs = osr.SpatialReference()
    srs.ImportFromWkt(wkt)
    points = [ogr.CreateGeometryFromWkt(f"POINT ({t['x']} {t['y']})") for t in trees]
    circles = points[0].Buffer(RADIUS, 64)
    for p in points[1:]:
        circles = circles.Union(p.Buffer(RADIUS, 64))
    for name, geoms, gtype in (("tree", points, ogr.wkbPoint),
                               ("ring30", [ogr.ForceToMultiLineString(circles.Boundary())], ogr.wkbMultiLineString)):
        layer = ds.CreateLayer(name, srs, gtype)
        for g in geoms:
            f = ogr.Feature(layer.GetLayerDefn())
            f.SetGeometry(g)
            layer.CreateFeature(f)
    ds = None


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "compare"
    which = sys.argv[2] if len(sys.argv) > 2 else "all"
    for c in (CASES if which == "all" else [which]):
        {"prepare": prepare, "compare": compare}[action](c)
    if action == "compare":
        print("\nDifference maps written; add them to QGIS with qgis_validation\\build_project.py")
