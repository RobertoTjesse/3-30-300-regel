r"""
isochronen_osm.py - QGIS Processing script: isochrones from your own points, by
walking, cycling or car, as distance or time, over OpenStreetMap.

INSTALL (once)
  1. QGIS menu Processing > Toolbox.
  2. In the Toolbox, click the Python icon (top row) > "Add Script to Toolbox..."
     and pick this file.
  3. The tool is now under Scripts > Bereikbaarheid > "Isochrones from points
     (OSM)". Double-click it to get its form, or drag it into the Model
     Designer (Processing > Model Designer) as a step of a model.

WHAT YOU FILL IN
  Start points       any point layer in QGIS (stations, schools, homes, ...).
  Point name field   optional: a field whose value is copied to the result.
  Transport          walking, cycling or car.
  Measure            distance (metres) or time (minutes).
  Values             one or more values, comma separated: "5, 10, 15" (minutes)
                     or "300, 500, 1000" (metres).
  Merge points       off: one isochrone per point per value.
                     on: one isochrone per value for all points together
                     (everything within reach of the nearest point).
  OSM download area  one or more Dutch provinces, or the whole Netherlands,
                     from Geofabrik, downloaded once to
                     %LOCALAPPDATA%\isochronen_osm. Default: Zuid-Holland.
                     * marks the provinces that border Zuid-Holland
                     (Noord-Holland, Utrecht, Gelderland, Noord-Brabant,
                     Zeeland): for points near that border, tick the
                     neighbour as well, or the network and the isochrone
                     stop at the border. Several provinces together work;
                     the whole Netherlands is the simplest for long car trips.
  OSM file           optional: your own .osm.pbf. When given, the download
                     area is ignored.
  Advanced           speeds, cell size, buffer, snap distance (see below), and
                     the optional Expansion output.

WHAT YOU GET
  A polygon layer in RD New (EPSG:28992), one feature per point (or for all
  points) per value: point_id, point_name, mode, measure, value, unit, km2.
  Style it by "value" (Symbology > Categorized) and put the smallest on top.

  Expansion (optional, under Advanced; tick the box and it appears in the map
  as a temporary layer "Expansion ..." when the tool finishes): like
  Valhalla's expansion, every street segment the route search reaches, as a
  line between two OSM nodes: point_id, point_name, mode, measure, unit,
  cost_from (time or distance at the end reached first), cost_to (at the
  other end; empty when the search stops halfway along the segment), order
  (1 = reached first). Style it by cost_from (Graduated) to see how the
  search spreads out over the network.

HOW IT WORKS
  1. Reads the OSM ways once into a GeoPackage next to the .pbf (about a
     minute), then only the part within reach of your points.
  2. Builds the network for the chosen transport. Which ways it may use,
     and in which direction, follows Valhalla 3.9.1: its graph.lua and the
     access changes in its C++ code, written out below in foot_rule(),
     bike_rule() and motor_rule(). Checked against Valhalla's own tiles for
     the whole Netherlands (vergelijk_valhalla.py): the same for 99.96% of
     the km on foot and by bicycle, 100% for car, truck and motorcycle.
       walking  5.1 km/h. Cycle paths are open to pedestrians, the one
                change this repository makes to Valhalla (RVV 1990 art. 4).
       cycling  15 km/h. Steps at 1 km/h and avoided (cost x 8),
                bicycle=dismount at 5.1 km/h.
       car, truck, motorcycle  the maxspeed tag, or the default per road
                type in CAR_SPEED_KMH below; trucks at most 80 km/h.
     Driveways, parking aisles and access=destination roads are reached but
     not used as a short cut: 600 s is added to the cost of entering one;
     the isochrone shows the travel time. Private roads (access=private)
     stay closed, unless you tick "Enter private roads": then they work like
     driveways, as in Valhalla.
     Ways only meet where OSM says they share a node: a road on a bridge does
     not meet the road below it. Every way is cut into pieces of at most the
     cell size.
  3. Puts each start point on the nearest node of the network (for the car:
     not on a motorway or trunk road), within the snap distance.
  4. Dijkstra from each point (or from all points at once when merged) over
     the network, by length or by travel time.
  5. Draws every reached network point into a raster, spreads it over the
     buffer distance (land that close to a reached way is inside), and turns
     each value into a polygon.
  Not modelled: waiting at traffic lights and junctions, congestion, ferries,
  public transport, turning restrictions.

TESTED with QGIS 3.40 / GDAL 3.12 on Windows. It uses only what comes with
QGIS: GDAL, numpy and scipy.
"""
import math
import os
import re
import time
import urllib.request

from qgis.PyQt.QtCore import QCoreApplication, QMetaType, QVariant
from qgis.core import (
    Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature, QgsFeatureSink, QgsField,
    QgsFields, QgsGeometry, QgsPointXY, QgsProcessing, QgsProcessingAlgorithm, QgsProcessingException,
    QgsProcessingParameterBoolean, QgsProcessingParameterDefinition, QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink, QgsProcessingParameterFeatureSource, QgsProcessingParameterField,
    QgsProcessingContext, QgsProcessingOutputVectorLayer, QgsProcessingParameterFile,
    QgsProcessingParameterNumber, QgsProcessingParameterString, QgsVectorLayer, QgsWkbTypes,
)

# ===========================================================================
# SETTINGS
# ===========================================================================
GEOFABRIK = "https://download.geofabrik.de/europe/"
# The download areas in the form: (label, Geofabrik path). Sizes of November 2026.
# * = borders Zuid-Holland.
OSM_AREAS = [
    ("Zuid-Holland (212 MB)", "netherlands/zuid-holland"),
    ("Noord-Holland * (190 MB)", "netherlands/noord-holland"),
    ("Utrecht * (99 MB)", "netherlands/utrecht"),
    ("Gelderland * (195 MB)", "netherlands/gelderland"),
    ("Noord-Brabant * (207 MB)", "netherlands/noord-brabant"),
    ("Zeeland * (44 MB)", "netherlands/zeeland"),
    ("Drenthe (63 MB)", "netherlands/drenthe"),
    ("Flevoland (36 MB)", "netherlands/flevoland"),
    ("Friesland (89 MB)", "netherlands/friesland"),
    ("Groningen (56 MB)", "netherlands/groningen"),
    ("Limburg (100 MB)", "netherlands/limburg"),
    ("Overijssel (114 MB)", "netherlands/overijssel"),
    ("Whole Netherlands (1.4 GB; the first read takes 10 minutes or more)", "netherlands"),
]
CACHE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "isochronen_osm")

MODES = ["Walking", "Cycling", "Car", "Truck", "Motorcycle"]   # rule() also knows "Bus" (vergelijk_valhalla.py)
MEASURES = ["Time (minutes)", "Distance (metres)"]

WALK_SPEED_KMH = 5.1                 # Valhalla's default walking speed
BIKE_SPEED_KMH = 15.0                # an ordinary city bike
# Car speed per road type where OSM has no maxspeed tag (km/h, Dutch defaults).
CAR_SPEED_KMH = {
    "motorway": 100, "motorway_link": 60, "trunk": 80, "trunk_link": 50,
    "primary": 70, "primary_link": 50, "secondary": 60, "secondary_link": 40,
    "tertiary": 50, "tertiary_link": 40, "unclassified": 50, "road": 40,
    "residential": 30, "living_street": 15, "service": 15,
}
# Defaults per transport for the advanced settings (0 in the form = these).
CELL_M = {"Walking": 10, "Cycling": 20, "Car": 50, "Truck": 50, "Motorcycle": 50, "Bus": 50}   # piece length
BUFFER_M = {"Walking": 30, "Cycling": 40, "Car": 100, "Truck": 100, "Motorcycle": 100, "Bus": 100}   # land this close to a reached way is inside

ALLOWED = ("yes", "designated", "permissive", "official", "destination")
NO_CAR_START = ("motorway", "motorway_link", "trunk", "trunk_link")

# Cycling follows Valhalla 3.9.1: the bicycle part of its graph.lua (way access,
# per direction) and of its bicycle costing (steps, dismount, destination-only).
# https://github.com/valhalla/valhalla/blob/3.9.1/lua/graph.lua
# A highway value not in V_HIGHWAY is closed to bicycles, whatever its tags.
V_HIGHWAY = {
    "motorway": False, "motorway_link": False, "trunk": True, "trunk_link": True,
    "primary": True, "primary_link": True, "secondary": True, "secondary_link": True,
    "tertiary": True, "tertiary_link": True, "residential": True, "residential_link": True,
    "unclassified": True, "road": True, "service": True, "track": True, "living_street": True,
    "steps": True, "cycleway": True, "path": True, "footway": False, "pedestrian": False,
    "bridleway": False, "bus_guideway": False, "busway": False, "corridor": False, "elevator": False,
    "platform": False, "via_ferrata": False, "undefined": False, "unknown": False,
}
_Y = ("yes", "designated", "permissive", "official", "public", "restricted", "allowed", "permit", "residents")
V_ACCESS = {**dict.fromkeys(_Y + ("private", "use_sidepath", "delivery", "dismount", "destination",
                                  "customers"), True),
            **dict.fromkeys(("no", "agricultural", "discouraged", "forestry", "emergency", "psv"), False)}
V_VEHICLE = {**dict.fromkeys(_Y + ("private", "delivery", "destination", "customers"), True),
             **dict.fromkeys(("no", "agricultural", "discouraged", "forestry"), False)}
V_BICYCLE = {**dict.fromkeys(("yes", "designated", "use_sidepath", "permissive", "destination", "dismount", "lane",
                              "track", "shared", "shared_lane", "sidepath", "share_busway", "allowed", "private",
                              "official", "permit", "residents"), True),
             **dict.fromkeys(("no", "none"), False)}
V_CYCLEWAY = dict.fromkeys(("yes", "designated", "use_sidepath", "permissive", "destination", "dismount", "lane",
                            "track", "shared", "shared_lane", "sidepath", "share_busway", "allowed", "private",
                            "cyclestreet", "crossing"), True)
V_BIKE_REVERSE = ("opposite", "opposite_lane", "opposite_track")
V_ONEWAY = {"no": False, "false": False, "-1": True, "yes": True, "true": True, "1": True,
            "reversible": False, "alternating": False}
V_PRIVATE = ("private", "destination", "customers", "delivery", "permit", "residents")
V_LANE_BOTH_WAYS = ("shared_lane", "share_busway", "shared", "opposite_track", "track", "opposite_lane", "lane",
                    "buffered_lane")
V_DEST_ONLY_SERVICE = ("driveway", "parking_aisle", "drive-through")
# Not in the graph: include_construction and include_platforms are false in valhalla.json.
V_NOT_IN_GRAPH = ("construction", "proposed", "platform")
V_MOTORROAD_CLASS = ("motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link")
# Walking, as Valhalla 3.9.1 with the one change of indicator_300_park/valhalla/graph_fietspad.lua:
# pedestrians may use a cycle path (RVV 1990 art. 4: where there is no footpath or pavement).
V_HIGHWAY_FOOT = {**{k: True for k, v in V_HIGHWAY.items()},
                  **dict.fromkeys(("motorway", "motorway_link", "bridleway", "bus_guideway", "busway",
                                   "undefined", "unknown"), False)}
V_FOOT = {**dict.fromkeys(_Y + ("private", "use_sidepath", "delivery", "destination", "customers", "crossing",
                                "sidewalk", "passable", "footway"), True),
          **dict.fromkeys(("no", "agricultural", "discouraged", "forestry"), False)}
V_FOOT_ONEWAY_HIGHWAYS = ("footway", "pedestrian", "steps", "path")
# Motor vehicles (Car, Truck, Motorcycle, Bus), from the same graph.lua.
V_HIGHWAY_MOTOR = {k: k in CAR_SPEED_KMH or k in ("residential_link", "track", "living_street")
                   for k in V_HIGHWAY}
V_HIGHWAY_BUS = {**V_HIGHWAY_MOTOR, "bus_guideway": True, "busway": True}
V_TRUCK = {**dict.fromkeys(("designated", "yes", "destination", "delivery", "local", "private", "permissive",
                            "official", "permit", "residents"), True),
           **dict.fromkeys(("no", "agricultural", "discouraged", "unsuitable", "forestry"), False)}
V_BUS = {**dict.fromkeys(("yes", "designated", "urban", "permissive", "restricted", "destination", "official",
                          "permit"), True), "no": False, "delivery": False}
V_PSV = {**dict.fromkeys(("bus", "taxi", "yes", "designated", "permissive", "1", "2"), True), "no": False}
MOTOR_TAG = {"Car": "motorcar", "Truck": "hgv", "Motorcycle": "motorcycle", "Bus": "bus"}
# Legal top speeds in the Netherlands (RVV 1990 art. 21): lorries over 3.5 t 80 km/h; buses 80 km/h
# (100 km/h for buses approved for it, not assumed here).
MAX_KMH = {"Truck": 80, "Bus": 80}
STEPS_KMH = 1.0                # Valhalla: steps at 1 km/h, carrying the bike ...
STEPS_COST_FACTOR = 8.0        # ... and their cost times 8 (kBicycleStepsFactor), so they are avoided
DISMOUNT_KMH = 5.1             # Valhalla: an edge marked dismount at walking speed (here: bicycle=dismount)
DEST_ONLY_PENALTY_S = 600      # Valhalla's destination_only_penalty: on the cost, not on the travel time
# ===========================================================================

# Field types: QMetaType from QGIS 3.38, QVariant before (deprecated since).
if Qgis.versionInt() >= 33800:
    INT, TEXT, REAL = QMetaType.Type.LongLong, QMetaType.Type.QString, QMetaType.Type.Double
else:
    INT, TEXT, REAL = QVariant.LongLong, QVariant.String, QVariant.Double

TAG = re.compile(r'"([^"]+)"=>"((?:[^"\\]|\\.)*)"')


def tags_of(feature):
    """The OSM tags of a GDAL OSM feature: its own columns plus other_tags."""
    tags = dict(TAG.findall(feature.GetField("other_tags") or ""))
    for i in range(feature.GetFieldCount()):
        name = feature.GetFieldDefnRef(i).GetName()
        if name not in ("other_tags", "osm_id") and feature.IsFieldSet(i):
            tags[name] = feature.GetField(i)
    return tags


def any_in(table, value):
    """Valhalla's any_in: the value, or for 'a;b' the first part that is True (else the last one known)."""
    if value is None:
        return None
    if value in table:
        return table[value]
    found = None
    for part in value.split(";"):
        found = table.get(part, found)
        if found is True:
            break
    return found


def first(*values):
    """Lua's 'a or b' on Valhalla's "true"/"false" strings: the first value that is set (False counts)."""
    return next((v for v in values if v is not None), None)


def bike_rule(tags):
    """Valhalla 3.9.1 bicycle access of a way: None, or (forward, backward, steps, dismount, destination only).
    Follows filter_tags_generic() in graph.lua; its HOV-only-lane rule is left out (not used in the Netherlands)."""
    hw = tags.get("highway")
    if hw in V_NOT_IN_GRAPH:
        return None
    if tags.get("area") == "yes":
        return None
    base = V_HIGHWAY.get(hw)
    if base is None:
        return None
    access = any_in(V_ACCESS, tags.get("access"))
    vehicle = any_in(V_VEHICLE, tags.get("vehicle"))
    fwd = base
    if (tags.get("impassable") == "yes" or access is False or tags.get("smoothness") == "impassable"
            or (tags.get("access") == "private"
                and (tags.get("emergency") == "yes" or tags.get("service") == "emergency_access"))):
        fwd = False
    bike_tag = first(any_in(V_BICYCLE, tags.get("bicycle")), any_in(V_CYCLEWAY, tags.get("cycleway")),
                     any_in(V_BICYCLE, tags.get("bicycle_road")), V_BICYCLE.get(tags.get("cyclestreet")), vehicle)
    fwd = first(bike_tag, fwd)
    if tags.get("service") == "driveway" and "access" not in tags:
        fwd = True
    ow_raw, ow_bike = tags.get("oneway"), tags.get("oneway:bicycle")
    bwd = None
    if (ow_raw == "yes" and ow_bike == "no") or tags.get("bicycle:backward") in ("yes", "no"):
        bwd = True
    if not bwd:
        bwd = any(tags.get(k) in V_BIKE_REVERSE for k in ("cycleway", "cycleway:left", "cycleway:right"))
    oneway_bike = V_ONEWAY.get(ow_bike) if bwd else None
    oneway = V_ONEWAY.get(ow_raw)
    if tags.get("junction") in ("roundabout", "circular"):
        oneway = True
    if oneway is True:
        if bwd and oneway_bike is True:
            fwd = False
        elif bwd and oneway_bike is False:
            fwd = True
    elif not bwd and ow_bike != "-1" and ow_bike in (None, "no"):
        bwd = fwd
    sides = [tags.get(k) for k in ("cycleway:right", "cycleway:left")]
    if tags.get("cycleway:both") in V_LANE_BOTH_WAYS or all(s in V_LANE_BOTH_WAYS for s in sides):
        fwd = bwd = True
    if "bicycle:forward" in tags or "vehicle:forward" in tags:
        fwd = any_in(V_BICYCLE, tags.get("bicycle:forward") or tags.get("vehicle:forward"))
    if "bicycle:backward" in tags or "vehicle:backward" in tags:
        bwd = any_in(V_BICYCLE, tags.get("bicycle:backward") or tags.get("vehicle:backward"))
    if ow_raw == "-1":
        fwd, bwd = bwd, fwd
    if ow_bike == "-1":
        fwd, bwd = bwd, fwd
    # Valhalla's C++ (pbfgraphparser.cc), after graph.lua: mountain-bike tags open the way to bicycles, also
    # over bicycle=no or oneway:bicycle; only oneway itself counts. Scales below kMaxMtbScale = 6 and
    # kMaxMtbUphillScale = 5 (baldr/graphconstants.h); mtb:scale:imba and mtb:description only when no scale.
    scale, uphill = (int(tags[k]) if re.fullmatch(r"\d+", tags.get(k, "")) else -1
                     for k in ("mtb:scale", "mtb:scale:uphill"))
    mtb = (0 <= scale < 6 or 0 <= uphill < 5
           or ("mtb:scale" not in tags and "mtb:scale:uphill" not in tags
               and ("mtb:scale:imba" in tags or "mtb:description" in tags)))
    # ... but only on a way graph.lua kept: it drops a way that no transport may use (access=no, agricultural).
    if mtb and not (fwd or bwd or foot_rule(tags) or any(motor_rule(tags, m) for m in MOTOR_TAG)
                    or tags.get("emergency") == "yes" or tags.get("access") == "emergency"
                    or any_in(V_BICYCLE, tags.get("moped")) or any_in(V_BICYCLE, tags.get("mofa"))):
        mtb = False
    if mtb:
        if ow_raw != "-1":
            fwd = True
        if oneway is not True:
            bwd = True
    # Valhalla's C++ (graphenhancer.cc, countryaccess.cc): motorroad=yes on roads of class primary and up
    # loses bicycle access unless a bicycle tag (bike_tag above) sets it.
    if tags.get("motorroad") == "yes" and hw in V_MOTORROAD_CLASS and bike_tag is None:
        return None
    if not (fwd or bwd):
        return None
    dest_only = (tags.get("service") in V_DEST_ONLY_SERVICE
                 or any(any_in(dict.fromkeys(V_PRIVATE, True), tags.get(k))
                        for k in ("access", "motor_vehicle", "motorcar", "vehicle")))
    dismount = tags.get("bicycle") == "dismount"
    return bool(fwd), bool(bwd), hw == "steps", dismount, bool(dest_only)


def foot_rule(tags):
    """Valhalla 3.9.1 pedestrian access of a way: None, or (forward, backward). Same source as bike_rule."""
    hw = tags.get("highway")
    if hw in V_NOT_IN_GRAPH:
        return None
    if tags.get("area") == "yes" and hw != "pedestrian":
        return None
    fwd = V_HIGHWAY_FOOT.get(hw)
    if fwd is None:
        return None
    access = any_in(V_ACCESS, tags.get("access"))
    if (tags.get("impassable") == "yes" or access is False
            or (tags.get("access") == "private"
                and (tags.get("emergency") == "yes" or tags.get("service") == "emergency_access"))):
        fwd = False
    foot_tag = first(any_in(V_FOOT, tags.get("foot")), V_FOOT.get(tags.get("pedestrian")))
    fwd = first(foot_tag, fwd)
    if tags.get("service") == "driveway" and "access" not in tags:
        fwd = True
    ow_raw, ow_foot = tags.get("oneway"), tags.get("oneway:foot")
    bwd = (ow_raw == "yes" and ow_foot == "no") or tags.get("foot:backward") == "yes"
    oneway_foot = V_ONEWAY.get(ow_foot) if bwd else None
    oneway = V_ONEWAY.get(ow_raw)
    if tags.get("junction") in ("roundabout", "circular"):
        oneway = True
    if oneway is True:
        if hw in V_FOOT_ONEWAY_HIGHWAYS or "oneway:foot" in tags:   # oneway binds pedestrians only here
            if bwd and oneway_foot is True:
                fwd = False
            elif bwd and oneway_foot is False:
                fwd = True
        else:
            bwd = fwd
    elif not bwd and ow_foot in (None, "no"):
        bwd = fwd
    if "foot:forward" in tags:
        fwd = any_in(V_FOOT, tags["foot:forward"])
    if "foot:backward" in tags:
        bwd = any_in(V_FOOT, tags["foot:backward"])
    if ow_raw == "-1":
        fwd, bwd = bwd, fwd
    if ow_foot == "-1":
        fwd, bwd = bwd, fwd
    if tags.get("motorroad") == "yes" and hw in V_MOTORROAD_CLASS and foot_tag is None:
        return None
    if not (fwd or bwd):
        return None
    return bool(fwd), bool(bwd)


def motor_rule(tags, mode):
    """Valhalla 3.9.1 access of a way for Car, Truck, Motorcycle or Bus: None, or (forward, backward, destination
    only). Same source as bike_rule; the HOV-only-lane rule is left out."""
    hw = tags.get("highway")
    if hw in V_NOT_IN_GRAPH or tags.get("area") == "yes":
        return None
    fwd = (V_HIGHWAY_BUS if mode == "Bus" else V_HIGHWAY_MOTOR).get(hw)
    if fwd is None:
        return None
    access = any_in(V_ACCESS, tags.get("access"))
    vehicle = any_in(V_VEHICLE, tags.get("vehicle"))
    mv = first(any_in(V_VEHICLE, tags.get("motor_vehicle")), vehicle)
    if (tags.get("impassable") == "yes" or access is False or tags.get("smoothness") == "impassable"
            or (tags.get("access") == "private"
                and (tags.get("emergency") == "yes" or tags.get("service") == "emergency_access"))):
        fwd = False
    if mode == "Car":
        tag = first(any_in(V_VEHICLE, tags.get("motorcar")), mv)
    elif mode == "Truck":
        tag = first(any_in(V_TRUCK, tags.get("hgv")), mv)
    elif mode == "Motorcycle":
        tag = first(any_in(V_VEHICLE, tags.get("motorcycle")), mv)
    else:
        tag = first(any_in(V_BUS, tags.get("bus")), any_in(V_PSV, tags.get("psv")),
                    V_PSV.get(tags.get("lanes:psv:forward")), mv)
    fwd = first(tag, fwd)
    if mode == "Bus" and tags.get("access") == "psv":
        fwd = True
    ow_raw = tags.get("oneway")
    if tags.get("access") in ("permissive", "hov", "taxi") and ow_raw == "reversible":
        if mode != "Bus" or not fwd:     # only buses keep such a way, when they may use it
            return None
    if tags.get("service") == "driveway" and "access" not in tags:
        fwd = True
    bwd = False
    own = {"Bus": tags.get("oneway:bus") or tags.get("oneway:psv"),
           "Motorcycle": tags.get("oneway:motorcycle")}.get(mode)
    if mode == "Bus":
        bwd = ((ow_raw == "yes" and own == "no") or tags.get("bus:backward") in ("yes", "designated")
               or any(tags.get(k) in ("opposite", "opposite_lane") for k in ("busway", "busway:left", "busway:right"))
               or V_PSV.get(tags.get("lanes:psv:backward")) is True)
    elif mode == "Motorcycle":
        bwd = (ow_raw == "yes" and own == "no") or tags.get("motorcycle:backward") == "yes"
    own_oneway = V_ONEWAY.get(own) if bwd else None
    if mode == "Bus" and bwd and own_oneway is False and tags.get("bus:backward") == "yes":
        own_oneway = True
    oneway = V_ONEWAY.get(ow_raw)
    if tags.get("junction") in ("roundabout", "circular"):
        oneway = True
    if oneway is True:
        if mode in ("Car", "Truck"):
            bwd = False
        elif bwd and own_oneway is True:
            fwd = False
        elif bwd and own_oneway is False:
            fwd = True
    elif mode in ("Car", "Truck"):
        bwd = fwd
    elif not bwd and (own is None or (mode == "Motorcycle" and own == "no")) and own != "-1":
        bwd = fwd
    if mode == "Bus" and (tags.get("busway") == "lane"
                          or (tags.get("busway:left") == "lane" and tags.get("busway:right") == "lane")):
        fwd = bwd = True
    mv_fwd = tags.get("motor_vehicle:forward") or tags.get("vehicle:forward")
    if mv_fwd is not None:
        fwd = any_in(V_VEHICLE, mv_fwd)
    mv_bwd = tags.get("motor_vehicle:backward") or tags.get("vehicle:backward")
    if mv_bwd is not None:
        bwd = any_in(V_VEHICLE, mv_bwd)
    if ow_raw == "-1":
        fwd, bwd = bwd, fwd
    if own == "-1":
        fwd, bwd = bwd, fwd
    if mode == "Bus" and tags.get("lanes:bus") in ("1", "2"):
        fwd, bwd = True, tags.get("lanes:bus") == "2"
    if not (fwd or bwd):
        return None
    keys = ("access", "motor_vehicle", "motorcar", "vehicle") + (("hgv",) if mode == "Truck" else ())
    dest_only = (tags.get("service") in V_DEST_ONLY_SERVICE
                 or any(any_in(dict.fromkeys(V_PRIVATE, True), tags.get(k)) for k in keys))
    return bool(fwd), bool(bwd), bool(dest_only)


def maxspeed(tags):
    m = re.match(r"\s*(\d+)", tags.get("maxspeed", ""))
    return float(m.group(1)) if m and int(m.group(1)) > 0 else CAR_SPEED_KMH.get(tags["highway"], 30)


def is_private(tags, keys, own):
    """A private way for this transport: 'private' in one of its access keys, and not opened by its own tag."""
    return any("private" in (tags.get(k) or "").split(";") for k in keys) and tags.get(own) not in ALLOWED


def rule(tags, mode, bike_kmh, walk_kmh, private=False):
    """None when the way is closed to this transport, else (forward, backward, km/h, may start here,
    cost factor, destination only). The cost (what the route search minimises) is the travel time times the
    cost factor, plus DEST_ONLY_PENALTY_S on entering a destination-only way; the isochrone shows the time.
    private=False (the default): private ways stay closed, our one departure from Valhalla.
    private=True: as Valhalla, they are open as destination-only ways (reached, not used as a short cut)."""
    hw = tags.get("highway")
    if tags.get("area") == "yes":
        return None
    # Valhalla (pbfgraphparser.cc) drops a closed way (first node = last node) with a building, landuse,
    # leisure or natural key, for every transport: a running track, an ice rink. The caller sets "_closed".
    if tags.get("_closed") and any(k in tags for k in ("building", "landuse", "leisure", "natural")):
        return None
    if mode == "Walking":
        if not private and is_private(tags, ("access", "foot"), "foot"):
            return None
        r = foot_rule(tags)
        if r is None:
            return None
        # Valhalla's destination-only flag is one per edge, the same for every transport.
        dest_only = (tags.get("service") in V_DEST_ONLY_SERVICE
                     or any(any_in(dict.fromkeys(V_PRIVATE, True), tags.get(k))
                            for k in ("access", "motor_vehicle", "motorcar", "vehicle")))
        return r[0], r[1], walk_kmh, True, 1.0, dest_only
    if mode == "Cycling":
        if not private and is_private(tags, ("access", "bicycle"), "bicycle"):
            return None
        r = bike_rule(tags)
        if r is None:
            return None
        fwd, bwd, steps, dismount, dest_only = r
        if steps:      # not a start: Valhalla does not snap a bicycle to steps
            return fwd, bwd, STEPS_KMH, False, STEPS_COST_FACTOR, dest_only
        return fwd, bwd, DISMOUNT_KMH if dismount else bike_kmh, True, 1.0, dest_only
    # Car, Truck, Motorcycle, Bus
    if not private and is_private(tags, ("access", "vehicle", "motor_vehicle", MOTOR_TAG[mode]), MOTOR_TAG[mode]):
        return None
    r = motor_rule(tags, mode)
    if r is None:
        return None
    kmh = min(maxspeed(tags), MAX_KMH.get(mode, 999))
    return r[0], r[1], kmh, hw not in NO_CAR_START, 1.0, r[2]


class IsochronesFromPoints(QgsProcessingAlgorithm):
    POINTS, NAME_FIELD, MODE, MEASURE, VALUES, MERGE, AREAS, OSM = (
        "POINTS", "NAME_FIELD", "MODE", "MEASURE", "VALUES", "MERGE", "AREAS", "OSM")
    SPEED, CELL, BUFFER, SNAP, OUTPUT, EXPANSION, PRIVATE = (
        "SPEED", "CELL", "BUFFER", "SNAP", "OUTPUT", "EXPANSION", "PRIVATE")

    def tr(self, text):
        return QCoreApplication.translate("IsochronesFromPoints", text)

    def createInstance(self):
        return IsochronesFromPoints()

    def name(self):
        return "isochronen_osm"

    def displayName(self):
        return self.tr("Isochrones from points (OSM)")

    def group(self):
        return self.tr("Bereikbaarheid")

    def groupId(self):
        return "bereikbaarheid"

    def shortHelpString(self):
        return self.tr(
            "Isochrones around your own points, over OpenStreetMap, on foot, by bicycle, car, truck or "
            "motorcycle, as time (minutes) or distance (metres). Which roads each may use, and in which "
            "direction, follows the route planner Valhalla 3.9.1.\n\n"
            "Values: comma separated, e.g. 5, 10, 15.\n"
            "Merge points: one isochrone per value for all points together.\n"
            "Enter private roads: off (default), private roads stay closed. On, they are open as in "
            "Valhalla: reached as a destination, but 600 s is added to the cost of entering one, so a "
            "route hardly ever uses one as a short cut.\n"
            "OSM download area: one or more provinces, or the whole Netherlands, downloaded once "
            f"from Geofabrik to {CACHE_DIR}.\n"
            "* = borders Zuid-Holland. For points near the border of Zuid-Holland, tick that "
            "neighbour too: otherwise the network, and so the isochrone, stops at the border.\n"
            "OSM file: your own .osm.pbf instead of a download.\n\n"
            "The output is in RD New (EPSG:28992), one feature per point per value. "
            "The full explanation is at the top of the script file.")

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.POINTS, self.tr("Start points"), [QgsProcessing.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterField(
            self.NAME_FIELD, self.tr("Point name field (copied to the result)"),
            parentLayerParameterName=self.POINTS, optional=True))
        self.addParameter(QgsProcessingParameterEnum(self.MODE, self.tr("Transport"), MODES, defaultValue=0))
        self.addParameter(QgsProcessingParameterEnum(self.MEASURE, self.tr("Measure"), MEASURES, defaultValue=0))
        self.addParameter(QgsProcessingParameterString(
            self.VALUES, self.tr("Values, comma separated (minutes or metres)"), defaultValue="5, 10, 15"))
        self.addParameter(QgsProcessingParameterBoolean(
            self.MERGE, self.tr("Merge points (one isochrone per value for all points)"), defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean(
            self.PRIVATE, self.tr("Enter private roads (access=private), as Valhalla: reached, but not used "
                                  "as a short cut"), defaultValue=False))
        self.addParameter(QgsProcessingParameterEnum(
            self.AREAS, self.tr("OSM download area (* = borders Zuid-Holland: tick it too for points "
                                "near that border)"),
            [label for label, _ in OSM_AREAS], allowMultiple=True, defaultValue=[0]))
        self.addParameter(QgsProcessingParameterFile(
            self.OSM, self.tr("OSM file (.osm.pbf), instead of the download area"),
            extension="pbf", optional=True))
        for name, label, default in (
                (self.SPEED, "Speed for walking or cycling, km/h (0 = 5.1 walking, 15 cycling)", 0),
                (self.CELL, "Cell size, m (0 = 10 walking, 20 cycling, 50 car)", 0),
                (self.BUFFER, "Buffer around reached ways, m (0 = 30 walking, 40 cycling, 100 car)", 0),
                (self.SNAP, "Snap distance from a point to the network, m", 250)):
            p = QgsProcessingParameterNumber(name, self.tr(label), QgsProcessingParameterNumber.Double,
                                             defaultValue=default, minValue=0)
            p.setFlags(p.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
            self.addParameter(p)
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Isochrones"), QgsProcessing.TypeVectorPolygon))
        p = QgsProcessingParameterBoolean(
            self.EXPANSION, self.tr("Expansion: add every street reached, with its cost, as a temporary "
                                    "layer (like Valhalla)"), defaultValue=False)
        p.setFlags(p.flags() | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(p)
        self.addOutput(QgsProcessingOutputVectorLayer(self.EXPANSION + "_LAYER", self.tr("Expansion"),
                                                      QgsProcessing.TypeVectorLine))

    # -----------------------------------------------------------------------
    def processAlgorithm(self, parameters, context, feedback):
        import numpy as np
        from osgeo import gdal, ogr
        gdal.UseExceptions()
        ogr.UseExceptions()
        t0 = time.time()

        source = self.parameterAsSource(parameters, self.POINTS, context)
        name_field = self.parameterAsString(parameters, self.NAME_FIELD, context)
        mode = MODES[self.parameterAsEnum(parameters, self.MODE, context)]
        by_time = self.parameterAsEnum(parameters, self.MEASURE, context) == 0
        merge = self.parameterAsBool(parameters, self.MERGE, context)
        private = self.parameterAsBool(parameters, self.PRIVATE, context)
        osm_file = self.parameterAsFile(parameters, self.OSM, context)
        areas = [OSM_AREAS[i] for i in self.parameterAsEnums(parameters, self.AREAS, context)]
        speed = self.parameterAsDouble(parameters, self.SPEED, context)
        cell = self.parameterAsDouble(parameters, self.CELL, context) or CELL_M[mode]
        buffer_m = self.parameterAsDouble(parameters, self.BUFFER, context) or BUFFER_M[mode]
        snap_m = self.parameterAsDouble(parameters, self.SNAP, context)
        try:
            values = sorted({float(v) for v in self.parameterAsString(parameters, self.VALUES, context)
                             .replace(";", ",").split(",") if v.strip()})
        except ValueError:
            raise QgsProcessingException("Values must be numbers separated by commas, e.g. 5, 10, 15")
        if not values or values[0] <= 0:
            raise QgsProcessingException("Give at least one value above 0, e.g. 5, 10, 15")
        unit = "min" if by_time else "m"
        walk_kmh = speed if (speed and mode == "Walking") else WALK_SPEED_KMH
        bike_kmh = speed if (speed and mode == "Cycling") else BIKE_SPEED_KMH
        feedback.pushInfo(f"Transport: {mode}; measure: {'time' if by_time else 'distance'}; "
                          f"values: {', '.join(f'{v:g}' for v in values)} {unit}; "
                          f"{'merged' if merge else 'per point'}; cell {cell:g} m, buffer {buffer_m:g} m; "
                          f"private roads {'open' if private else 'closed'}")
        if mode in ("Walking", "Cycling"):
            feedback.pushInfo(f"Speed: {walk_kmh if mode == 'Walking' else bike_kmh:g} km/h")

        # 1. The points, in RD New
        rd = QgsCoordinateReferenceSystem("EPSG:28992")
        ct = QgsCoordinateTransform(source.sourceCrs(), rd, context.transformContext())
        points = []   # (point_id, name, x, y)
        for f in source.getFeatures():
            g = f.geometry()
            if g.isNull() or g.isEmpty():
                continue
            g.transform(ct)
            p = g.centroid().asPoint()
            points.append((f.id(), str(f[name_field]) if name_field else "", p.x(), p.y()))
        if not points:
            raise QgsProcessingException("The point layer has no points (or none selected)")
        feedback.pushInfo(f"{len(points):,} start points")

        # How far the network must reach from the points
        top_kmh = {"Walking": walk_kmh, "Cycling": bike_kmh}.get(mode, max(CAR_SPEED_KMH.values()) + 30)
        reach = values[-1] / 60 * top_kmh * 1000 if by_time else values[-1]
        margin = reach + buffer_m + snap_m + 500
        xs, ys = [p[2] for p in points], [p[3] for p in points]
        box = (min(xs) - margin, max(xs) + margin, min(ys) - margin, max(ys) + margin)

        # 2. OSM
        if osm_file:
            pbfs = [osm_file]
        else:
            if not areas:
                raise QgsProcessingException("Tick at least one OSM download area, or choose an OSM file")
            if any(path == "netherlands" for _, path in areas) and len(areas) > 1:
                feedback.pushInfo("OSM: the whole Netherlands is ticked; the provinces are already in it")
                areas = [a for a in areas if a[1] == "netherlands"]
            pbfs = [self.download(label, path, feedback) for label, path in areas]
        caches = [self.osm_cache(pbf, feedback) for pbf in pbfs]
        if feedback.isCanceled():
            return {}

        # 3. Network
        feedback.pushInfo(f"Reading the {mode.lower()} network within {margin / 1000:.1f} km of the points ...")
        x, y, u, v, m, c, start_ok, seg_a, seg_b = self.network(caches, box, mode, by_time, cell, walk_kmh,
                                                                 bike_kmh, private, feedback)
        n = len(x)
        from scipy.sparse import csr_matrix
        from scipy.sparse.csgraph import dijkstra
        from scipy.spatial import cKDTree
        graph = csr_matrix((c, (u, v)), shape=(n, n))
        # Where cost and measure differ (cycling: steps, destination-only ways), search on the cost, as
        # Valhalla does, and report the measure along the cheapest route.
        two_weights = not np.allclose(c, m)
        measure = csr_matrix((m, (u, v)), shape=(n, n)) if two_weights else None

        # 4. Snap the points
        starts = np.flatnonzero(start_ok)
        d, idx = cKDTree(np.column_stack([x[starts], y[starts]])).query(
            np.array([(p[2], p[3]) for p in points]), distance_upper_bound=snap_m)
        snapped = [(p, int(starts[i])) for p, dd, i in zip(points, d, idx) if np.isfinite(dd)]
        if len(snapped) < len(points):
            feedback.reportError(f"{len(points) - len(snapped):,} points lie further than {snap_m:g} m from the "
                                 f"{mode.lower()} network and are skipped (outside the OSM file?)", False)
        if not snapped:
            raise QgsProcessingException("No point lies near the network. Is the OSM file the right area?")

        # 5. Output
        fields = QgsFields()
        for fname, ftype in (("point_id", INT), ("point_name", TEXT), ("mode", TEXT), ("measure", TEXT),
                             ("value", REAL), ("unit", TEXT), ("km2", REAL)):
            fields.append(QgsField(fname, ftype))
        sink, dest = self.parameterAsSink(parameters, self.OUTPUT, context, fields, QgsWkbTypes.MultiPolygon, rd)
        if sink is None:
            raise QgsProcessingException("Cannot create the output layer")
        exp_layer = exp_sink = None
        if self.parameterAsBool(parameters, self.EXPANSION, context):
            exp_fields = QgsFields()
            for fname, ftype in (("point_id", INT), ("point_name", TEXT), ("mode", TEXT), ("measure", TEXT),
                                 ("unit", TEXT), ("cost_from", REAL), ("cost_to", REAL), ("order", INT)):
                exp_fields.append(QgsField(fname, ftype))
            exp_name = f"Expansion {mode.lower()} {values[-1]:g} {unit}"
            exp_layer = QgsVectorLayer("LineString?crs=EPSG:28992", exp_name, "memory")
            exp_layer.dataProvider().addAttributes(exp_fields.toList())
            exp_layer.updateFields()
            exp_sink = exp_layer.dataProvider()

        groups = [(None, "all points", [s for _, s in snapped])] if merge else \
            [(p[0], p[1], [s]) for p, s in snapped]
        for k, (pid, pname, nodes) in enumerate(groups):
            if feedback.isCanceled():
                break
            if two_weights:
                # No limit: a route over steps or into a destination-only way costs more than it takes
                # time; the network is already cut to the reach of the points.
                cost, pred, _ = dijkstra(graph, directed=True, indices=nodes, min_only=True,
                                         return_predecessors=True)
                dist = self.along_route(cost, pred, measure)
                dist[dist > values[-1]] = np.inf
            else:
                dist = dijkstra(graph, directed=True, indices=nodes, min_only=True, limit=values[-1])
            for value, geom, km2 in self.polygons(x, y, dist, values, cell, buffer_m):
                f = QgsFeature(fields)
                f.setAttributes([pid, pname, mode, "time" if by_time else "distance", value, unit, round(km2, 4)])
                f.setGeometry(geom)
                sink.addFeature(f, QgsFeatureSink.FastInsert)
            if exp_sink is not None:
                self.expansion(exp_sink, exp_fields, x, y, seg_a, seg_b, dist,
                               [pid, pname, mode, "time" if by_time else "distance", unit])
            feedback.setProgress(100 * (k + 1) / len(groups))
        feedback.pushInfo(f"Done in {time.time() - t0:.0f} s")
        result = {self.OUTPUT: dest}
        if exp_layer is not None:   # a temporary layer, put in the map when the tool finishes
            exp_layer.updateExtents()
            feedback.pushInfo(f"Expansion: {exp_layer.featureCount():,} street segments in the temporary "
                              f"layer '{exp_layer.name()}'")
            context.temporaryLayerStore().addMapLayer(exp_layer)
            context.addLayerToLoadOnCompletion(exp_layer.id(), QgsProcessingContext.LayerDetails(
                exp_layer.name(), context.project(), self.EXPANSION + "_LAYER"))
            result[self.EXPANSION + "_LAYER"] = exp_layer.id()
        return result

    # -----------------------------------------------------------------------
    def download(self, label, area, feedback):
        """The Geofabrik extract of one area, downloaded once to CACHE_DIR."""
        os.makedirs(CACHE_DIR, exist_ok=True)
        url = f"{GEOFABRIK}{area}-latest.osm.pbf"
        path = os.path.join(CACHE_DIR, os.path.basename(url))
        if os.path.exists(path):
            age = (time.time() - os.path.getmtime(path)) / 86400
            feedback.pushInfo(f"OSM: using {path} (downloaded {age:.0f} days ago; delete it to fetch a new one)")
            return path
        feedback.pushInfo(f"OSM: downloading {label}, once: {url} to {path} ...")

        def progress(blocks, size, total):
            if total > 0:
                feedback.setProgress(min(100, blocks * size * 100 / total))

        try:
            urllib.request.urlretrieve(url, path + ".part", progress)
        except Exception as e:  # noqa: BLE001
            raise QgsProcessingException(f"Download failed ({e}). Download {url} in a browser and "
                                         f"choose it as OSM file.")
        os.replace(path + ".part", path)
        return path

    def osm_cache(self, pbf, feedback):
        """The ways of the .pbf as a GeoPackage with a spatial index (made once per .pbf)."""
        from osgeo import gdal
        cache = os.path.splitext(os.path.splitext(pbf)[0])[0] + "_ways.gpkg"
        if os.path.exists(cache) and os.path.getmtime(cache) > os.path.getmtime(pbf):
            return cache
        feedback.pushInfo(f"OSM: reading the ways of {os.path.basename(pbf)} into {cache} "
                          f"(once, about a minute per 200 MB) ...")
        if os.path.exists(cache):
            os.remove(cache)
        gdal.VectorTranslate(cache, pbf, format="GPKG", layers=["lines"], layerName="ways",
                             where="highway IS NOT NULL")
        return cache

    def network(self, caches, box, mode, by_time, cell, walk_kmh, bike_kmh, private, feedback):
        """Network points (x, y), directed pieces (u, v, measure, cost) and which points may be a start.
        measure = minutes or metres; cost = measure times the way's cost factor, plus the destination-only
        penalty (in the same unit) on a piece that enters a destination-only way from another way.
        Ways meet only at shared OSM nodes; every piece is at most `cell` long. A way that is
        in two extracts (along a shared border) is read twice; its pieces count once."""
        import numpy as np
        from osgeo import ogr, osr
        rd, wgs = osr.SpatialReference(), osr.SpatialReference()
        rd.ImportFromEPSG(28992)
        wgs.ImportFromEPSG(4326)
        for s in (rd, wgs):
            s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        corners = osr.CoordinateTransformation(rd, wgs).TransformPoints(
            [(box[0], box[2]), (box[1], box[2]), (box[0], box[3]), (box[1], box[3])])
        chunks, attrs = [], []
        for cache in caches:
            ds = ogr.Open(cache)
            lyr = ds.GetLayerByName("ways")
            lyr.SetSpatialFilterRect(min(c[0] for c in corners), min(c[1] for c in corners),
                                     max(c[0] for c in corners), max(c[1] for c in corners))
            for f in lyr:
                g = f.GetGeometryRef()
                tags = tags_of(f)
                if g.GetPointCount() > 2 and g.GetPoint_2D(0) == g.GetPoint_2D(g.GetPointCount() - 1):
                    tags["_closed"] = "yes"
                r = rule(tags, mode, bike_kmh, walk_kmh, private)
                if r is None:
                    continue
                pts = np.asarray(g.GetPoints(), dtype=float)[:, :2]
                if len(pts) >= 2:
                    chunks.append(pts)
                    attrs.append(r)
        if not chunks:
            raise QgsProcessingException("No ways for this transport near the points. Is the OSM file the right area?")
        lengths = np.array([len(c) for c in chunks])
        ll = np.concatenate(chunks)
        way = np.repeat(np.arange(len(chunks)), lengths)
        fwd, bwd, kmh, start, factor, dest_only = (np.array(a) for a in zip(*attrs))
        last = np.zeros(len(ll), dtype=bool)
        last[np.cumsum(lengths) - 1] = True

        # One node per OSM node: the same coordinates (1e-7 degree, as OSM stores them) = the same node.
        key = (np.round(ll[:, 0] * 1e7).astype(np.int64) << 31) | np.round(ll[:, 1] * 1e7).astype(np.int64)
        _, first, inv = np.unique(key, return_index=True, return_inverse=True)
        inv = inv.ravel()
        to_rd = osr.CoordinateTransformation(wgs, rd)
        xy = np.empty((len(first), 2))
        for s in range(0, len(first), 500_000):
            block = ll[first[s:s + 500_000]]
            xy[s:s + len(block)] = np.asarray(to_rd.TransformPoints(block.tolist()))[:, :2]

        keep = ~last[:-1]
        a, b, sw = inv[:-1][keep], inv[1:][keep], way[:-1][keep]
        ok = a != b
        a, b, sw = a[ok], b[ok], sw[ok]
        seg_len = np.hypot(xy[b, 0] - xy[a, 0], xy[b, 1] - xy[a, 1])
        feedback.pushInfo(f"  {len(chunks):,} ways, {seg_len.sum() / 1000:,.0f} km")

        # Cut every segment into k pieces of at most `cell` metres.
        k = np.maximum(np.ceil(seg_len / cell).astype(np.int64), 1)
        seg = np.repeat(np.arange(len(a)), k)
        j = np.arange(k.sum()) - np.repeat(np.cumsum(k) - k, k)
        inner = j > 0
        new_id = np.full(len(j), -1, dtype=np.int64)
        new_id[inner] = len(xy) + np.arange(inner.sum())
        pu = np.where(inner, new_id, a[seg])
        end = j == k[seg] - 1
        pv = np.roll(pu, -1)
        pv[end] = b[seg[end]]
        t = j[inner] / k[seg[inner]]
        sa, sb = a[seg[inner]], b[seg[inner]]
        x = np.concatenate([xy[:, 0], xy[sa, 0] + t * (xy[sb, 0] - xy[sa, 0])])
        y = np.concatenate([xy[:, 1], xy[sa, 1] + t * (xy[sb, 1] - xy[sa, 1])])
        piece_len = seg_len[seg] / k[seg]
        pway = sw[seg]
        meas = piece_len / (kmh[pway] * 1000 / 60) if by_time else piece_len
        meas = np.maximum(meas, 1e-6)

        f_, b_ = fwd[pway], bwd[pway]
        u = np.concatenate([pu[f_], pv[b_]])
        v = np.concatenate([pv[f_], pu[b_]])
        m = np.concatenate([meas[f_], meas[b_]])
        w = np.concatenate([pway[f_], pway[b_]])
        c = m * factor[w]
        if dest_only.any():
            # Entering a destination-only way: from a point that an ordinary way also touches.
            public = np.zeros(len(x), dtype=bool)
            public[u[~dest_only[w]]] = True
            public[v[~dest_only[w]]] = True
            # 600 s, in minutes, or in metres at the way's own speed
            penalty = DEST_ONLY_PENALTY_S / 60 if by_time else DEST_ONLY_PENALTY_S * kmh[w] / 3.6
            c = c + np.where(dest_only[w] & public[u], penalty, 0.0)
        # Two ways over the same nodes: keep the cheaper (a sparse matrix would add them up).
        order = np.lexsort((c, u * len(x) + v))
        _, firsts = np.unique((u * len(x) + v)[order], return_index=True)
        pick = order[firsts]
        start_ok = np.zeros(len(x), dtype=bool)
        start_ok[pu[start[pway]]] = True
        start_ok[pv[start[pway]]] = True
        feedback.pushInfo(f"  {len(x):,} network points, {len(pick):,} directed pieces")
        pairs = np.unique(np.minimum(a, b) * len(xy) + np.maximum(a, b))   # each OSM segment once
        return x, y, u[pick], v[pick], m[pick], c[pick], start_ok, pairs // len(xy), pairs % len(xy)

    @staticmethod
    def along_route(cost, pred, measure):
        """The measure (time or length) from the start along the cheapest route to every reached point.
        Sums the predecessor tree by pointer doubling: log2(route length) numpy steps."""
        import numpy as np
        reached = np.isfinite(cost)
        p = np.where(reached, pred, -1)
        has = p >= 0
        total = np.zeros(len(cost))
        total[has] = np.asarray(measure[p[has], np.flatnonzero(has)]).ravel()
        while has.any():   # total[v] = measure over 2^k steps up the tree, p[v] = the point 2^k steps up
            total_next, p_next = total.copy(), p.copy()
            total_next[has] += total[p[has]]
            p_next[has] = p[p[has]]
            total, p = total_next, p_next
            has = p >= 0
        total[~reached] = np.inf
        return total

    def expansion(self, sink, fields, x, y, seg_a, seg_b, dist, attrs):
        """Every OSM segment the search reached: the cost at both ends and the order reached."""
        import numpy as np
        da, db = dist[seg_a], dist[seg_b]
        hit = np.flatnonzero(np.isfinite(da) | np.isfinite(db))
        first, last = np.fmin(da[hit], db[hit]), np.fmax(da[hit], db[hit])
        order = np.empty(len(hit), dtype=np.int64)
        order[np.argsort(first, kind="stable")] = np.arange(1, len(hit) + 1)
        for i, s_, c0, c1, o in zip(hit, seg_a[hit], first, last, order):
            e = seg_b[i]
            f = QgsFeature(fields)
            f.setAttributes(attrs + [round(float(c0), 3), round(float(c1), 3) if np.isfinite(c1) else None, int(o)])
            f.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(x[s_], y[s_]), QgsPointXY(x[e], y[e])]))
            sink.addFeature(f, QgsFeatureSink.FastInsert)

    def polygons(self, x, y, dist, values, cell, buffer_m):
        """[(value, QgsGeometry, km2)]: the land within buffer_m of a network point reached within each value."""
        import numpy as np
        from osgeo import gdal, ogr
        from scipy.ndimage import minimum_filter
        reached = np.isfinite(dist)
        if not reached.any():
            return []
        rx, ry, rd_ = x[reached], y[reached], dist[reached].astype(np.float32)
        pad = buffer_m + 2 * cell
        x0 = math.floor((rx.min() - pad) / cell) * cell
        y1 = math.ceil((ry.max() + pad) / cell) * cell
        cols = int(math.ceil((rx.max() + pad - x0) / cell))
        rows = int(math.ceil((y1 - (ry.min() - pad)) / cell))
        grid = np.full((rows, cols), np.inf, dtype=np.float32)
        np.minimum.at(grid, (((y1 - ry) // cell).astype(np.int64), ((rx - x0) // cell).astype(np.int64)), rd_)
        r = int(buffer_m // cell)
        if r > 0:
            yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
            grid = minimum_filter(grid, footprint=(xx * xx + yy * yy) <= r * r, mode="constant", cval=np.inf)
        out = []
        for value in values:
            mask = (grid <= value).astype(np.uint8)
            if not mask.any():
                continue
            mem = gdal.GetDriverByName("MEM").Create("", cols, rows, 1, gdal.GDT_Byte)
            mem.SetGeoTransform((x0, cell, 0, y1, 0, -cell))
            band = mem.GetRasterBand(1)
            band.WriteArray(mask)
            tmp_ds = ogr.GetDriverByName("MEM").CreateDataSource("")
            tmp = tmp_ds.CreateLayer("p", None, ogr.wkbPolygon)
            tmp.CreateField(ogr.FieldDefn("v", ogr.OFTInteger))
            gdal.Polygonize(band, band, tmp, 0)
            multi = ogr.Geometry(ogr.wkbMultiPolygon)
            for f in tmp:
                multi.AddGeometry(f.GetGeometryRef())
            geom = QgsGeometry()
            geom.fromWkb(bytes(multi.ExportToWkb()))
            out.append((value, geom, float(mask.sum()) * cell * cell / 1e6))
        return out
