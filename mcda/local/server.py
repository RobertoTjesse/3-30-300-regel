"""
server.py — The local afwegingskaart: the static map (mcda/site) plus what a
static page on GitHub Pages cannot do, served from this machine only.

What it adds (local.js is the browser side):
  /api/gebied    homes inside a polygon you draw or load: an MCDA for any area
  /api/watals    what-if new green: walking distance re-estimated for every home
                 within 1,275 m of a polygon (straight line x detour factor)
  /api/bomen     the NEO tree points around the view (purchased data: local only)
  /api/export    the scores of every area as a GeoPackage or CSV
  /api/scenario  saved weightings, kept in local/data/scenarios.json

Only the Python standard library and DuckDB (with its spatial extension, as
mcda/build_data.py uses). It listens on 127.0.0.1, so only this computer sees it.

Usage (OSGeo4W Python, after mcda/build_data.py):
    python mcda/local/server.py [--port 8765]
then open http://127.0.0.1:8765
"""

import argparse
import json
import mimetypes
import re
import tempfile
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
SITE = HERE.parent / "site"
DB = HERE / "data" / "mcda.duckdb"
SCENARIOS = HERE / "data" / "scenarios.json"
MAX_AFSTAND = 1275          # the 300 measures no further (03_grass.py MAX_DISTANCE_M)
MAX_TREES = 25000           # tree points per request
INJECT = '<script>window.MCDA_LOCAL = true;</script>\n<link rel="stylesheet" href="local.css">\n' \
         '<script src="local.js" defer></script>\n</body>'

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/octet-stream", ".pmtiles")
mimetypes.add_type("application/gzip", ".gz")

_con = None
_scen_lock = threading.Lock()


def cursor():
    """A cursor of the shared read-only connection (one per request: thread-safe)."""
    cur = _con.cursor()
    cur.execute("LOAD spatial")
    return cur


def polygon_rd(cur, geometry):
    """A GeoJSON geometry in WGS84 as an RD New WKT, plus its bounding box."""
    row = cur.execute("""
        WITH g AS (SELECT ST_Transform(ST_GeomFromGeoJSON(?), 'EPSG:4326', 'EPSG:28992', true) AS g)
        SELECT ST_AsText(g), ST_XMin(g), ST_YMin(g), ST_XMax(g), ST_YMax(g), ST_Area(g), ST_IsValid(g) FROM g
    """, [json.dumps(geometry)]).fetchone()
    if row[0] is None:
        raise ValueError("geen geldige geometrie")
    if not row[6]:
        raise ValueError("de vorm snijdt zichzelf: teken hem opnieuw")
    return row[0], row[1:5], row[5]


def api_gebied(body):
    """Indices of the homes inside a polygon."""
    cur = cursor()
    wkt, (x0, y0, x1, y1), area = polygon_rd(cur, body["geometry"])
    idx = [r[0] for r in cur.execute("""
        SELECT i FROM woningen
        WHERE x BETWEEN ? AND ? AND y BETWEEN ? AND ?
          AND ST_Contains(ST_GeomFromText(?), ST_Point(x, y))
        ORDER BY i""", [x0, x1, y0, y1, wkt]).fetchall()]
    return {"indices": idx, "oppervlakte_ha": round(area / 1e4, 1)}


def api_watals(body):
    """New green: for every home within MAX_AFSTAND of the polygon, the walking
    distance estimated as straight-line distance x detour factor (0 inside),
    where that is shorter than the measured route."""
    omweg = min(max(float(body.get("omweg", 1.3)), 1.0), 3.0)
    cur = cursor()
    wkt, (x0, y0, x1, y1), area = polygon_rd(cur, body["geometry"])
    reach = MAX_AFSTAND / omweg
    rows = cur.execute("""
        WITH p AS (SELECT ST_GeomFromText(?) AS g),
        h AS (
          SELECT i, afstand, ST_Distance(p.g, ST_Point(x, y)) * ? AS nieuw
          FROM woningen, p
          WHERE x BETWEEN ? AND ? AND y BETWEEN ? AND ?)
        SELECT i, round(nieuw) FROM h
        WHERE nieuw <= ? AND (afstand IS NULL OR nieuw < afstand)
        ORDER BY i""", [wkt, omweg, x0 - reach, x1 + reach, y0 - reach, y1 + reach, MAX_AFSTAND]).fetchall()
    return {"wijzigingen": [[int(i), int(d)] for i, d in rows], "oppervlakte_ha": round(area / 1e4, 1),
            "omweg": omweg}


def api_bomen(query):
    """Tree points in a WGS84 bounding box, as GeoJSON."""
    m = re.fullmatch(r"(-?[\d.]+),(-?[\d.]+),(-?[\d.]+),(-?[\d.]+)", query.get("bbox", ""))
    if not m:
        raise ValueError("bbox=minlon,minlat,maxlon,maxlat")
    lon0, lat0, lon1, lat1 = map(float, m.groups())
    cur = cursor()
    x0, y0, x1, y1 = cur.execute("""
        WITH b AS (SELECT ST_Transform(ST_MakeEnvelope(?, ?, ?, ?), 'EPSG:4326', 'EPSG:28992', true) AS g)
        SELECT ST_XMin(g), ST_YMin(g), ST_XMax(g), ST_YMax(g) FROM b""", [lon0, lat0, lon1, lat1]).fetchone()
    rows = cur.execute("""
        SELECT ST_X(p), ST_Y(p), status FROM (
          SELECT ST_Transform(ST_Point(x, y), 'EPSG:28992', 'EPSG:4326', true) AS p, status
          FROM bomen WHERE x BETWEEN ? AND ? AND y BETWEEN ? AND ? LIMIT ?)""",
                       [x0, x1, y0, y1, MAX_TREES + 1]).fetchall()
    feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(a, 6), round(b, 6)]},
              "properties": {"status": s}} for a, b, s in rows[:MAX_TREES]]
    return {"type": "FeatureCollection", "features": feats, "afgekapt": len(rows) > MAX_TREES}


EXPORT_FIELDS = [("score", "DOUBLE"), ("alle", "DOUBLE"), ("bomen", "DOUBLE"), ("kroon", "DOUBLE"),
                 ("afstand", "DOUBLE"), ("mbomen", "DOUBLE"), ("mkroon", "DOUBLE"), ("mafstand", "DOUBLE"),
                 ("woningen", "INTEGER")]
EXPORT_NAMES = {"score": "score", "alle": "pct_alle_drie", "bomen": "score_3", "kroon": "score_30",
                "afstand": "score_300", "mbomen": "pct_doel_3", "mkroon": "pct_doel_30",
                "mafstand": "pct_doel_300", "woningen": "woningen"}


def api_export(body):
    """The areas of one level with the scores the browser computed, joined to
    their geometry: (bytes, filename, content type)."""
    level = body["level"]
    if level not in ("gemeenten", "wijken", "buurten"):
        raise ValueError("onbekend niveau")
    fmt = body.get("format", "gpkg")
    rows = body["rows"]
    cur = cursor()
    cols = ", ".join(f"{k} {t}" for k, t in EXPORT_FIELDS)
    # The weighting itself goes along in every row, so the file explains itself
    afweging = str(body.get("afweging", ""))[:500]
    cur.execute(f"CREATE TEMP TABLE uit (idx INTEGER, {cols}, afweging VARCHAR)")
    cur.executemany(f"INSERT INTO uit VALUES ({', '.join(['?'] * (len(EXPORT_FIELDS) + 2))})",
                    [[r["i"]] + [r.get(k) for k, _ in EXPORT_FIELDS] + [afweging] for r in rows])
    sel = ", ".join(f"round(u.{k}, 2) AS {EXPORT_NAMES[k]}" for k, _ in EXPORT_FIELDS) + ", u.afweging"
    stamp = time.strftime("%Y%m%d-%H%M")
    with tempfile.TemporaryDirectory() as tmp:
        if fmt == "csv":
            path = Path(tmp) / "uit.csv"
            cur.execute(f"""COPY (SELECT a.code, a.naam, {sel} FROM uit u JOIN {level} a ON a.idx = u.idx ORDER BY a.code)
                            TO '{path.as_posix()}' (HEADER, DELIMITER ',')""")
            name, ctype = f"afweging_{level}_{stamp}.csv", "text/csv"
        else:
            path = Path(tmp) / "uit.gpkg"
            cur.execute(f"""COPY (SELECT a.code, a.naam, {sel}, a.geom FROM uit u JOIN {level} a ON a.idx = u.idx
                                  WHERE a.geom IS NOT NULL ORDER BY a.code)
                            TO '{path.as_posix()}' WITH (FORMAT GDAL, DRIVER 'GPKG', SRS 'EPSG:28992',
                                                          LAYER_NAME '{level}')""")
            name, ctype = f"afweging_{level}_{stamp}.gpkg", "application/geopackage+sqlite3"
        return path.read_bytes(), name, ctype


def load_scenarios():
    if SCENARIOS.exists():
        return json.loads(SCENARIOS.read_text(encoding="utf-8"))
    return []


def save_scenarios(items):
    tmp = SCENARIOS.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(SCENARIOS)


class Handler(BaseHTTPRequestHandler):
    server_version = "afwegingskaart/1"

    def log_message(self, fmt, *args):     # quieter: only errors
        if args and str(args[1])[:1] in "45":
            super().log_message(fmt, *args)

    # ---------------------------------------------------------------- helpers
    def send_json(self, obj, status=HTTPStatus.OK):
        raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self):
        n = int(self.headers.get("Content-Length", 0))
        if n > 50_000_000:
            raise ValueError("verzoek te groot")
        return json.loads(self.rfile.read(n) or b"{}")

    def send_file(self, path):
        """A static file, with HTTP Range support (PMTiles reads byte ranges)."""
        size = path.stat().st_size
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        if rng:
            m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
            if not m:
                self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                return
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:
                start = max(size - int(m.group(2)), 0)
            end = min(end, size - 1)
            if start > end:
                self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                return
        self.send_response(HTTPStatus.PARTIAL_CONTENT if rng else HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with path.open("rb") as fh:
            fh.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = fh.read(min(1 << 20, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def static_path(self, url_path):
        """Map a URL path to a file in local/ (local.*) or site/, never outside."""
        rel = url_path.lstrip("/") or "index.html"
        for root in (HERE, SITE) if rel.startswith("local.") else (SITE,):
            p = (root / rel).resolve()
            if p.is_file() and root.resolve() in p.parents:
                return p
        return None

    # ---------------------------------------------------------------- routes
    def do_GET(self):
        path, _, qs = self.path.partition("?")
        query = dict(kv.split("=", 1) for kv in qs.split("&") if "=" in kv)
        try:
            if path in ("/", "/index.html"):
                html = (SITE / "index.html").read_text(encoding="utf-8").replace("</body>", INJECT, 1)
                html = html.replace("<title>Afwegingskaart 3-30-300</title>", "<title>Afwegingskaart 3-30-300 · lokaal</title>")
                raw = html.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            elif path == "/api/bomen":
                self.send_json(api_bomen(query))
            elif path == "/api/scenario":
                with _scen_lock:
                    self.send_json(load_scenarios())
            else:
                p = self.static_path(path)
                if p is None:
                    self.send_error(HTTPStatus.NOT_FOUND)
                else:
                    self.send_file(p)
        except (ValueError, KeyError) as exc:
            self.send_json({"fout": str(exc)}, HTTPStatus.BAD_REQUEST)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        try:
            body = self.read_json()
            if self.path == "/api/gebied":
                self.send_json(api_gebied(body))
            elif self.path == "/api/watals":
                self.send_json(api_watals(body))
            elif self.path == "/api/export":
                raw, name, ctype = api_export(body)
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                self.end_headers()
                self.wfile.write(raw)
            elif self.path == "/api/scenario":
                naam = str(body.get("naam", "")).strip()[:80]
                if not naam:
                    raise ValueError("geef het scenario een naam")
                item = {"id": uuid.uuid4().hex[:10], "naam": naam, "params": body["params"],
                        "watals": body.get("watals"), "gemaakt": time.strftime("%Y-%m-%d %H:%M")}
                with _scen_lock:
                    items = load_scenarios()
                    items.append(item)
                    save_scenarios(items)
                self.send_json(item, HTTPStatus.CREATED)
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, KeyError, json.JSONDecodeError, duckdb.Error) as exc:
            self.send_json({"fout": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_DELETE(self):
        m = re.fullmatch(r"/api/scenario/([0-9a-f]{10})", self.path)
        if not m:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        with _scen_lock:
            items = [s for s in load_scenarios() if s["id"] != m.group(1)]
            save_scenarios(items)
        self.send_json({"ok": True})


def main():
    global _con
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    if not DB.exists() or not (SITE / "data" / "kaart.pmtiles").exists():
        raise SystemExit("Run mcda/build_data.py first.")
    _con = duckdb.connect(str(DB), read_only=True)
    _con.execute("LOAD spatial")
    n = _con.execute("SELECT count(*) FROM woningen").fetchone()[0]
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Afwegingskaart (lokaal): http://127.0.0.1:{args.port}  ({n:,} woningen; Ctrl+C stopt)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
