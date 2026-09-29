"""
studiegebied.py — Reproduce the colleague's benchmark (visibility_Delft) for
one small study area: a Delft buurt plus a 30 m buffer. First with ArcGIS
(studiegebied_arcgis.py, the benchmark's own tool and settings), then with
GDAL (this file), and compare both with the benchmark cut out on the same
cells.

Why a buffer: a cell in the buurt can see every tree within 30 m, so the
trees in the 30 m buffer around the buurt count too, and lines of sight
from those trees run over the buffer. With the DEM and the trees of
buurt + buffer, every cell INSIDE the buurt gets exactly the inputs it got
in the full-Delft benchmark run. Cells in the buffer itself miss trees
further out, so they are never compared.

The benchmark's settings (visibility_Delft; shown in arcgis_tests/one_tree.py,
WORKLOG section 9 — reproduced on 100% of cells):
  ArcGIS Visibility, FREQUENCY (number of trees that see a cell), non-visible 0
  DEM          AHN5ruw05m_Delft, raw DSM 0.5 m, NoData not filled
  trees        bomen_Delft_met_hoogte_uit_AHN05ruw, trees with a RASTERVALU
               (bilinear AHN5 value at the tree point)
  observer     observer_elevation = RASTERVALU AND observer_offset = RASTERVALU,
               i.e. at 2 x RASTERVALU m NAP
  target       surface_offset 1.8 m;  outer radius 30 m (2D);  flat earth

Written to data/studiegebied/<buurtcode>/ (gitignored):
  studiegebied.gpkg  layers buurt, buffer30 (the study area), bomen (the trees)
  dem_raw.tif        the benchmark DSM, cut out: what the benchmark saw
  dem.tif            the same with NoData filled as in stage 1 (for GDAL,
                     which would read the NoData value as a height)
  bomen.shp          the trees for ArcGIS: FID_BOOM, RASTERVALU
  buurt.tif          1 = cell centre inside the buurt (the compared cells)
  benchmark.tif      visibility_Delft, cut out
  arcgis.tif         studiegebied_arcgis.py (ArcGIS Visibility, benchmark settings)
  gdal.tif           `gdal` below
  verschil_<x>.tif   `compare`: 1 = x counts more trees than the benchmark,
                     2 = fewer, 0 = the same (inside the buurt only)

Usage (OSGeo4W Python), in this order:
    python indicator_3_bomen/arcgis_tests/studiegebied.py prepare [buurtcode]   # default Molenbuurt
    (ArcGIS Pro Python Command Prompt:  python indicator_3_bomen/arcgis_tests/studiegebied_arcgis.py)
    python indicator_3_bomen/arcgis_tests/studiegebied.py gdal
    python indicator_3_bomen/arcgis_tests/studiegebied.py compare
"""

import json
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "indicator_3_bomen" / "etl"))
import config  # noqa: E402,F401  (GDAL environment)

from osgeo import gdal, ogr, osr  # noqa: E402
gdal.UseExceptions()
ogr.UseExceptions()

BUURT = "BU05031407"                         # Molenbuurt, Delft (holds the earlier test area)
REFERENCE_GDB = r"R:/ESRI/DATA/RUIMTELIJKE ONTWIKKELING/PERSOONLIJK/Chris/test/data.gdb"
BENCH_DEM = f'OpenFileGDB:"{REFERENCE_GDB}":AHN5ruw05m_Delft'
BENCH_TREES = "bomen_Delft_met_hoogte_uit_AHN05ruw"
BENCHMARK = f'OpenFileGDB:"{REFERENCE_GDB}":visibility_Delft'
BENCH_NODATA = -2147483647                   # visibility_Delft's undeclared NoData
CBS_BUURTEN = "WFS:https://service.pdok.nl/cbs/wijkenbuurten/2025/wfs/v1_0"
ROOT = REPO / "data" / "studiegebied"

CELL = 0.5
RADIUS = 30.0                                # the benchmark's outer radius (2D)
BUFFER = RADIUS + CELL                       # trees / DEM: 30 m + one cell, so no tree just
                                             # within 30 m of a buurt cell centre is missed
TARGET_HEIGHT = 1.8                          # the benchmark's surface_offset


def folder_for(code):
    """data/studiegebied/<buurtcode>/"""
    return ROOT / code


def current():
    """The study area prepared last (data/studiegebied/current.json)."""
    info = ROOT / "current.json"
    if not info.exists():
        sys.exit("No study area yet — run `python indicator_3_bomen/arcgis_tests/studiegebied.py prepare` first")
    return folder_for(json.loads(info.read_text())["buurtcode"])


def write_raster(path, array, gt, wkt, dtype=gdal.GDT_Byte, nodata=None):
    """Save a numpy array as a compressed GeoTIFF on the given grid."""
    ds =gdal.GetDriverByName("GTiff").Create(str(path), array.shape[1], array.shape[0], 1, dtype,
                                              options=["COMPRESS=DEFLATE"])
    ds.SetGeoTransform(gt)
    ds.SetProjection(wkt)
    if nodata is not None:
        ds.GetRasterBand(1).SetNoDataValue(nodata)
    ds.GetRasterBand(1).WriteArray(array)
    ds = None


def read(path):
    """A raster as (array, geotransform, projection, NoData value)."""
    ds =gdal.Open(str(path))
    b = ds.GetRasterBand(1)
    return b.ReadAsArray(), ds.GetGeoTransform(), ds.GetProjection(), b.GetNoDataValue()


def fetch_buurt(code):
    """The buurt polygon (CBS Wijk- en Buurtkaart 2025, PDOK) in RD New."""
    ds = gdal.OpenEx(CBS_BUURTEN, gdal.OF_VECTOR)
    layer = ds.GetLayerByName("wijkenbuurten:buurten")
    layer.SetAttributeFilter(f"buurtcode = '{code}'")
    f = layer.GetNextFeature()
    if f is None:
        sys.exit(f"Buurt {code} not found in the CBS 2025 map")
    g = f.GetGeometryRef().Clone()
    rd = osr.SpatialReference()
    rd.ImportFromEPSG(28992)
    rd.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    src = layer.GetSpatialRef().Clone()
    src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    g.Transform(osr.CoordinateTransformation(src, rd))
    return f.GetField("buurtnaam"), g, rd


def prepare(code):
    """Write the inputs for one buurt: DEM (filled and raw), the benchmark
    cut-out, the buurt mask, the trees (shapefile for ArcGIS, JSON for GDAL)
    and a GeoPackage for QGIS; remember it as the current study area."""
    folder = folder_for(code)
    folder.mkdir(parents=True, exist_ok=True)
    name, buurt, rd = fetch_buurt(code)
    buffer = buurt.Buffer(BUFFER, 16)
    print(f"{code} {name}: {buurt.GetArea() / 1e4:.1f} ha, with {RADIUS:.0f} m buffer "
          f"{buffer.GetArea() / 1e4:.1f} ha")

    # DEM window: the buffer's bounding box, snapped to the benchmark DEM grid
    src = gdal.Open(BENCH_DEM)
    sgt = src.GetGeoTransform()
    xmin, xmax, ymin, ymax = buffer.GetEnvelope()
    x0 = sgt[0] + np.floor((xmin - sgt[0]) / CELL) * CELL
    x1 = sgt[0] + np.ceil((xmax - sgt[0]) / CELL) * CELL
    y1 = sgt[3] - np.floor((sgt[3] - ymax) / CELL) * CELL
    y0 = sgt[3] - np.ceil((sgt[3] - ymin) / CELL) * CELL
    window = [x0, y1, x1, y0]
    gdal.Translate(str(folder / "dem_raw.tif"), src, projWin=window, outputType=gdal.GDT_Float32,
                   creationOptions=["COMPRESS=DEFLATE"])
    gdal.Translate(str(folder / "dem.tif"), src, projWin=window, outputType=gdal.GDT_Float32,
                   creationOptions=["COMPRESS=DEFLATE"])
    # dem.tif: fill NoData (water) as stage 1 does and drop the flag
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
    dem, gt, wkt, _ = read(folder / "dem.tif")
    print(f"DEM {dem.shape[1]} x {dem.shape[0]} cells, {n_nodata} NoData cells "
          f"(filled in dem.tif, kept in dem_raw.tif)")

    # The benchmark, cut out on the same grid
    gdal.Translate(str(folder / "benchmark.tif"), gdal.Open(BENCHMARK), projWin=window,
                   noData=BENCH_NODATA, creationOptions=["COMPRESS=DEFLATE"])

    # Compared cells: cell centre inside the buurt
    mem = gdal.GetDriverByName("MEM").Create("", dem.shape[1], dem.shape[0], 1, gdal.GDT_Byte)
    mem.SetGeoTransform(gt)
    mem.SetProjection(wkt)
    vds = ogr.GetDriverByName("Memory").CreateDataSource("")
    vl = vds.CreateLayer("buurt", rd, ogr.wkbMultiPolygon)
    f = ogr.Feature(vl.GetLayerDefn())
    f.SetGeometry(buurt)
    vl.CreateFeature(f)
    gdal.RasterizeLayer(mem, [1], vl, burn_values=[1])
    mask = mem.GetRasterBand(1).ReadAsArray()
    write_raster(folder / "buurt.tif", mask, gt, wkt)

    # Trees: the benchmark's tree layer, within the buffer, with a RASTERVALU
    tds = ogr.Open(REFERENCE_GDB)
    tl = tds.GetLayerByName(BENCH_TREES)
    tl.SetSpatialFilter(buffer)
    trees, skipped = [], 0
    for t in tl:
        rv = t.GetField("RASTERVALU")
        if rv is None:
            skipped += 1                      # the benchmark could not use it either
            continue
        g = t.GetGeometryRef()
        trees.append({"fid": t.GetFID(), "x": g.GetX(), "y": g.GetY(), "rastervalu": rv})
    tds = None
    print(f"{len(trees)} trees in buurt + buffer ({skipped} without RASTERVALU left out, as in the benchmark)")

    # The trees as a shapefile for ArcGIS's Visibility tool
    drv = ogr.GetDriverByName("ESRI Shapefile")
    shp = folder / "bomen.shp"
    if shp.exists():
        drv.DeleteDataSource(str(shp))
    sds = drv.CreateDataSource(str(shp))
    sl = sds.CreateLayer("bomen", rd, ogr.wkbPoint)
    sl.CreateField(ogr.FieldDefn("FID_BOOM", ogr.OFTInteger))
    sl.CreateField(ogr.FieldDefn("RASTERVALU", ogr.OFTReal))
    for t in trees:
        f = ogr.Feature(sl.GetLayerDefn())
        f.SetField("FID_BOOM", t["fid"])
        f.SetField("RASTERVALU", t["rastervalu"])
        f.SetGeometry(ogr.CreateGeometryFromWkt(f"POINT ({t['x']} {t['y']})"))
        sl.CreateFeature(f)
    sds = None

    # The study area and trees for QGIS
    gpkg = folder / "studiegebied.gpkg"
    if gpkg.exists():
        ogr.GetDriverByName("GPKG").DeleteDataSource(str(gpkg))
    gds = ogr.GetDriverByName("GPKG").CreateDataSource(str(gpkg))
    for lname, geom in (("buurt", buurt), ("buffer30", buurt.Buffer(RADIUS, 16))):
        lyr = gds.CreateLayer(lname, rd, ogr.wkbMultiPolygon)
        lyr.CreateField(ogr.FieldDefn("naam", ogr.OFTString))
        f = ogr.Feature(lyr.GetLayerDefn())
        f.SetField("naam", f"{name} ({code})" if lname == "buurt" else f"{name} + {RADIUS:.0f} m")
        f.SetGeometry(ogr.ForceToMultiPolygon(geom))
        lyr.CreateFeature(f)
    lyr = gds.CreateLayer("bomen", rd, ogr.wkbPoint)
    lyr.CreateField(ogr.FieldDefn("fid_boom", ogr.OFTInteger))
    lyr.CreateField(ogr.FieldDefn("rastervalu", ogr.OFTReal))
    lyr.CreateField(ogr.FieldDefn("waarnemer_nap", ogr.OFTReal))
    for t in trees:
        f = ogr.Feature(lyr.GetLayerDefn())
        f.SetField("fid_boom", t["fid"])
        f.SetField("rastervalu", t["rastervalu"])
        f.SetField("waarnemer_nap", 2 * t["rastervalu"])
        f.SetGeometry(ogr.CreateGeometryFromWkt(f"POINT ({t['x']} {t['y']})"))
        lyr.CreateFeature(f)
    gds = None

    (folder / "trees.json").write_text(json.dumps(trees))
    (ROOT / "current.json").write_text(json.dumps({"buurtcode": code, "naam": name}))
    print(f"Ready in {folder}. Next: studiegebied_arcgis.py (ArcGIS), then `studiegebied.py gdal`")


def gdal_run():
    """GDAL ViewshedGenerate per tree with the benchmark's observer
    (2 x RASTERVALU m NAP), target 1.8 m, radius 30 m, summed = FREQUENCY.
    observerHeight is added to the DEM value of the observer's cell, so it is
    set to 2 x RASTERVALU minus that value."""
    folder = current()
    dem, gt, wkt, _ = read(folder / "dem.tif")
    trees = json.loads((folder / "trees.json").read_text())
    ds = gdal.Open(str(folder / "dem.tif"))
    band = ds.GetRasterBand(1)
    total = np.zeros(dem.shape, np.int32)
    t0 = time.time()
    for t in trees:
        c, r = int((t["x"] - gt[0]) / CELL), int((gt[3] - t["y"]) / CELL)
        out = gdal.ViewshedGenerate(srcBand=band, driverName="MEM", targetRasterName="", creationOptions=[],
                                    observerX=t["x"], observerY=t["y"],
                                    observerHeight=2 * t["rastervalu"] - float(dem[r, c]),
                                    targetHeight=TARGET_HEIGHT, visibleVal=1, invisibleVal=0,
                                    outOfRangeVal=0, noDataVal=0, dfCurvCoeff=0, mode=gdal.GVM_Edge,
                                    maxDistance=RADIUS)
        # Add the tree's result window (clipped to the study area) to the count
        v = out.GetRasterBand(1).ReadAsArray()
        ogt = out.GetGeoTransform()
        co, ro = round((ogt[0] - gt[0]) / CELL), round((gt[3] - ogt[3]) / CELL)
        r0, c0 = max(ro, 0), max(co, 0)
        r1, c1 = min(ro + v.shape[0], dem.shape[0]), min(co + v.shape[1], dem.shape[1])
        total[r0:r1, c0:c1] += (v[r0 - ro:r1 - ro, c0 - co:c1 - co] > 0)
    write_raster(folder / "gdal.tif", total, gt, wkt, gdal.GDT_Int32)
    print(f"gdal.tif: {len(trees)} trees in {time.time() - t0:.0f} s")


def compare():
    """Compare the ArcGIS and GDAL results with the benchmark on the buurt's
    land cells; print the table and write the verschil_*.tif maps."""
    folder = current()
    mask = read(folder / "buurt.tif")[0] == 1
    bench, gt, wkt, bnd = read(folder / "benchmark.tif")
    bench = bench.astype(np.int64)
    bench_nd = bench == BENCH_NODATA if bnd is None else bench == bnd
    area = mask & ~bench_nd
    print(f"{int(mask.sum()):,} cells in the buurt, {int((mask & bench_nd).sum()):,} NoData in the benchmark "
          f"(water; left out) -> {int(area.sum()):,} compared\n")
    print(f"{'':10s} {'mean':>6s} {'>=3':>6s} {'=bench':>7s} {'same>=3':>8s} {'+/-1':>6s}  (share of compared cells)")
    b = bench[area]
    print(f"{'benchmark':10s} {b.mean():6.2f} {100 * (b >= 3).mean():5.1f}%")
    for name in ("arcgis", "gdal"):
        path = folder / f"{name}.tif"
        if not path.exists():
            print(f"{name:10s} (not there yet)")
            continue
        v, vgt, _, vnd = read(path)
        if vgt != gt or v.shape != bench.shape:
            sys.exit(f"{path.name}: grid differs from benchmark.tif")
        v = v.astype(np.int64)
        if vnd is not None:
            v[v == vnd] = 0
        # Columns: mean, share >= 3, same count as the benchmark, same verdict
        # on >= 3, within one tree of the benchmark
        x = v[area]
        print(f"{name:10s} {x.mean():6.2f} {100 * (x >= 3).mean():5.1f}% {100 * (x == b).mean():6.1f}% "
              f"{100 * ((x >= 3) == (b >= 3)).mean():7.1f}% {100 * (np.abs(x - b) <= 1).mean():5.1f}%")
        diff = np.where(~mask, 0, np.where(v > bench, 1, np.where(v < bench, 2, 0))).astype(np.uint8)
        diff[mask & bench_nd] = 0
        write_raster(folder / f"verschil_{name}.tif", diff, gt, wkt)
    print("\nverschil_*.tif written (1 = counts more trees than the benchmark, 2 = fewer). "
          "Add to QGIS: python-qgis-ltr.bat qgis_validation\\build_project.py")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "compare"
    if action == "prepare":
        prepare(sys.argv[2] if len(sys.argv) > 2 else BUURT)
    elif action == "gdal":
        gdal_run()
    elif action == "compare":
        compare()
    else:
        sys.exit(__doc__)
