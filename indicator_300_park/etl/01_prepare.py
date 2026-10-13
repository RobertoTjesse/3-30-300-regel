"""
01_prepare.py — Parks, park entrances, walking network and homes for the 300.

Reads the parks (config.PARKS_*), the OpenStreetMap extract (config.OSM_PBF)
and the BAG addresses of the 3, for the study area (config.MUNICIPALITIES plus
a margin), and writes one GeoPackage, config.PREPARED_GPKG, in RD New:

  area       the study area (the municipalities, without the margin)
  parks      the parks that count: park_id, m2, plus the source fields
  network    walkable OSM ways, cut at every junction (shared OSM node), so
             a path over a bridge does not meet the street below it.
             Used by GRASS; Valhalla builds its own network from the same file.
  gates      OSM gates and entrances (barrier=gate etc., entrance=*)
  entrances  park entrances: ent_id, park_id, soort
               pad   a walkable way crosses the park edge
               poort an OSM gate or entrance on the edge
               rand  open edge along a street (parks with neither of the above)
  homes      BAG addresses in use with woonfunctie: home_id, pand_id, gemeente

Usage (from the repository root, with the OSGeo4W Python):
    python indicator_300_park/etl/01_prepare.py
"""
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402

from osgeo import gdal, ogr, osr  # noqa: E402
gdal.UseExceptions()
ogr.UseExceptions()

RD = osr.SpatialReference()
RD.ImportFromEPSG(28992)
WGS84 = osr.SpatialReference()
WGS84.ImportFromEPSG(4326)
for s in (RD, WGS84):
    s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

TAG = re.compile(r'"([^"]+)"=>"((?:[^"\\]|\\.)*)"')


def tags_of(feature):
    """The OSM tags of a GDAL OSM feature: its own columns plus other_tags."""
    tags = dict(TAG.findall(feature.GetField("other_tags") or ""))
    for i in range(feature.GetFieldCount()):
        name = feature.GetFieldDefnRef(i).GetName()
        if name not in ("other_tags", "osm_id", "osm_way_id") and feature.IsFieldSet(i):
            tags[name] = feature.GetField(i)
    return tags


def walkable(tags):
    """True when a pedestrian may use this way (config.WALKABLE_HIGHWAYS, access tags)."""
    if tags.get("highway") not in config.WALKABLE_HIGHWAYS or tags.get("area") == "yes":
        return False
    foot = tags.get("foot")
    if foot == "no":
        return False
    if foot in config.FOOT_ALLOWED:
        return True
    return tags.get("access") not in config.ACCESS_CLOSED


def study_area():
    """(area, margin): the municipalities as one RD geometry, and that plus config.AREA_MARGIN_M."""
    ds = ogr.Open(str(config.GEBIEDEN_GPKG))
    lyr = ds.GetLayerByName("gemeenten")
    area = ogr.Geometry(ogr.wkbMultiPolygon)
    found = set()
    for f in lyr:
        if not config.MUNICIPALITIES or f.GetField("naam") in config.MUNICIPALITIES:
            area = area.Union(f.GetGeometryRef())
            found.add(f.GetField("naam"))
    missing = set(config.MUNICIPALITIES) - found
    if missing:
        sys.exit(f"ERROR: not in {config.GEBIEDEN_GPKG.name}: {', '.join(sorted(missing))}")
    return area, area.Buffer(config.AREA_MARGIN_M, 4)


def new_layer(ds, name, geom_type, fields):
    lyr = ds.CreateLayer(name, RD, geom_type)
    for fname, ftype in fields:
        lyr.CreateField(ogr.FieldDefn(fname, ftype))
    return lyr


def write_parks(out, margin):
    """Copy the parks inside the margin to layer parks; returns their count. Leaves out parks
    under config.MIN_PARK_AREA_M2 and narrow strips (perimeter / area > config.MAX_EDGE_RATIO)."""
    src = ogr.Open(str(config.PARKS_PATH))
    lyr = src.GetLayerByName(config.PARKS_LAYER) if config.PARKS_LAYER else src.GetLayer(0)
    if config.PARKS_WHERE:
        lyr.SetAttributeFilter(config.PARKS_WHERE)
    to_rd = None
    srs = lyr.GetSpatialRef()
    if srs and not srs.IsSame(RD):
        srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        to_rd = osr.CoordinateTransformation(srs, RD)
        back = margin.Clone()
        back.Transform(osr.CoordinateTransformation(RD, srs))
        lyr.SetSpatialFilter(back)
    else:
        lyr.SetSpatialFilter(margin)
    defn = lyr.GetLayerDefn()
    keep = [defn.GetFieldDefn(i) for i in range(defn.GetFieldCount())
            if defn.GetFieldDefn(i).GetName().lower() not in ("park_id", "m2", "shape_length", "shape_area")]
    parks = new_layer(out, "parks", ogr.wkbMultiPolygon, [("park_id", ogr.OFTInteger), ("m2", ogr.OFTInteger)])
    for fd in keep:
        parks.CreateField(ogr.FieldDefn(fd.GetName(), fd.GetType()))
    n = 0
    out.StartTransaction()
    for f in lyr:
        g = f.GetGeometryRef()
        if g is None:
            continue
        g = ogr.ForceToMultiPolygon(g.Clone())
        if to_rd:
            g.Transform(to_rd)
        if not g.Intersects(margin) or g.GetArea() < config.MIN_PARK_AREA_M2:
            continue
        if config.MAX_EDGE_RATIO and g.Boundary().Length() > config.MAX_EDGE_RATIO * g.GetArea():
            continue
        if not g.IsValid():
            fixed = g.MakeValid()
            g = ogr.Geometry(ogr.wkbMultiPolygon)
            parts = [fixed] if fixed.GetGeometryCount() == 0 else [fixed.GetGeometryRef(i) for i in range(fixed.GetGeometryCount())]
            for part in parts:   # keep only the polygon parts of the repaired shape
                if ogr.GT_Flatten(part.GetGeometryType()) == ogr.wkbPolygon:
                    g.AddGeometry(part)
                elif ogr.GT_Flatten(part.GetGeometryType()) == ogr.wkbMultiPolygon:
                    for j in range(part.GetGeometryCount()):
                        g.AddGeometry(part.GetGeometryRef(j))
            if g.IsEmpty():
                continue
        n += 1
        nf = ogr.Feature(parks.GetLayerDefn())
        nf.SetField("park_id", n)
        nf.SetField("m2", round(g.GetArea()))
        for fd in keep:
            nf.SetField(fd.GetName(), f.GetField(fd.GetName()))
        nf.SetGeometry(g)
        parks.CreateFeature(nf)
    out.CommitTransaction()
    return n


def osm_gpkg():
    """The ways and points of the OSM extract as a GeoPackage (made once per .pbf).
    GDAL reads a .pbf front to back, all layers at once; a GeoPackage has a
    spatial index, so every later run only reads its own area."""
    cache = config.OSM_GPKG
    if cache.exists() and cache.stat().st_mtime > config.OSM_PBF.stat().st_mtime:
        return cache
    print(f"Converting {config.OSM_PBF.name} to {cache.name} (once) …")
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists():
        cache.unlink()
    gdal.VectorTranslate(str(cache), str(config.OSM_PBF), format="GPKG", layers=["lines"],
                         where="highway IS NOT NULL")
    gdal.VectorTranslate(str(cache), str(config.OSM_PBF), format="GPKG", accessMode="update",
                         layers=["points"], where="barrier IS NOT NULL OR other_tags LIKE '%\"entrance\"%'")
    return cache


def read_osm(margin):
    """(ways, gates) inside the margin from the OSM extract.
    ways: [(osm_id, highway, [(lon, lat), …])], gates: [(osm_id, kind, lon, lat)]."""
    wgs = margin.Clone()
    wgs.Transform(osr.CoordinateTransformation(RD, WGS84))
    minx, maxx, miny, maxy = wgs.GetEnvelope()
    ds = gdal.OpenEx(str(osm_gpkg()), gdal.OF_VECTOR)
    ways = []
    lines = ds.GetLayerByName("lines")
    lines.SetSpatialFilterRect(minx, miny, maxx, maxy)
    for f in lines:
        tags = tags_of(f)
        if walkable(tags):
            g = f.GetGeometryRef()
            ways.append((f.GetField("osm_id"), tags["highway"],
                         [(g.GetX(i), g.GetY(i)) for i in range(g.GetPointCount())]))
    gates = []
    points = ds.GetLayerByName("points")
    points.SetSpatialFilterRect(minx, miny, maxx, maxy)
    for f in points:
        tags = tags_of(f)
        kind = tags.get("entrance") and "entrance" or (
            tags.get("barrier") in ("gate", "entrance", "kissing_gate", "swing_gate", "lift_gate",
                                    "stile", "turnstile", "bollard", "cycle_barrier") and tags["barrier"])
        if kind and tags.get("access") not in config.ACCESS_CLOSED and tags.get("foot") != "no":
            g = f.GetGeometryRef()
            gates.append((f.GetField("osm_id"), kind, g.GetX(), g.GetY()))
    return ways, gates


def write_network(out, ways, gates, margin):
    """Cut the ways at every shared OSM node and write layers network and gates."""
    uses = {}
    for _, _, pts in ways:
        for p in pts:
            uses[p] = uses.get(p, 0) + 1
    to_rd = osr.CoordinateTransformation(WGS84, RD)
    net = new_layer(out, "network", ogr.wkbLineString, [("osm_id", ogr.OFTString), ("highway", ogr.OFTString)])
    n = 0
    out.StartTransaction()
    for osm_id, highway, pts in ways:
        start = 0
        for i in range(1, len(pts)):
            if i == len(pts) - 1 or uses[pts[i]] > 1:
                g = ogr.Geometry(ogr.wkbLineString)
                for x, y in pts[start:i + 1]:
                    g.AddPoint_2D(x, y)
                g.Transform(to_rd)
                if g.Length() > 0:
                    f = ogr.Feature(net.GetLayerDefn())
                    f.SetField("osm_id", osm_id)
                    f.SetField("highway", highway)
                    f.SetGeometry(g)
                    net.CreateFeature(f)
                    n += 1
                start = i
    gl = new_layer(out, "gates", ogr.wkbPoint, [("osm_id", ogr.OFTString), ("soort", ogr.OFTString)])
    for osm_id, kind, x, y in gates:
        g = ogr.Geometry(ogr.wkbPoint)
        g.AddPoint_2D(x, y)
        g.Transform(to_rd)
        if g.Within(margin):
            f = ogr.Feature(gl.GetLayerDefn())
            f.SetField("osm_id", osm_id)
            f.SetField("soort", kind)
            f.SetGeometry(g)
            gl.CreateFeature(f)
    out.CommitTransaction()
    return n


def points_of(g):
    """The points of an intersection result (points, or the ends of overlapping lines)."""
    t = ogr.GT_Flatten(g.GetGeometryType())
    if t == ogr.wkbPoint:
        return [(g.GetX(), g.GetY())]
    if t == ogr.wkbLineString:
        n = g.GetPointCount()
        return [(g.GetX(0), g.GetY(0)), (g.GetX(n - 1), g.GetY(n - 1))] if n else []
    return [p for i in range(g.GetGeometryCount()) for p in points_of(g.GetGeometryRef(i))]


def nearby(lyr, g, dist):
    """Features of a (spatially indexed) layer within dist of geometry g."""
    minx, maxx, miny, maxy = g.GetEnvelope()
    lyr.SetSpatialFilterRect(minx - dist, miny - dist, maxx + dist, maxy + dist)
    out = [f for f in lyr if f.GetGeometryRef().Distance(g) <= dist]
    lyr.SetSpatialFilter(None)
    return out


def write_entrances(out):
    """Find the entrances of every park (see the module docstring)."""
    parks, net, gates = (out.GetLayerByName(n) for n in ("parks", "network", "gates"))
    ent = new_layer(out, "entrances", ogr.wkbPoint,
                    [("ent_id", ogr.OFTInteger), ("park_id", ogr.OFTInteger), ("soort", ogr.OFTString)])
    counts = {"pad": 0, "poort": 0, "rand": 0}
    no_entrance = 0
    n = 0
    out.StartTransaction()
    for park in list(parks):
        pid = park.GetField("park_id")
        edge = park.GetGeometryRef().Boundary()
        found = []   # (x, y, soort)
        for f in nearby(net, edge, 0):
            hit = f.GetGeometryRef().Intersection(edge)
            if hit and not hit.IsEmpty():
                found += [(x, y, "pad") for x, y in points_of(hit)]
        for f in nearby(gates, edge, config.GATE_SNAP_M):
            g = f.GetGeometryRef()
            found.append((g.GetX(), g.GetY(), "poort"))
        if not found:   # open edge: walk every part of it (outer rings and holes)
            parts = ([edge.GetGeometryRef(i) for i in range(edge.GetGeometryCount())]
                     if edge.GetGeometryCount() else [edge])
            for part in parts:
                d = 0.0
                while d < part.Length():
                    p = part.Value(d)
                    if nearby(net, p, config.EDGE_STREET_M):
                        found.append((p.GetX(), p.GetY(), "rand"))
                    d += config.EDGE_SPACING_M
        kept = []
        rank = {"poort": 0, "pad": 1, "rand": 2}
        for x, y, soort in sorted(found, key=lambda e: rank[e[2]]):
            if all((x - kx) ** 2 + (y - ky) ** 2 > config.ENTRANCE_MERGE_M ** 2 for kx, ky, _ in kept):
                kept.append((x, y, soort))
        if not kept:
            no_entrance += 1
        for x, y, soort in kept:
            n += 1
            counts[soort] += 1
            f = ogr.Feature(ent.GetLayerDefn())
            f.SetField("ent_id", n)
            f.SetField("park_id", pid)
            f.SetField("soort", soort)
            g = ogr.Geometry(ogr.wkbPoint)
            g.AddPoint_2D(x, y)
            f.SetGeometry(g)
            ent.CreateFeature(f)
    out.CommitTransaction()
    return n, counts, no_entrance


def write_homes(out, area):
    """BAG addresses in use with woonfunctie inside the study area, with their gemeente."""
    gem = []
    ds = ogr.Open(str(config.GEBIEDEN_GPKG))
    for f in ds.GetLayerByName("gemeenten"):
        if not config.MUNICIPALITIES or f.GetField("naam") in config.MUNICIPALITIES:
            gem.append((f.GetField("naam"), f.GetGeometryRef().Clone()))
    src = ogr.Open(str(config.PROVINCE_ADDRESSES_GPKG))
    lyr = src.GetLayer(0)
    homes = new_layer(out, "homes", ogr.wkbPoint,
                      [("home_id", ogr.OFTString), ("pand_id", ogr.OFTString), ("gemeente", ogr.OFTString)])
    statuses = {s.lower() for s in config.BAG_STATUSES_IN_USE}
    n = 0
    out.StartTransaction()
    for name, geom in gem:
        lyr.SetSpatialFilter(geom)
        for f in lyr:
            if (f.GetField(config.BAG_ADDRESS_STATUS) or "").lower() not in statuses:
                continue
            if "woonfunctie" not in (f.GetField(config.BAG_ADDRESS_USE) or ""):
                continue
            g = f.GetGeometryRef()
            if not g.Within(geom):
                continue
            nf = ogr.Feature(homes.GetLayerDefn())
            nf.SetField("home_id", f.GetField("identificatie"))
            nf.SetField("pand_id", (f.GetField(config.BAG_ADDRESS_BUILDING_ID) or "").split(",")[0])
            nf.SetField("gemeente", name)
            nf.SetGeometry(g.Clone() if g.GetGeometryCount() == 0 else g.GetGeometryRef(0).Clone())
            homes.CreateFeature(nf)
            n += 1
    out.CommitTransaction()
    return n


def main():
    t0 = time.time()
    if not config.PARKS_PATH.exists():
        sys.exit(f"ERROR: {config.PARKS_PATH} is missing. Copy the voorzieningen geodatabase there, "
                 "or set PARKS_PATH in config_local_300.py")
    config.WORK_DIR.mkdir(parents=True, exist_ok=True)
    if config.PREPARED_GPKG.exists():
        config.PREPARED_GPKG.unlink()
    area, margin = study_area()
    print(f"Study area: {', '.join(config.MUNICIPALITIES) or config.PROVINCE} "
          f"({area.GetArea() / 1e6:.1f} km2, margin {config.AREA_MARGIN_M:.0f} m)")
    out = ogr.GetDriverByName("GPKG").CreateDataSource(str(config.PREPARED_GPKG))
    al = new_layer(out, "area", ogr.wkbMultiPolygon, [])
    f = ogr.Feature(al.GetLayerDefn())
    f.SetGeometry(ogr.ForceToMultiPolygon(area))
    al.CreateFeature(f)

    print(f"Parks: {write_parks(out, margin):,} from {config.PARKS_PATH.name}")
    t = time.time()
    ways, gates = read_osm(margin)
    print(f"OSM: {len(ways):,} walkable ways, {len(gates):,} gates/entrances ({time.time() - t:.0f} s)")
    print(f"Network: {write_network(out, ways, gates, margin):,} segments between junctions")
    out = None   # close, so the layers get their spatial index before the queries below
    out = ogr.Open(str(config.PREPARED_GPKG), 1)
    t = time.time()
    n, counts, none = write_entrances(out)
    print(f"Entrances: {n:,} ({', '.join(f'{k} {v:,}' for k, v in counts.items())}); "
          f"parks without an entrance: {none:,} ({time.time() - t:.0f} s)")
    print(f"Homes: {write_homes(out, area):,} addresses with woonfunctie in use")
    print(f"Wrote {config.PREPARED_GPKG} in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
