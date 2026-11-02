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
  OSM file           optional: an .osm.pbf. Empty = the Geofabrik extract of
                     Zuid-Holland, downloaded once (about 210 MB) to
                     %LOCALAPPDATA%\isochronen_osm.
  Advanced           speeds, cell size, buffer, snap distance (see below).

WHAT YOU GET
  A polygon layer in RD New (EPSG:28992), one feature per point (or for all
  points) per value: point_id, point_name, mode, measure, value, unit, km2.
  Style it by "value" (Symbology > Categorized) and put the smallest on top.

HOW IT WORKS
  1. Reads the OSM ways once into a GeoPackage next to the .pbf (about a
     minute), then only the part within reach of your points.
  2. Builds the network for the chosen transport:
       walking  footways, paths, cycle paths, streets and roads; no motorways
                or trunk roads; no ways tagged foot=no or access=private/no
                (unless foot= allows it). Both directions.
       cycling  cycle paths, paths, tracks, streets and roads; footways only
                with bicycle=yes/designated; no motorways or trunk roads, no
                bicycle=no. One-way streets count one way unless
                oneway:bicycle=no.
       car      roads from motorway to service road; no access=private/no
                unless motor_vehicle= allows it; one-way streets and
                roundabouts one way. Speed = the maxspeed tag, or the default
                per road type in CAR_SPEED_KMH below.
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

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature, QgsFeatureSink, QgsField,
    QgsFields, QgsGeometry, QgsProcessing, QgsProcessingAlgorithm, QgsProcessingException,
    QgsProcessingParameterBoolean, QgsProcessingParameterDefinition, QgsProcessingParameterEnum,
    QgsProcessingParameterFeatureSink, QgsProcessingParameterFeatureSource, QgsProcessingParameterField,
    QgsProcessingParameterFile, QgsProcessingParameterNumber, QgsProcessingParameterString,
    QgsWkbTypes,
)

# ===========================================================================
# SETTINGS
# ===========================================================================
OSM_URL = "https://download.geofabrik.de/europe/netherlands/zuid-holland-latest.osm.pbf"
CACHE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "isochronen_osm")

MODES = ["Walking", "Cycling", "Car"]
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
CELL_M = {"Walking": 10, "Cycling": 20, "Car": 50}       # raster cell and network piece length
BUFFER_M = {"Walking": 30, "Cycling": 40, "Car": 100}    # land this close to a reached way is inside

WALK_HIGHWAYS = (
    "footway", "path", "pedestrian", "living_street", "steps", "track",
    "residential", "service", "unclassified", "road", "cycleway",
    "tertiary", "tertiary_link", "secondary", "secondary_link",
    "primary", "primary_link",
)
BIKE_HIGHWAYS = (
    "cycleway", "path", "track", "living_street", "residential", "service", "unclassified", "road",
    "tertiary", "tertiary_link", "secondary", "secondary_link", "primary", "primary_link",
)
BIKE_ONLY_IF_ALLOWED = ("footway", "pedestrian", "steps")
ALLOWED = ("yes", "designated", "permissive", "official", "destination")
CLOSED = ("no", "private")
NO_CAR_START = ("motorway", "motorway_link", "trunk", "trunk_link")
# ===========================================================================

TAG = re.compile(r'"([^"]+)"=>"((?:[^"\\]|\\.)*)"')


def tags_of(feature):
    """The OSM tags of a GDAL OSM feature: its own columns plus other_tags."""
    tags = dict(TAG.findall(feature.GetField("other_tags") or ""))
    for i in range(feature.GetFieldCount()):
        name = feature.GetFieldDefnRef(i).GetName()
        if name not in ("other_tags", "osm_id") and feature.IsFieldSet(i):
            tags[name] = feature.GetField(i)
    return tags


def oneway_of(tags, mode):
    """(forward, backward) allowed for this way."""
    if mode == "Walking":
        return True, True
    ow = tags.get("oneway", "")
    if mode == "Cycling" and tags.get("oneway:bicycle") == "no":
        return True, True
    if ow in ("yes", "1", "true"):
        return True, False
    if ow == "-1":
        return False, True
    if mode == "Car" and ow != "no" and (tags.get("highway") in ("motorway", "motorway_link")
                                         or tags.get("junction") in ("roundabout", "circular")):
        return True, False
    return True, True


def maxspeed(tags):
    m = re.match(r"\s*(\d+)", tags.get("maxspeed", ""))
    return float(m.group(1)) if m and int(m.group(1)) > 0 else CAR_SPEED_KMH.get(tags["highway"], 30)


def rule(tags, mode, bike_kmh, walk_kmh):
    """None when the way is closed to this transport, else (forward, backward, km/h, may start here)."""
    hw = tags.get("highway")
    if tags.get("area") == "yes":
        return None
    access = tags.get("access")
    if mode == "Walking":
        foot = tags.get("foot")
        if hw not in WALK_HIGHWAYS or foot == "no" or (access in CLOSED and foot not in ALLOWED):
            return None
        return True, True, walk_kmh, True
    if mode == "Cycling":
        bike = tags.get("bicycle")
        if bike == "no" or (access in CLOSED and bike not in ALLOWED):
            return None
        if hw not in BIKE_HIGHWAYS and not (hw in BIKE_ONLY_IF_ALLOWED and bike in ALLOWED):
            return None
        return (*oneway_of(tags, mode), bike_kmh, True)
    motor = tags.get("motor_vehicle") or tags.get("motorcar")
    if hw not in CAR_SPEED_KMH or motor in CLOSED or (access in CLOSED and motor not in ALLOWED):
        return None
    return (*oneway_of(tags, mode), maxspeed(tags), hw not in NO_CAR_START)


class IsochronesFromPoints(QgsProcessingAlgorithm):
    POINTS, NAME_FIELD, MODE, MEASURE, VALUES, MERGE, OSM = (
        "POINTS", "NAME_FIELD", "MODE", "MEASURE", "VALUES", "MERGE", "OSM")
    SPEED, CELL, BUFFER, SNAP, OUTPUT = "SPEED", "CELL", "BUFFER", "SNAP", "OUTPUT"

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
            "Isochrones around your own points, over OpenStreetMap, by walking, cycling or car, "
            "as time (minutes) or distance (metres).\n\n"
            "Values: comma separated, e.g. 5, 10, 15.\n"
            "Merge points: one isochrone per value for all points together.\n"
            "OSM file: empty = the Geofabrik extract of Zuid-Holland, downloaded once to "
            f"{CACHE_DIR}.\n\n"
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
        self.addParameter(QgsProcessingParameterFile(
            self.OSM, self.tr("OSM file (.osm.pbf); empty = download Zuid-Holland from Geofabrik"),
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
        osm_file = self.parameterAsFile(parameters, self.OSM, context)
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
                          f"{'merged' if merge else 'per point'}; cell {cell:g} m, buffer {buffer_m:g} m")
        if mode != "Car":
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
        top_kmh = {"Walking": walk_kmh, "Cycling": bike_kmh, "Car": max(CAR_SPEED_KMH.values()) + 30}[mode]
        reach = values[-1] / 60 * top_kmh * 1000 if by_time else values[-1]
        margin = reach + buffer_m + snap_m + 500
        xs, ys = [p[2] for p in points], [p[3] for p in points]
        box = (min(xs) - margin, max(xs) + margin, min(ys) - margin, max(ys) + margin)

        # 2. OSM
        pbf = osm_file or self.download(feedback)
        cache = self.osm_cache(pbf, feedback)
        if feedback.isCanceled():
            return {}

        # 3. Network
        feedback.pushInfo(f"Reading the {mode.lower()} network within {margin / 1000:.1f} km of the points ...")
        x, y, u, v, w, start_ok = self.network(cache, box, mode, by_time, cell, walk_kmh, bike_kmh, feedback)
        n = len(x)
        from scipy.sparse import csr_matrix
        from scipy.sparse.csgraph import dijkstra
        from scipy.spatial import cKDTree
        graph = csr_matrix((w, (u, v)), shape=(n, n))

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
        for fname, ftype in (("point_id", QVariant.LongLong), ("point_name", QVariant.String),
                             ("mode", QVariant.String), ("measure", QVariant.String),
                             ("value", QVariant.Double), ("unit", QVariant.String), ("km2", QVariant.Double)):
            fields.append(QgsField(fname, ftype))
        sink, dest = self.parameterAsSink(parameters, self.OUTPUT, context, fields, QgsWkbTypes.MultiPolygon, rd)
        if sink is None:
            raise QgsProcessingException("Cannot create the output layer")

        groups = [(None, "all points", [s for _, s in snapped])] if merge else \
            [(p[0], p[1], [s]) for p, s in snapped]
        for k, (pid, pname, nodes) in enumerate(groups):
            if feedback.isCanceled():
                break
            dist = dijkstra(graph, directed=True, indices=nodes, min_only=True, limit=values[-1])
            for value, geom, km2 in self.polygons(x, y, dist, values, cell, buffer_m):
                f = QgsFeature(fields)
                f.setAttributes([pid, pname, mode, "time" if by_time else "distance", value, unit, round(km2, 4)])
                f.setGeometry(geom)
                sink.addFeature(f, QgsFeatureSink.FastInsert)
            feedback.setProgress(100 * (k + 1) / len(groups))
        feedback.pushInfo(f"Done in {time.time() - t0:.0f} s")
        return {self.OUTPUT: dest}

    # -----------------------------------------------------------------------
    def download(self, feedback):
        os.makedirs(CACHE_DIR, exist_ok=True)
        path = os.path.join(CACHE_DIR, os.path.basename(OSM_URL))
        if os.path.exists(path):
            age = (time.time() - os.path.getmtime(path)) / 86400
            feedback.pushInfo(f"OSM: using {path} (downloaded {age:.0f} days ago; delete it to fetch a new one)")
            return path
        feedback.pushInfo(f"OSM: downloading {OSM_URL} (about 210 MB, once) to {path} ...")

        def progress(blocks, size, total):
            if total > 0:
                feedback.setProgress(min(100, blocks * size * 100 / total))

        try:
            urllib.request.urlretrieve(OSM_URL, path + ".part", progress)
        except Exception as e:  # noqa: BLE001
            raise QgsProcessingException(f"Download failed ({e}). Download {OSM_URL} in a browser and "
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
                          f"(once, about a minute for Zuid-Holland) ...")
        if os.path.exists(cache):
            os.remove(cache)
        gdal.VectorTranslate(cache, pbf, format="GPKG", layers=["lines"], layerName="ways",
                             where="highway IS NOT NULL")
        return cache

    def network(self, cache, box, mode, by_time, cell, walk_kmh, bike_kmh, feedback):
        """Network points (x, y), directed pieces (u, v, cost) and which points may be a start.
        Ways meet only at shared OSM nodes; every piece is at most `cell` long."""
        import numpy as np
        from osgeo import ogr, osr
        rd, wgs = osr.SpatialReference(), osr.SpatialReference()
        rd.ImportFromEPSG(28992)
        wgs.ImportFromEPSG(4326)
        for s in (rd, wgs):
            s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        corners = osr.CoordinateTransformation(rd, wgs).TransformPoints(
            [(box[0], box[2]), (box[1], box[2]), (box[0], box[3]), (box[1], box[3])])
        ds = ogr.Open(cache)
        lyr = ds.GetLayerByName("ways")
        lyr.SetSpatialFilterRect(min(c[0] for c in corners), min(c[1] for c in corners),
                                 max(c[0] for c in corners), max(c[1] for c in corners))
        chunks, attrs = [], []
        for f in lyr:
            r = rule(tags_of(f), mode, bike_kmh, walk_kmh)
            if r is None:
                continue
            pts = np.asarray(f.GetGeometryRef().GetPoints(), dtype=float)[:, :2]
            if len(pts) >= 2:
                chunks.append(pts)
                attrs.append(r)
        if not chunks:
            raise QgsProcessingException("No ways for this transport near the points. Is the OSM file the right area?")
        lengths = np.array([len(c) for c in chunks])
        ll = np.concatenate(chunks)
        way = np.repeat(np.arange(len(chunks)), lengths)
        fwd, bwd, kmh, start = (np.array(a) for a in zip(*attrs))
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
        cost = piece_len / (kmh[pway] * 1000 / 60) if by_time else piece_len
        cost = np.maximum(cost, 1e-6)

        f_, b_ = fwd[pway], bwd[pway]
        u = np.concatenate([pu[f_], pv[b_]])
        v = np.concatenate([pv[f_], pu[b_]])
        c = np.concatenate([cost[f_], cost[b_]])
        # Two ways over the same nodes: keep the cheaper (a sparse matrix would add them up).
        order = np.lexsort((c, u * len(x) + v))
        _, firsts = np.unique((u * len(x) + v)[order], return_index=True)
        pick = order[firsts]
        start_ok = np.zeros(len(x), dtype=bool)
        start_ok[pu[start[pway]]] = True
        start_ok[pv[start[pway]]] = True
        feedback.pushInfo(f"  {len(x):,} network points, {len(pick):,} directed pieces")
        return x, y, u[pick], v[pick], c[pick], start_ok

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
