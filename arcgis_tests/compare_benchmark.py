"""
compare_benchmark.py — Compare the corrected ArcGIS benchmark
(arcgis_tests/benchmark_corrected.py) with the original visibility_Delft
and with this pipeline's Delft_viewshed.tif, cell by cell.

For each raster: mean number of trees visible, share of cells with 0 and
with >= 3 trees; for each pair: Pearson r and the share of cells with the
same count and with the same >= 3 verdict. Computed over the corrected
raster's extent (the test area or all of Delft), on cells where all
rasters have data, in blocks (Delft is ~176 million cells).

For all of Delft only cells inside the municipality, at least 30 m from
its boundary, are compared (MUNICIPALITY, INSET_M): the benchmark only
has Delft's trees, the pipeline those of the whole province, so near and
beyond the boundary their counts differ for that reason alone.

Note: Delft_viewshed.tif was made on the pipeline's production DEM, not on
AHN5, so its differences from the benchmarks mix the observer rule, the
engine and the surface model.

Usage (OSGeo4W Python):
    python arcgis_tests/compare_benchmark.py          # all of Delft
    python arcgis_tests/compare_benchmark.py test     # the test area run
"""

import sys
from itertools import combinations
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "etl"))
import config  # noqa: E402,F401  (GDAL environment)

from osgeo import gdal, ogr  # noqa: E402
gdal.UseExceptions()

REFERENCE_GDB = r"R:/ESRI/DATA/RUIMTELIJKE ONTWIKKELING/PERSOONLIJK/Chris/test/data.gdb"
BENCH_NODATA = -2147483647            # visibility_Delft's undeclared NoData
EXPERIMENTS = REPO / "data" / "processed" / "experiments"
BLOCK_ROWS = 1024
MUNICIPALITIES = REPO / "data" / "interim" / "gemeenten.gpkg"
MUNICIPALITY = "Delft"
INSET_M = 30.0                        # = outer radius


def municipality_mask(gt, nx, ny, wkt):
    """True inside MUNICIPALITY, at least INSET_M from its boundary, on the grid."""
    ds = ogr.Open(str(MUNICIPALITIES))
    layer = ds.GetLayer(0)
    layer.SetAttributeFilter(f"naam = '{MUNICIPALITY}'")
    f = layer.GetNextFeature()
    if f is None:
        raise SystemExit(f"{MUNICIPALITY} not found in {MUNICIPALITIES}")
    inner = f.GetGeometryRef().Buffer(-INSET_M)
    mem_v = ogr.GetDriverByName("Memory").CreateDataSource("")
    lyr = mem_v.CreateLayer("m", layer.GetSpatialRef(), ogr.wkbMultiPolygon)
    out = ogr.Feature(lyr.GetLayerDefn())
    out.SetGeometry(inner)
    lyr.CreateFeature(out)
    mem = gdal.GetDriverByName("MEM").Create("", nx, ny, 1, gdal.GDT_Byte)
    mem.SetGeoTransform(gt)
    mem.SetProjection(wkt)
    gdal.RasterizeLayer(mem, [1], lyr, burn_values=[1])
    return mem.GetRasterBand(1).ReadAsArray().astype(bool)


def open_aligned(path, ref_gt, nodata=None):
    """(dataset band, row offset, col offset, nodata) relative to the reference grid."""
    ds = gdal.Open(str(path))
    gt = ds.GetGeoTransform()
    if abs(gt[1] - ref_gt[1]) > 1e-9 or abs(gt[5] - ref_gt[5]) > 1e-9:
        raise SystemExit(f"{path}: cell size {gt[1]} differs from {ref_gt[1]}")
    co, ro = round((ref_gt[0] - gt[0]) / gt[1]), round((ref_gt[3] - gt[3]) / gt[5])
    band = ds.GetRasterBand(1)
    return ds, band, ro, co, band.GetNoDataValue() if nodata is None else nodata


def main(test):
    name = "visibility_Delft_corrected" + ("_test" if test else "")
    corrected = EXPERIMENTS / f"{name}.tif"
    if not corrected.exists():
        raise SystemExit(f"Not found: {corrected} — run arcgis_tests/benchmark_corrected.py first")
    ref = gdal.Open(str(corrected))
    ref_gt, nx, ny = ref.GetGeoTransform(), ref.RasterXSize, ref.RasterYSize
    inside = None if test else municipality_mask(ref_gt, nx, ny, ref.GetProjection())
    ref = None

    sources = {
        "corrected benchmark": (corrected, None),
        "original benchmark": (f'OpenFileGDB:"{REFERENCE_GDB}":visibility_Delft', BENCH_NODATA),
        "pipeline (production DEM)": (REPO / "data" / "processed" / "Delft_viewshed.tif", None),
    }
    opened = {k: open_aligned(p, ref_gt, nd) for k, (p, nd) in sources.items()}
    names = list(opened)

    n = 0
    s = {k: dict(sum=0.0, sq=0.0, zero=0, ge3=0) for k in names}
    pair = {pq: dict(xy=0.0, same=0, same3=0) for pq in combinations(names, 2)}
    for r0 in range(0, ny, BLOCK_ROWS):
        rows = min(BLOCK_ROWS, ny - r0)
        data = {}
        valid = np.ones((rows, nx), bool) if inside is None else inside[r0:r0 + rows].copy()
        for k, (ds, band, ro, co, nd) in opened.items():
            a = np.full((rows, nx), np.nan)
            src_r0, src_c0 = r0 + ro, co
            rr0, rr1 = max(src_r0, 0), min(src_r0 + rows, band.YSize)
            cc0, cc1 = max(src_c0, 0), min(src_c0 + nx, band.XSize)
            if rr1 > rr0 and cc1 > cc0:
                block = band.ReadAsArray(cc0, rr0, cc1 - cc0, rr1 - rr0).astype(np.float64)
                if nd is not None:
                    block[block == nd] = np.nan
                a[rr0 - src_r0:rr1 - src_r0, cc0 - src_c0:cc1 - src_c0] = block
            valid &= np.isfinite(a)
            data[k] = a
        m = int(valid.sum())
        if not m:
            continue
        n += m
        v = {k: data[k][valid] for k in names}
        for k in names:
            s[k]["sum"] += v[k].sum()
            s[k]["sq"] += (v[k] ** 2).sum()
            s[k]["zero"] += int((v[k] == 0).sum())
            s[k]["ge3"] += int((v[k] >= 3).sum())
        for p, q in pair:
            pair[(p, q)]["xy"] += (v[p] * v[q]).sum()
            pair[(p, q)]["same"] += int((v[p] == v[q]).sum())
            pair[(p, q)]["same3"] += int(((v[p] >= 3) == (v[q] >= 3)).sum())
        print(f"\r  rows {r0 + rows:,} / {ny:,}", end="", flush=True)
    print()
    if not n:
        raise SystemExit("No cells where all rasters have data")

    where = "" if test else f" inside {MUNICIPALITY}, >= {INSET_M:.0f} m from its boundary,"
    print(f"\n{name}: {n:,} cells{where} where all three rasters have data\n")
    print(f"{'':28s} {'mean':>6s} {'0 trees':>8s} {'>=3 trees':>10s}")
    mean, sd = {}, {}
    for k in names:
        mean[k] = s[k]["sum"] / n
        sd[k] = np.sqrt(max(s[k]["sq"] / n - mean[k] ** 2, 0))
        print(f"{k:28s} {mean[k]:6.2f} {100 * s[k]['zero'] / n:7.1f}% {100 * s[k]['ge3'] / n:9.1f}%")
    print(f"\n{'':56s} {'r':>5s} {'same count':>11s} {'same >=3':>9s}")
    for (p, q), a in pair.items():
        r = (a["xy"] / n - mean[p] * mean[q]) / (sd[p] * sd[q]) if sd[p] and sd[q] else float("nan")
        print(f"{p + ' vs ' + q:56s} {r:5.2f} {100 * a['same'] / n:10.1f}% {100 * a['same3'] / n:8.1f}%")


if __name__ == "__main__":
    main(test=len(sys.argv) > 1 and sys.argv[1] == "test")
