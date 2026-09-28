"""
one_tree.py — Viewshed of ONE tree, computed by GDAL (this pipeline), by
ArcGIS Pro and by an exact line-of-sight reference, on identical inputs,
to find where and why the tools differ (GitHub issue #2).

The tree: a ~26 m tree 245 m from the Markt in Delft (NEO tree data,
bomen_Delft). For this tree the two observer-height rules nearly coincide
(DSM at the point + 1 m = 27.85 m NAP; canopy top 27.73 m), so every
difference comes from the viewshed calculation itself.

Shared inputs, written to arcgis_tests/one_tree/ by `prepare`:
  dem.tif      AHN5 raw DSM 0.5 m (the reference run's DEM), 60 m around the tree
  tree.shp     the tree point, field OBS_Z = absolute observer elevation (m NAP)
Settings: observer at OBS_Z (offset 0), target 1.8 m above the DSM, 30 m radius
(2D), flat earth.

Usage:
    python arcgis_tests/one_tree.py prepare    # inputs + GDAL + exact reference
    (run arcgis_tests/one_tree_arcgis.py in ArcGIS Pro — writes arc_*.tif)
    python arcgis_tests/one_tree.py compare    # compare everything found
"""

import sys
from pathlib import Path

import numpy as np
from scipy.ndimage import map_coordinates

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "etl"))
import config  # noqa: E402,F401  (GDAL environment)

from osgeo import gdal, ogr, osr  # noqa: E402
gdal.UseExceptions()
ogr.UseExceptions()

REFERENCE_GDB = r"R:/ESRI/DATA/RUIMTELIJKE ONTWIKKELING/PERSOONLIJK/Chris/test/data.gdb"
DIR = REPO / "arcgis_tests" / "one_tree"
TREE_X, TREE_Y = 84274.25, 447451.50        # bomen_Delft fid 58448
OBS_Z = 26.85 + 1.0                          # RASTERVALU (bilinear AHN5 at the point) + 1 m
HALF = 60.0                                  # DEM clip: tree +/- 60 m
CELL = 0.5
RADIUS = 30.0
TARGET_HEIGHT = 1.8


def write_raster(path, array, gt, wkt, dtype=gdal.GDT_Byte):
    ds = gdal.GetDriverByName("GTiff").Create(str(path), array.shape[1], array.shape[0], 1, dtype,
                                              options=["COMPRESS=DEFLATE"])
    ds.SetGeoTransform(gt)
    ds.SetProjection(wkt)
    ds.GetRasterBand(1).WriteArray(array)
    ds = None


def load_dem():
    ds = gdal.Open(str(DIR / "dem.tif"))
    return ds, ds.GetRasterBand(1).ReadAsArray().astype(np.float64), ds.GetGeoTransform(), ds.GetProjection()


def exact_visibility(dem, gt, order):
    """Exact sightline test to every cell centre within RADIUS; order 1 =
    bilinear surface between cell centres, 0 = every cell a flat square.
    Samples every <= 0.1 m; the target cell itself does not block."""
    ny, nx = dem.shape
    rows, cols = np.mgrid[0:ny, 0:nx]
    tx, ty = gt[0] + (cols + 0.5) * CELL, gt[3] - (rows + 0.5) * CELL
    d = np.hypot(tx - TREE_X, ty - TREE_Y)
    inside = d <= RADIUS
    rows, cols, tx, ty, d = rows[inside], cols[inside], tx[inside], ty[inside], d[inside]
    n = int(RADIUS / 0.1)
    t = (np.arange(1, n) / n)[None, :]
    sx, sy = TREE_X + t * (tx - TREE_X)[:, None], TREE_Y + t * (ty - TREE_Y)[:, None]
    scol, srow = (sx - gt[0]) / CELL - 0.5, (gt[3] - sy) / CELL - 0.5
    if order == 0:
        scol, srow = np.floor(scol + 0.5), np.floor(srow + 0.5)
    surf = map_coordinates(dem, [srow.ravel(), scol.ravel()], order=order, mode="nearest").reshape(sx.shape)
    tz = dem[rows, cols] + TARGET_HEIGHT
    line = OBS_Z + t * (tz - OBS_Z)[:, None]
    near_target = (1 - t) * d[:, None] < CELL / 2
    visible = ~((surf > line) & ~near_target).any(axis=1)
    out = np.zeros(dem.shape, np.uint8)
    out[rows[visible], cols[visible]] = 1
    return out


def prepare():
    DIR.mkdir(parents=True, exist_ok=True)
    # DEM clip from the reference run's DEM
    src = gdal.Open(f'OpenFileGDB:"{REFERENCE_GDB}":AHN5ruw05m_Delft')
    sgt = src.GetGeoTransform()
    x0 = sgt[0] + round((TREE_X - HALF - sgt[0]) / CELL) * CELL      # snap to the source grid
    y1 = sgt[3] - round((sgt[3] - (TREE_Y + HALF)) / CELL) * CELL
    n = int(2 * HALF / CELL)
    gdal.Translate(str(DIR / "dem.tif"), src, projWin=[x0, y1, x0 + n * CELL, y1 - n * CELL],
                   outputType=gdal.GDT_Float32, creationOptions=["COMPRESS=DEFLATE"])
    # Fill NoData (water) exactly as stage 1 does, and drop the flag: every
    # tool then sees the same surface, with no sentinel to interpret
    ds = gdal.Open(str(DIR / "dem.tif"), gdal.GA_Update)
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
    ds, dem, gt, wkt = load_dem()
    print(f"dem.tif {dem.shape}, {n_nodata} NoData cells filled, range {dem.min():.2f} .. {dem.max():.2f} m")
    ds = None

    # tree point with the absolute observer elevation
    srs = osr.SpatialReference()
    srs.ImportFromWkt(wkt)
    shp = DIR / "tree.shp"
    drv = ogr.GetDriverByName("ESRI Shapefile")
    if shp.exists():
        drv.DeleteDataSource(str(shp))
    vds = drv.CreateDataSource(str(shp))
    layer = vds.CreateLayer("tree", srs, ogr.wkbPoint)
    layer.CreateField(ogr.FieldDefn("OBS_Z", ogr.OFTReal))
    f = ogr.Feature(layer.GetLayerDefn())
    f.SetField("OBS_Z", OBS_Z)
    f.SetGeometry(ogr.CreateGeometryFromWkt(f"POINT ({TREE_X} {TREE_Y})"))
    layer.CreateFeature(f)
    vds = None

    # GDAL, as the pipeline calls it (observerHeight is added to the observer cell's DEM value)
    ds = gdal.Open(str(DIR / "dem.tif"))
    c, r = int((TREE_X - gt[0]) / CELL), int((gt[3] - TREE_Y) / CELL)
    out = gdal.ViewshedGenerate(srcBand=ds.GetRasterBand(1), driverName="MEM", targetRasterName="",
                                creationOptions=[], observerX=TREE_X, observerY=TREE_Y,
                                observerHeight=OBS_Z - dem[r, c], targetHeight=TARGET_HEIGHT,
                                visibleVal=1, invisibleVal=0, outOfRangeVal=0, noDataVal=0,
                                dfCurvCoeff=0, mode=gdal.GVM_Edge, maxDistance=RADIUS)
    v = out.GetRasterBand(1).ReadAsArray()
    ogt = out.GetGeoTransform()
    full = np.zeros(dem.shape, np.uint8)
    co, ro = round((ogt[0] - gt[0]) / CELL), round((gt[3] - ogt[3]) / CELL)
    full[ro:ro + v.shape[0], co:co + v.shape[1]] = v
    write_raster(DIR / "gdal.tif", full, gt, wkt)

    for name, order in (("exact_bilinear", 1), ("exact_nearest", 0)):
        write_raster(DIR / f"{name}.tif", exact_visibility(dem, gt, order), gt, wkt)
    print(f"Wrote dem.tif, tree.shp, gdal.tif, exact_bilinear.tif, exact_nearest.tif in {DIR}")


def compare():
    ds, dem, gt, wkt = load_dem()
    ny, nx = dem.shape
    rows, cols = np.mgrid[0:ny, 0:nx]
    d = np.hypot(gt[0] + (cols + 0.5) * CELL - TREE_X, gt[3] - (rows + 0.5) * CELL - TREE_Y)
    inside = d <= RADIUS
    results = {}
    for path in sorted(DIR.glob("*.tif")):
        if path.stem == "dem":
            continue
        rds = gdal.Open(str(path))
        rgt = rds.GetGeoTransform()
        a = rds.GetRasterBand(1).ReadAsArray().astype(float)
        nd = rds.GetRasterBand(1).GetNoDataValue()
        if nd is not None:
            a[a == nd] = 0                                  # Viewshed2: not visible = NoData
        full = np.zeros(dem.shape)
        co, ro = round((rgt[0] - gt[0]) / CELL), round((gt[3] - rgt[3]) / CELL)
        r0, c0 = max(ro, 0), max(co, 0)
        r1, c1 = min(ro + a.shape[0], ny), min(co + a.shape[1], nx)
        full[r0:r1, c0:c1] = a[r0 - ro:r1 - ro, c0 - co:c1 - co]
        results[path.stem] = full > 0
    ref = results.get("exact_bilinear")
    rings = [(0, 5), (5, 10), (10, 20), (20, 30)]
    print(f"{inside.sum():,} cells within {RADIUS:.0f} m of the tree; share visible:\n")
    print(f"{'':26s} {'all':>6s} " + " ".join(f"{f'{a}-{b} m':>8s}" for a, b in rings)
          + "   agree with exact_bilinear")
    for name, v in results.items():
        ring = " ".join(f"{100 * v[inside & (d >= a) & (d < b)].mean():7.1f}%" for a, b in rings)
        agree = f"{100 * (v == ref)[inside].mean():6.1f}%" if ref is not None else ""
        outside = (v & ~inside).sum()
        print(f"{name:26s} {100 * v[inside].mean():5.1f}% {ring}   {agree}"
              + (f"   ({outside} visible cells beyond {RADIUS:.0f} m)" if outside else ""))


if __name__ == "__main__":
    {"prepare": prepare, "compare": compare}[sys.argv[1] if len(sys.argv) > 1 else "prepare"]()
