"""
06_area_summaries.py — Summarise the residential building scores per
gemeente, wijk and buurt (CBS Wijk- en Buurtkaart), for tables and the web
map.

Every building in ZuidHolland_woningen.gpkg (05_merge_province.py) is
assigned to a buurt by its footprint centroid, looked up on a 5 m raster of
the buurt boundaries (as stage 5 does for municipalities); its wijk follows
from the buurt, so the three levels always nest. The gemeente is the one
stage 5 assigned. Buildings whose centroid falls in no buurt (a few, on
boundary slivers) count for their gemeente only.

Inputs:
  data/processed/ZuidHolland_woningen.gpkg
  data/interim/wijken.gpkg, buurten.gpkg — CBS Wijk- en Buurtkaart 2025 from
      PDOK, e.g. (in Python, after `import config`, for GDAL's environment):
        gdal.VectorTranslate("data/interim/buurten.gpkg",
            "WFS:https://service.pdok.nl/cbs/wijkenbuurten/2025/wfs/v1_0",
            format="GPKG", layers=["wijkenbuurten:buurten"],
            spatFilter=[45000, 405000, 135000, 485000], layerName="buurten",
            selectFields=["buurtcode", "buurtnaam", "wijkcode", "gemeentecode",
                          "gemeentenaam", "water"])
      and the same for "wijken" (wijkcode, wijknaam, gemeentecode,
      gemeentenaam, water)
  data/interim/gemeenten.gpkg                 (see 05_merge_province.py)

Outputs (data/processed/):
  ZuidHolland_gebieden.gpkg    layers gemeenten, wijken, buurten: polygons
                               with the summary columns below
  ZuidHolland_wijken.csv, ZuidHolland_buurten.csv
      code, naam, gemeente, gebouwen, woningen, pct_woningen_<class>,
      pct_woningen_geen_ring, pct_woningen_3_of_meer
  (the per-gemeente table is 05_merge_province.py's ZuidHolland_samenvatting.csv;
   this script checks its own gemeente figures against it)

Usage:
    python etl/06_area_summaries.py
"""

import csv
import sys
from collections import defaultdict

import numpy as np

import config

from osgeo import gdal, ogr
gdal.UseExceptions()
ogr.UseExceptions()

LOOKUP_RES = 5.0      # metres, as in 05_merge_province.py
CLASSES = ["0", "1-2", "3-5", "6-7", "8+"]
SUMMARY_FIELDS = (["gebouwen", "woningen"] + [f"pct_woningen_{c}" for c in CLASSES]
                  + ["pct_woningen_geen_ring", "pct_woningen_3_of_meer"])


def new_stats():
    return {"gebouwen": 0, "woningen": 0, "geen_ring": 0, **{c: 0 for c in CLASSES}}


def summary(s):
    """Counts -> the summary columns (percentages of homes)."""
    pct = lambda n: round(100 * n / s["woningen"], 1) if s["woningen"] else None
    return {"gebouwen": s["gebouwen"], "woningen": s["woningen"],
            **{f"pct_woningen_{c}": pct(s[c]) for c in CLASSES},
            "pct_woningen_geen_ring": pct(s["geen_ring"]),
            "pct_woningen_3_of_meer": pct(sum(s[c] for c in CLASSES[2:]))}


def province_municipalities():
    """Names of the municipalities 05_merge_province.py assigned buildings to."""
    ds = ogr.Open(str(config.PROCESSED_DIR / "ZuidHolland_woningen.gpkg"))
    sql = ds.ExecuteSQL("SELECT DISTINCT gemeente FROM woningen")
    names = {f.GetField(0) for f in sql}
    ds.ReleaseResultSet(sql)
    return names


def load_areas(name, code_field, municipalities):
    """Land parts (water = NEE) of the CBS areas in the province's municipalities.
    CBS disambiguates some names ("Rijswijk (ZH.)"); gemeentenaam is set to
    the name stage 5 uses."""
    ds = ogr.Open(str(config.INTERIM_DIR / f"{name}.gpkg"))
    layer = ds.GetLayer(0)
    areas = {}
    for f in layer:
        gemeente = f.GetField("gemeentenaam").split(" (")[0]
        if f.GetField("water") == "JA" or gemeente not in municipalities:
            continue
        a = areas[f.GetField(code_field)] = {k: f.GetField(k) for k in f.keys()}
        a["gemeentenaam"] = gemeente
        a["geom"] = f.GetGeometryRef().Clone()
    return areas, layer.GetSpatialRef()


def buurt_lookup(buurten, srs, extent):
    """Rasterise the buurten: (array of index into codes, gt, codes)."""
    xmin, xmax, ymin, ymax = extent
    mem = ogr.GetDriverByName("MEM").CreateDataSource("")
    layer = mem.CreateLayer("b", srs, ogr.wkbMultiPolygon)
    layer.CreateField(ogr.FieldDefn("idx", ogr.OFTInteger))
    codes = [None]
    for code, b in buurten.items():
        codes.append(code)
        f = ogr.Feature(layer.GetLayerDefn())
        f.SetField("idx", len(codes) - 1)
        f.SetGeometry(b["geom"])
        layer.CreateFeature(f)
    nx = int(np.ceil((xmax - xmin) / LOOKUP_RES))
    ny = int(np.ceil((ymax - ymin) / LOOKUP_RES))
    gt = (xmin, LOOKUP_RES, 0.0, ymax, 0.0, -LOOKUP_RES)
    ras = gdal.GetDriverByName("MEM").Create("", nx, ny, 1, gdal.GDT_Int16)
    ras.SetGeoTransform(gt)
    gdal.RasterizeLayer(ras, [1], layer, options=["ATTRIBUTE=idx"])
    return ras.GetRasterBand(1).ReadAsArray(), gt, codes


def write_csv(path, areas, stats, name_field):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["code", "naam", "gemeente", *SUMMARY_FIELDS])
        for code in sorted(areas, key=lambda c: (areas[c]["gemeentenaam"], areas[c][name_field] or "")):
            s = summary(stats[code])
            w.writerow([code, areas[code][name_field], areas[code]["gemeentenaam"],
                        *[s[k] for k in SUMMARY_FIELDS]])


def write_layer(ds, name, srs, rows):
    """rows: (geometry, code, naam, gemeente, counts)"""
    layer = ds.CreateLayer(name, srs, ogr.wkbMultiPolygon)
    for fname, ftype in [("code", ogr.OFTString), ("naam", ogr.OFTString),
                         ("gemeente", ogr.OFTString), ("gebouwen", ogr.OFTInteger),
                         ("woningen", ogr.OFTInteger)]:
        layer.CreateField(ogr.FieldDefn(fname, ftype))
    for k in SUMMARY_FIELDS[2:]:
        layer.CreateField(ogr.FieldDefn(k, ogr.OFTReal))
    layer.StartTransaction()
    for geom, code, naam, gemeente, counts in rows:
        f = ogr.Feature(layer.GetLayerDefn())
        f.SetField("code", code)
        f.SetField("naam", naam)
        f.SetField("gemeente", gemeente)
        for k, v in summary(counts).items():
            if v is not None:
                f.SetField(k, v)
        f.SetGeometry(ogr.ForceToMultiPolygon(geom))
        layer.CreateFeature(f)
    layer.CommitTransaction()


def main():
    homes_path = config.PROCESSED_DIR / "ZuidHolland_woningen.gpkg"
    for p in (homes_path, config.INTERIM_DIR / "wijken.gpkg", config.INTERIM_DIR / "buurten.gpkg"):
        if not p.exists():
            sys.exit(f"ERROR: not found: {p} (see the docstring of this script)")

    municipalities = province_municipalities()
    wijken, srs = load_areas("wijken", "wijkcode", municipalities)
    buurten, _ = load_areas("buurten", "buurtcode", municipalities)
    print(f"{len(municipalities)} municipalities, {len(wijken)} wijken, {len(buurten)} buurten (land)")

    homes_ds = ogr.Open(str(homes_path))
    homes = homes_ds.GetLayer(0)
    lookup, gt, codes = buurt_lookup(buurten, srs, homes.GetExtent())

    by_gem, by_wijk, by_buurt = (defaultdict(new_stats) for _ in range(3))
    unassigned = 0
    for f in homes:
        n = f.GetField("n_woningen") or 0
        k = f.GetField("klasse") or "geen_ring"
        c = f.GetGeometryRef().Centroid()
        col = int((c.GetX() - gt[0]) / gt[1])
        row = int((c.GetY() - gt[3]) / gt[5])
        idx = lookup[row, col] if 0 <= row < lookup.shape[0] and 0 <= col < lookup.shape[1] else 0
        targets = [by_gem[f.GetField("gemeente")]]
        if idx:
            b = codes[idx]
            targets += [by_buurt[b], by_wijk[buurten[b]["wijkcode"]]]
        else:
            unassigned += 1
        for s in targets:
            s["gebouwen"] += 1
            s["woningen"] += n
            s[k] += n
    print(f"{homes.GetFeatureCount() - unassigned:,} buildings assigned to a buurt, "
          f"{unassigned:,} to their gemeente only")

    # Check against stage 5's per-municipality table
    with open(config.PROCESSED_DIR / "ZuidHolland_samenvatting.csv", encoding="utf-8") as fh:
        stage5 = {r["gemeente"]: r for r in csv.DictReader(fh)}
    mismatches = [g for g in by_gem
                  if str(summary(by_gem[g])["pct_woningen_3_of_meer"]) != stage5.get(g, {}).get("pct_woningen_3_of_meer")
                  or str(by_gem[g]["woningen"]) != stage5.get(g, {}).get("woningen")]
    print("gemeente figures match ZuidHolland_samenvatting.csv" if not mismatches
          else f"WARNING: gemeente figures differ from stage 5 for {mismatches}")

    write_csv(config.PROCESSED_DIR / "ZuidHolland_wijken.csv", wijken, by_wijk, "wijknaam")
    write_csv(config.PROCESSED_DIR / "ZuidHolland_buurten.csv", buurten, by_buurt, "buurtnaam")

    # Polygons: gemeenten from gemeenten.gpkg (the boundaries stage 5 used)
    gem_ds = ogr.Open(str(config.INTERIM_DIR / "gemeenten.gpkg"))
    gem_rows = [(f.GetGeometryRef().Clone(), None, f.GetField("naam"), f.GetField("naam"), by_gem[f.GetField("naam")])
                for f in gem_ds.GetLayer(0) if f.GetField("naam") in municipalities]
    out_path = config.PROCESSED_DIR / "ZuidHolland_gebieden.gpkg"
    tmp = out_path.with_suffix(".partial.gpkg")
    tmp.unlink(missing_ok=True)
    out = ogr.GetDriverByName("GPKG").CreateDataSource(str(tmp))
    write_layer(out, "gemeenten", srs, gem_rows)
    write_layer(out, "wijken", srs, [(w["geom"], c, w["wijknaam"], w["gemeentenaam"], by_wijk[c])
                                     for c, w in wijken.items()])
    write_layer(out, "buurten", srs, [(b["geom"], c, b["buurtnaam"], b["gemeentenaam"], by_buurt[c])
                                      for c, b in buurten.items()])
    out = None
    tmp.replace(out_path)
    print(f"Wrote {out_path.name}, ZuidHolland_wijken.csv, ZuidHolland_buurten.csv")


if __name__ == "__main__":
    main()
