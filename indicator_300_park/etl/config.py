"""
config.py — Settings for the open 300 pipeline (parks within walking distance).

The 300 is computed twice, with two routing engines, so the results can be
compared:

  01_prepare.py      parks, park entrances, walking network and homes
  02_valhalla.py     walking distance per home with Valhalla (pyvalhalla)
  03_grass.py        walking distance per home with GRASS v.net.distance
  04_compare.py      both against each other and against the old FME result

Paths and the province come from indicator_3_bomen/etl/config.py (and its
config_local.py), so the 300 uses the same BAG homes as the 3. Put your own
values in config_local_300.py next to this file (gitignored); everything
below is a default.
"""
import importlib.util
import os
import sys
from pathlib import Path

# The config of the 3 is also called config.py, so load it under its own name.
_etl3 = Path(__file__).resolve().parents[2] / "indicator_3_bomen" / "etl"
sys.path.insert(0, str(_etl3))   # for its config_local.py
_spec = importlib.util.spec_from_file_location("config_3", _etl3 / "config.py")
_c3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_c3)
sys.path.remove(str(_etl3))
for _name in ("BASE_DIR", "DATA_DIR", "RAW_DIR", "INTERIM_DIR", "PROCESSED_DIR", "OSGEO4W_ROOT",
              "PROVINCE", "PROVINCE_SLUG", "FME_INPUT_DIR", "FME_OUTPUT_DIR",
              "PROVINCE_ADDRESSES_GPKG", "BAG_ADDRESS_USE", "BAG_ADDRESS_STATUS",
              "BAG_ADDRESS_BUILDING_ID", "BAG_STATUSES_IN_USE"):
    globals()[_name] = getattr(_c3, _name)
GEBIEDEN_GPKG = PROCESSED_DIR / f"{PROVINCE_SLUG}_gebieden.gpkg"   # CBS 2025 gemeenten/wijken/buurten
HOMES_GPKG = PROCESSED_DIR / f"{PROVINCE_SLUG}_woningen.gpkg"      # the homes of the 3 (pand_id, n_woningen)

# ---------------------------------------------------------------------------
# Input: the parks
# ---------------------------------------------------------------------------
# One or more polygon layers GDAL can read. Step 1 keeps the polygons of at least
# MIN_PARK_AREA_M2 that are not narrow strips, and merges overlapping and
# touching polygons of all sources into one green area. Per source: bron (a
# label), path, layer, optional where (SQL), and the type and name: a field
# (type_field, name_field) or a fixed type.
#   voorzieningen  layer parken_vlakken of the provincial voorzieningen
#                  geodatabase (OpenStreetMap and TOP10NL parks, peildatum
#                  17 September 2025), copied to data/raw/voorzieningen/
#   top10nl        TOP10NL functioneel gebied, the green kinds (botanische tuin,
#                  heemtuin, landgoed, nationaal park, natuurgebied,
#                  recreatiegebied, park), exported from the provincial SDE to
#                  data/raw/top10_groen/ by a local arcpy script
# Both are gitignored. The green of the old FME workbench, for comparison, as
# the only source in config_local_300.py (which needs full paths):
#   from pathlib import Path
#   PARKS_SOURCES = [dict(bron="fme groen", layer="groen_uit_osm_top10_2024", type_field="type",
#                         path=Path(r"D:\Repositories\330300regel\data\fme_input\groenvoorzieningen\groenkaart.gdb"),
#                         where="\"bron\" <> 'top 10 water'")]
PARKS_SOURCES = [
    dict(bron="voorzieningen", path=RAW_DIR / "voorzieningen" / "voorzieningen.gdb",
         layer="parken_vlakken", type="park", name_field="naam"),
    dict(bron="top10nl", path=RAW_DIR / "top10_groen" / "top10_groen.gdb",
         layer="functioneel_gebied_groen", type_field="typefunctioneelgebied", name_field="naamnl"),
    # All of TOP10NL functioneel gebied as a shapefile; only these kinds count.
    # Duck decoys (eendenkooi) are mostly fenced and closed to visitors.
    dict(bron="top10nl recreatie", path=RAW_DIR / "top10_recreatiegebieden" / "Top10_recreatiegebieden.shp",
         where="typefuncti IN ('begraafplaats', 'volkstuinen', 'eendenkooi')",
         type_field="typefuncti", name_field="naamnl"),
]
MIN_PARK_AREA_M2 = 300         # parks smaller than this are left out (WHO: 10,000)
# Narrow strips are left out: perimeter / area at most this (1/m). 0.35 keeps a
# strip of about 6 m wide or more, as the old FME workbench did. None = keep all.
MAX_EDGE_RATIO = 0.35

# ---------------------------------------------------------------------------
# Input: OpenStreetMap
# ---------------------------------------------------------------------------
# Geofabrik extract, the same file for the entrances, the GRASS network and the
# Valhalla tiles, so all three have one network date.
OSM_PBF = RAW_DIR / "osm" / "zuid-holland-latest.osm.pbf"
OSM_GPKG = INTERIM_DIR / "osm" / "osm_ways_points.gpkg"   # made from OSM_PBF by 01_prepare.py

# Ways a pedestrian can use. Cycle paths count: Dutch traffic law (RVV 1990,
# art. 4) lets pedestrians use one where there is no footpath or pavement.
# Bridleways (horses only), motorways, trunk roads and their slip roads are left
# out, unless foot= says walking is allowed; so are ways tagged access=no/private
# or foot=no.
WALKABLE_HIGHWAYS = (
    "footway", "path", "pedestrian", "living_street", "steps", "track",
    "residential", "service", "unclassified", "road", "cycleway",
    "tertiary", "tertiary_link", "secondary", "secondary_link",
    "primary", "primary_link",
)
FOOT_ALLOWED = ("yes", "designated", "permissive", "official")
ACCESS_CLOSED = ("no", "private")

# ---------------------------------------------------------------------------
# Park entrances (01_prepare.py)
# ---------------------------------------------------------------------------
# 1. Where a walkable way crosses the park edge.
# 2. OSM gates and entrances (barrier=gate, entrance=*) within GATE_SNAP_M of
#    the edge.
# 3. Parks with neither (open green along a street): points every
#    EDGE_SPACING_M along the edge, wherever a walkable way runs within
#    EDGE_STREET_M.
GATE_SNAP_M = 5.0
EDGE_SPACING_M = 25.0
EDGE_STREET_M = 10.0
ENTRANCE_MERGE_M = 10.0        # entrances of one park closer than this are merged

# ---------------------------------------------------------------------------
# Walking distance
# ---------------------------------------------------------------------------
WALK_SPEED_KMH = 5.1           # Valhalla's default pedestrian speed
THRESHOLDS_M = {               # the classes reported per home
    "300m": 300,
    "5min": round(5 / 60 * WALK_SPEED_KMH * 1000),    # 425 m
    "15min": round(15 / 60 * WALK_SPEED_KMH * 1000),  # 1275 m
}
MAX_DISTANCE_M = max(THRESHOLDS_M.values())   # beyond this a home gets no distance
HOME_SNAP_M = 75.0             # a home further than this from any walkable way gets no distance

# ---------------------------------------------------------------------------
# Study area
# ---------------------------------------------------------------------------
# Municipalities (CBS names, as in <Province>_gebieden.gpkg) to compute; empty =
# the whole province. Parks, entrances and network are taken with a margin of
# AREA_MARGIN_M around them, so a park just across the border still counts.
MUNICIPALITIES = ["Delft"]
AREA_MARGIN_M = MAX_DISTANCE_M + 250

# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------
VALHALLA_DIR = INTERIM_DIR / "valhalla"           # valhalla.json and the tiles
VALHALLA_PYTHON = Path.home() / "valhalla-venv" / "Scripts" / "python.exe"
VALHALLA_WORKERS = 6
# Valhalla's tag rules: its own graph.lua with cycle paths open to pedestrians,
# so Valhalla walks the same ways as GRASS. Used by 00_valhalla_tiles.py.
VALHALLA_LUA = BASE_DIR / "indicator_300_park" / "valhalla" / "graph_fietspad.lua"
GRASS_BAT = Path(OSGEO4W_ROOT) / "bin" / "grass84.bat"

# Road types GRASS leaves out of its network, for a test run. Empty = every
# walkable way of 01_prepare.py. The env var GRASS_EXCLUDE="cycleway" does this
# for one run; its outputs get the suffix _zonder_cycleway.
GRASS_EXCLUDE_HIGHWAYS = ()

try:
    from config_local_300 import *  # noqa: F401,F403
except ImportError:
    pass

if os.environ.get("GRASS_EXCLUDE") is not None:
    GRASS_EXCLUDE_HIGHWAYS = tuple(h for h in os.environ["GRASS_EXCLUDE"].split(",") if h)

_single = os.environ.get("MUNICIPALITY_OVERRIDE")
if _single:
    MUNICIPALITIES = [_single]

AREA_SLUG = ("_".join(MUNICIPALITIES) if MUNICIPALITIES else PROVINCE_SLUG).replace(" ", "")
WORK_DIR = INTERIM_DIR / "300" / AREA_SLUG
PREPARED_GPKG = WORK_DIR / "prepared.gpkg"        # parks, entrances, network, homes
VALHALLA_GPKG = WORK_DIR / "valhalla.gpkg"
# A GRASS run without some road types is a variant, with its own outputs.
VARIANT = ("_zonder_" + "_".join(GRASS_EXCLUDE_HIGHWAYS)) if GRASS_EXCLUDE_HIGHWAYS else ""
GRASS_GPKG = WORK_DIR / f"grass{VARIANT}.gpkg"
RESULT_GPKG = PROCESSED_DIR / f"{AREA_SLUG}_300{VARIANT}.gpkg"
RESULT_CSV = PROCESSED_DIR / f"{AREA_SLUG}_300_vergelijking{VARIANT}.csv"
