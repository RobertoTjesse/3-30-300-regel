"""
build_data.py — The data of both MCDA proposals (mcda/README.md), from the
results of the 3, the 30 and the 300.

One row per home (the 300's address points, 05_combine.py), with the three
values the MCDA weighs:
  bomen    visible trees of its building (the 3, 04_score_buildings.py);
           missing where the building has no facade ring
  kroon    canopy within 500 m of its building, % (the 30)
  afstand  walking distance to green, m (the 300); missing beyond 1,275 m
Its buurt is the buurt of its building's centroid, as in 06_area_summaries.py;
homes whose building centroid falls in no buurt (boundary slivers) get one
"no buurt" group per gemeente, so gemeente totals stay complete.

Written:
  mcda/site/data/gebieden.json   gemeenten, wijken and buurten in index order;
                                 per buurt its first home and number of homes
  mcda/site/data/woningen.bin.gz the homes, sorted by buurt, as columns:
                                 kroon x10 (uint16), afstand (uint16), bomen
                                 (uint8); 65535 / 255 = missing
  mcda/site/data/kaart.pmtiles   vector tiles: gemeenten, wijken, buurten
                                 (property id = index in gebieden.json) and
                                 gebouwen (bomen, kroon x10, median afstand of
                                 its homes, woningen, buurt index)
  mcda/local/data/mcda.duckdb    the same for the local server, plus the
                                 geometries of homes, areas and trees

Usage (OSGeo4W Python):
    python mcda/build_data.py [--skip-tiles] [--skip-local]
"""

import argparse
import gzip
import json
import sys
import time
from pathlib import Path

import duckdb
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "indicator_3_bomen" / "etl"))
import config  # noqa: E402

from osgeo import gdal, ogr, osr  # noqa: E402
gdal.UseExceptions()
ogr.UseExceptions()

HERE = Path(__file__).resolve().parent
SITE_DATA = HERE / "site" / "data"
LOCAL_DB = HERE / "local" / "data" / "mcda.duckdb"
SLUG = config.PROVINCE_SLUG
GEBIEDEN = config.PROCESSED_DIR / f"{SLUG}_gebieden.gpkg"
WONINGEN = config.PROCESSED_DIR / f"{SLUG}_woningen.gpkg"
KROON = config.PROCESSED_DIR / f"{SLUG}_kroon500_woningen.csv"
HOMES_300 = config.INTERIM_DIR / "300" / SLUG / "300.gpkg"
ZOOMS = {"gemeenten": (6, 9), "wijken": (9, 11), "buurten": (11, 13), "gebouwen": (13, 15)}
MISSING16, MISSING8 = 65535, 255


def log(msg):
    print(f"{time.strftime('%H:%M:%S')}  {msg}", flush=True)


def load(con):
    """Areas, buildings and homes into DuckDB tables, with indexes in map order."""
    con.execute("INSTALL spatial; LOAD spatial")
    log("areas")
    con.execute(f"""
        CREATE TABLE gemeenten AS
        SELECT row_number() OVER (ORDER BY naam) - 1 AS idx, code, naam, geom
        FROM st_read('{GEBIEDEN}', layer='gemeenten');
        CREATE TABLE wijken AS
        SELECT row_number() OVER (ORDER BY w.code) - 1 AS idx, w.code, w.naam, g.idx AS gem_idx, w.geom
        FROM st_read('{GEBIEDEN}', layer='wijken') w JOIN gemeenten g ON g.naam = w.gemeente;
        CREATE TABLE buurten_src AS
        SELECT b.code, b.naam, g.idx AS gem_idx, wk.idx AS wijk_idx, b.geom
        FROM st_read('{GEBIEDEN}', layer='buurten') b
        JOIN gemeenten g ON g.naam = b.gemeente
        LEFT JOIN wijken wk ON wk.code = 'WK' || substr(b.code, 3, 6);
    """)
    log("buildings -> buurt (centroid)")
    con.execute(f"""
        CREATE TABLE gebouwen AS
        SELECT w.pand_id, w.n_woningen, w.bomen_zichtbaar AS bomen, g.idx AS gem_idx, w.geom,
               ST_Centroid(w.geom) AS c
        FROM st_read('{WONINGEN}') w JOIN gemeenten g ON g.naam = w.gemeente;
        CREATE TABLE gebouw_buurt AS
        SELECT p.pand_id, b.code AS buurt
        FROM gebouwen p JOIN buurten_src b ON ST_Within(p.c, b.geom);
        CREATE TABLE kroon AS
        SELECT pand_id, TRY_CAST(kroon AS DOUBLE) AS kroon
        FROM read_csv('{KROON}', header=true, all_varchar=true);
    """)
    # Buurten in code order, then one "no buurt" group per gemeente that needs one
    con.execute("""
        CREATE TABLE groep AS
        SELECT p.pand_id, coalesce(gb.buurt, 'GEEN-' || p.gem_idx) AS groep
        FROM gebouwen p LEFT JOIN gebouw_buurt gb USING (pand_id);
        CREATE TABLE buurten AS
        WITH used AS (SELECT DISTINCT groep FROM groep WHERE groep LIKE 'GEEN-%')
        SELECT row_number() OVER (ORDER BY geen, code) - 1 AS idx, * EXCLUDE (geen) FROM (
            SELECT 0 AS geen, code, naam, gem_idx, wijk_idx, geom FROM buurten_src
            UNION ALL
            SELECT 1, groep, 'Buiten de buurtgrenzen', CAST(substr(groep, 6) AS INTEGER), NULL, NULL FROM used);
    """)
    log("homes")
    con.execute(f"""
        CREATE TABLE woningen AS
        SELECT row_number() OVER (ORDER BY b.idx, h.pand_id, h.home_id) - 1 AS i,
               b.idx AS buurt_idx, h.pand_id, p.bomen, k.kroon, h.afstand_m AS afstand, h.geom
        FROM st_read('{HOMES_300}', layer='homes') h
        JOIN gebouwen p USING (pand_id)
        JOIN groep gr USING (pand_id)
        JOIN buurten b ON b.code = gr.groep
        LEFT JOIN kroon k USING (pand_id)
        ORDER BY i;
    """)
    n, nb = con.execute("SELECT count(*), count(DISTINCT pand_id) FROM woningen").fetchone()
    log(f"{n:,} homes in {nb:,} buildings")


def write_site_data(con):
    """gebieden.json and woningen.bin.gz for the static map."""
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    gem = con.execute("SELECT code, naam FROM gemeenten ORDER BY idx").fetchall()
    wijk = con.execute("SELECT code, naam, gem_idx FROM wijken ORDER BY idx").fetchall()
    counts = dict(con.execute("SELECT buurt_idx, count(*) FROM woningen GROUP BY 1").fetchall())
    buurt, start = [], 0
    for idx, code, naam, gi, wi in con.execute(
            "SELECT idx, code, naam, gem_idx, wijk_idx FROM buurten ORDER BY idx").fetchall():
        n = counts.get(idx, 0)
        buurt.append([code, naam, gi, -1 if wi is None else wi, start, n])
        start += n
    meta = {"woningen": start, "gemaakt": time.strftime("%Y-%m-%d"),
            "bronnen": {"3": "AHN5 DSM 0,5 m, NEO-bomen 2020-2022", "30": "BKB 2024",
                        "300": "groen: provinciale voorzieningenkaart en TOP10NL, routes over OSM"}}
    # Bounding box per area in WGS84, for flying to it from the ranking
    bbox = {}
    for level in ("gemeenten", "wijken", "buurten"):
        g = "ST_Transform(geom, 'EPSG:28992', 'EPSG:4326', true)"
        bbox[level] = [None if r[0] is None else [round(v, 5) for v in r] for r in con.execute(
            f"SELECT ST_XMin({g}), ST_YMin({g}), ST_XMax({g}), ST_YMax({g}) FROM {level} ORDER BY idx").fetchall()]
    (SITE_DATA / "gebieden.json").write_text(json.dumps(
        {"meta": meta, "gemeenten": gem, "wijken": wijk, "buurten": buurt, "bbox": bbox,
         "velden": {"buurten": ["code", "naam", "gemeente", "wijk", "eerste", "woningen"]}},
        ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    rows = con.execute("SELECT kroon, afstand, bomen FROM woningen ORDER BY i").fetchnumpy()
    # NULLs arrive as masked values: fill them with NaN before encoding
    kroon, afst, bomen = (np.ma.filled(np.ma.asarray(rows[k]).astype(float), np.nan)
                          for k in ("kroon", "afstand", "bomen"))
    kroon = np.where(np.isnan(kroon), MISSING16, np.clip(np.round(kroon * 10), 0, 1000)).astype("<u2")
    afst = np.where(np.isnan(afst), MISSING16, np.clip(np.round(afst), 0, 65000)).astype("<u2")
    bomen = np.where(np.isnan(bomen), MISSING8, np.clip(bomen, 0, 254)).astype("u1")
    blob = kroon.tobytes() + afst.tobytes() + bomen.tobytes()
    with gzip.open(SITE_DATA / "woningen.bin.gz", "wb", compresslevel=9) as fh:
        fh.write(blob)
    log(f"woningen.bin.gz {(SITE_DATA / 'woningen.bin.gz').stat().st_size / 1e6:.1f} MB "
        f"({len(bomen):,} homes; missing: bomen {np.mean(bomen == MISSING8):.1%}, "
        f"kroon {np.mean(kroon == MISSING16):.1%}, afstand {np.mean(afst == MISSING16):.1%})")


def write_tiles(con):
    """kaart.pmtiles: the three area levels and the buildings."""
    out = SITE_DATA / "kaart.pmtiles"
    staging = SITE_DATA / "kaart.staging.gpkg"
    staging.unlink(missing_ok=True)
    rd = osr.SpatialReference()
    rd.ImportFromEPSG(28992)
    dst = ogr.GetDriverByName("GPKG").CreateDataSource(str(staging))
    queries = {
        "gemeenten": "SELECT idx, naam, ST_AsWKB(geom) FROM gemeenten",
        "wijken": "SELECT idx, naam, ST_AsWKB(geom) FROM wijken",
        "buurten": "SELECT idx, naam, ST_AsWKB(geom) FROM buurten WHERE geom IS NOT NULL",
    }
    for layer, sql in queries.items():
        lyr = dst.CreateLayer(layer, rd, ogr.wkbMultiPolygon)
        lyr.CreateField(ogr.FieldDefn("id", ogr.OFTInteger))
        lyr.CreateField(ogr.FieldDefn("naam", ogr.OFTString))
        lyr.StartTransaction()
        for idx, naam, wkb in con.execute(sql).fetchall():
            f = ogr.Feature(lyr.GetLayerDefn())
            f.SetField("id", int(idx))
            f.SetField("naam", naam)
            f.SetGeometry(ogr.CreateGeometryFromWkb(bytes(wkb)))
            lyr.CreateFeature(f)
        lyr.CommitTransaction()
        log(f"{layer}: {lyr.GetFeatureCount():,}")

    lyr = dst.CreateLayer("gebouwen", rd, ogr.wkbMultiPolygon)
    for name in ("bomen", "kroon", "afstand", "woningen", "buurt"):
        lyr.CreateField(ogr.FieldDefn(name, ogr.OFTInteger))
    cur = con.execute("""
        SELECT any_value(w.bomen), any_value(w.kroon), median(w.afstand), count(*), any_value(w.buurt_idx),
               ST_AsWKB(any_value(p.geom))
        FROM woningen w JOIN gebouwen p USING (pand_id) GROUP BY w.pand_id""")
    lyr.StartTransaction()
    while batch := cur.fetchmany(50000):
        for bomen, kroon, afstand, n, buurt, wkb in batch:
            f = ogr.Feature(lyr.GetLayerDefn())
            if bomen is not None:
                f.SetField("bomen", int(bomen))
            if kroon is not None:
                f.SetField("kroon", int(round(kroon * 10)))
            if afstand is not None:
                f.SetField("afstand", int(round(afstand)))
            f.SetField("woningen", int(n))
            f.SetField("buurt", int(buurt))
            f.SetGeometry(ogr.CreateGeometryFromWkb(bytes(wkb)))
            lyr.CreateFeature(f)
    lyr.CommitTransaction()
    log(f"gebouwen: {lyr.GetFeatureCount():,}")
    dst = None

    conf = {layer: {"minzoom": z0, "maxzoom": z1} for layer, (z0, z1) in ZOOMS.items()}
    tmp = out.with_suffix(".partial.pmtiles")
    tmp.unlink(missing_ok=True)
    log("vector tiles ...")
    gdal.VectorTranslate(str(tmp), str(staging), format="PMTiles", dstSRS="EPSG:3857",
                         datasetCreationOptions=["MINZOOM=6", "MAXZOOM=15", f"CONF={json.dumps(conf)}",
                                                 f"NAME=330300regel MCDA {config.PROVINCE}"])
    tmp.replace(out)
    staging.unlink()
    size = out.stat().st_size / 1e6
    log(f"kaart.pmtiles {size:.0f} MB" + ("  — over GitHub's 100 MB file limit!" if size > 100 else ""))


def write_local_db(con):
    """The local server's database: the same tables, plus the tree points."""
    LOCAL_DB.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_DB.unlink(missing_ok=True)
    con.execute(f"ATTACH '{LOCAL_DB}' AS loc")
    log("local database")
    con.execute("""
        CREATE TABLE loc.gemeenten AS SELECT idx, code, naam, geom FROM gemeenten;
        CREATE TABLE loc.wijken AS SELECT idx, code, naam, gem_idx, geom FROM wijken;
        CREATE TABLE loc.buurten AS SELECT idx, code, naam, gem_idx, wijk_idx, geom FROM buurten;
        CREATE TABLE loc.woningen AS
        SELECT i, buurt_idx, pand_id, bomen, kroon, afstand, ST_X(geom) AS x, ST_Y(geom) AS y
        FROM woningen ORDER BY i;
    """)
    # The trees the 3 counts (the excluded statuses left out), sorted on a
    # 250 m grid so a bounding-box query reads few row groups
    log("trees")
    con.execute(f"""
        CREATE TABLE loc.bomen AS
        SELECT ST_X(geom) AS x, ST_Y(geom) AS y, current_st AS status
        FROM st_read('{config.PROVINCE_TREES_GPKG}')
        WHERE NOT list_contains({list(config.TREE_STATUSES_EXCLUDED)}, current_st)
        ORDER BY floor(ST_Y(geom) / 250), floor(ST_X(geom) / 250);
    """)
    n = con.execute("SELECT count(*) FROM loc.bomen").fetchone()[0]
    con.execute("DETACH loc")
    log(f"{LOCAL_DB} ({LOCAL_DB.stat().st_size / 1e6:.0f} MB, {n:,} trees)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--skip-tiles", action="store_true")
    ap.add_argument("--skip-local", action="store_true")
    args = ap.parse_args()
    con = duckdb.connect()
    load(con)
    write_site_data(con)
    if not args.skip_tiles:
        write_tiles(con)
    if not args.skip_local:
        write_local_db(con)
    log("done")


if __name__ == "__main__":
    main()
