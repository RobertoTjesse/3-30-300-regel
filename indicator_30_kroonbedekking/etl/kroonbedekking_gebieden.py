"""
kroonbedekking_gebieden.py — The 30 for the whole province: canopy cover per
buurt, wijk and gemeente from BKB 2024 (boomkroonbedekking, Friedenau
Society, from lidar), on the same CBS 2025 areas as the web map.

  canopy cover = crown area / land area x 100%

1. Crown area: bkb_per_gebied.count() counts the crown pixels (0.25 m) per
   buurt of <Province>_gebieden.gpkg (06_area_summaries.py: the CBS 2025
   buurten of the province). A pixel belongs to the buurt that holds its
   centre, so no crown is counted twice.
2. Wijken and gemeenten are the sums of their buurten. CBS codes nest:
   buurt BU04820101 lies in wijk WK048201 and gemeente GM0482. (Tested on
   Delft: the sums equal clipping the wijken and the gemeente directly.)
3. Land area: CBS Wijk- en Buurtkaart 2025 (oppervlakteLandInHa, whole
   hectares) per buurt, wijk and gemeente, from the PDOK WFS; cached in
   data/interim/cbs2025_oppervlak.gpkg. Areas with no land get no value.

Output: data/processed/<Province>_kroonbedekking.csv, one row per area:
  niveau (gemeente/wijk/buurt), code, naam, gemeente, kroon_m2, gebied_m2
  (area from the pixels), land_m2 (CBS), pct
Read by web/build_tiles_30_300.py.

Usage (from the repository root):
    python indicator_30_kroonbedekking/etl/kroonbedekking_gebieden.py [--workers 5]
"""

import argparse
import csv
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "indicator_3_bomen" / "etl"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
from bkb_per_gebied import count, load_areas  # noqa: E402

from osgeo import gdal, ogr  # noqa: E402
gdal.UseExceptions()
ogr.UseExceptions()

GEBIEDEN = config.PROCESSED_DIR / f"{config.PROVINCE_SLUG}_gebieden.gpkg"
CBS_CACHE = config.INTERIM_DIR / "cbs2025_oppervlak.gpkg"
CBS_WFS = "WFS:https://service.pdok.nl/cbs/wijkenbuurten/2025/wfs/v1_0"
OUT = config.PROCESSED_DIR / f"{config.PROVINCE_SLUG}_kroonbedekking.csv"


def cbs_land(extent, cache):
    """{level: {code: (name, land m2)}} from the CBS 2025 map (cached)."""
    fields = {"buurten": "buurtcode", "wijken": "wijkcode", "gemeenten": "gemeentecode"}
    if not cache.exists():
        print("Fetching CBS 2025 land area from PDOK …")
        for layer, code in fields.items():
            name = {"buurten": "buurtnaam", "wijken": "wijknaam", "gemeenten": "gemeentenaam"}[layer]
            gdal.VectorTranslate(str(cache), CBS_WFS, format="GPKG", accessMode="update" if cache.exists() else None,
                                 layers=[f"wijkenbuurten:{layer}"], layerName=layer, spatFilter=extent,
                                 selectFields=[code, name, "oppervlakteLandInHa", "oppervlakteTotaalInHa"])
    ds = ogr.Open(str(cache))
    out = {}
    for layer, code in fields.items():
        name = {"buurten": "buurtnaam", "wijken": "wijknaam", "gemeenten": "gemeentenaam"}[layer]
        lyr = ds.GetLayerByName(layer)
        out[layer] = {f.GetField(code): (f.GetField(name), (f.GetField("oppervlakteLandInHa") or 0) * 1e4) for f in lyr}
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--bkb", type=Path, default=config.BKB_TIF)
    p.add_argument("--workers", type=int, default=5)
    p.add_argument("--gebieden", type=Path, default=GEBIEDEN, help="the map's areas (06_area_summaries.py)")
    p.add_argument("--cache", type=Path, default=CBS_CACHE, help="CBS 2025 land area, fetched once")
    p.add_argument("--out", type=Path, default=OUT)
    a = p.parse_args()
    t0 = time.perf_counter()

    # The map's buurten, with the name of their gemeente
    areas = load_areas(a.gebieden, "buurten", "code", None)
    ds = ogr.Open(str(a.gebieden))
    buurt_info = {f.GetField("code"): (f.GetField("naam"), f.GetField("gemeente")) for f in ds.GetLayerByName("buurten")}
    gem_namen = {f.GetField("naam") for f in ds.GetLayerByName("gemeenten")}
    xmin, xmax, ymin, ymax = ds.GetLayerByName("buurten").GetExtent()
    land = cbs_land([xmin, ymin, xmax, ymax], a.cache)

    crowns, every, pixel_m2 = count(areas, a.bkb, a.workers)

    # Sum per buurt, wijk and gemeente
    sums = {"buurt": defaultdict(lambda: [0, 0]), "wijk": defaultdict(lambda: [0, 0]), "gemeente": defaultdict(lambda: [0, 0])}
    for (code, _), k, e in zip(areas, crowns, every):
        for niveau, key in (("buurt", code), ("wijk", "WK" + code[2:8]), ("gemeente", "GM" + code[2:6])):
            sums[niveau][key][0] += int(k)
            sums[niveau][key][1] += int(e)
    gem_naam = {}   # gemeente code -> name of the map's gemeente (via its buurten)
    for code, (naam, gemeente) in buurt_info.items():
        gem_naam["GM" + code[2:6]] = gemeente

    rows, missing = [], []
    for niveau, layer in (("gemeente", "gemeenten"), ("wijk", "wijken"), ("buurt", "buurten")):
        for code, (k, e) in sorted(sums[niveau].items()):
            cbs_name, land_m2 = land[layer].get(code, (None, None))
            if land_m2 is None:
                missing.append(code)
                land_m2 = 0
            if niveau == "buurt":
                naam, gemeente = buurt_info[code]
            elif niveau == "wijk":
                naam, gemeente = cbs_name, gem_naam["GM" + code[2:6]]
            else:
                naam = gemeente = gem_naam[code]
            kroon_m2, gebied_m2 = k * pixel_m2, e * pixel_m2
            pct = round(100 * kroon_m2 / land_m2, 2) if land_m2 > 0 else None
            rows.append([niveau, code, naam, gemeente, round(kroon_m2, 2), round(gebied_m2, 2), round(land_m2), pct])
    if missing:
        print(f"WARNING: no CBS 2025 land area for {len(missing)} areas: {missing[:5]}")
    unknown = {r[2] for r in rows if r[0] == "gemeente"} - gem_namen
    if unknown:
        print(f"WARNING: gemeenten not on the map: {sorted(unknown)}")

    a.out.parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["niveau", "code", "naam", "gemeente", "kroon_m2", "gebied_m2", "land_m2", "pct"])
        w.writerows(rows)

    # Summary
    gem = [r for r in rows if r[0] == "gemeente"]
    k_all = sum(r[4] for r in gem)
    l_all = sum(r[6] for r in gem)
    print(f"Province: crown {k_all / 1e4:,.0f} ha on {l_all / 1e4:,.0f} ha land = {100 * k_all / l_all:.1f}%")
    for niveau in ("gemeente", "wijk", "buurt"):
        v = [r[7] for r in rows if r[0] == niveau and r[7] is not None]
        print(f"  {niveau}: {len(v)} with a value, {sum(x >= 30 for x in v)} at >= 30%, "
              f"{sum(x > 100 for x in v)} above 100%, median {sorted(v)[len(v) // 2]:.1f}%")
    gem.sort(key=lambda r: -(r[7] or 0))
    print("  highest:", ", ".join(f"{r[2]} {r[7]:.1f}%" for r in gem[:3]))
    print("  lowest: ", ", ".join(f"{r[2]} {r[7]:.1f}%" for r in gem[-3:]))
    print(f"Wrote {a.out} ({len(rows)} areas) in {time.perf_counter() - t0:.0f} s")


if __name__ == "__main__":
    main()
