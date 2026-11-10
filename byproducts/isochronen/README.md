# Isochrones from your own points (QGIS Processing script)

`isochronen_osm.py` is a QGIS Processing script. It draws isochrones around any point layer, on foot, by bicycle, car, lorry, motorcycle or bus, as time (minutes) or distance (metres), over OpenStreetMap. It needs only QGIS: no data from this repository, no GRASS, no Valhalla. Without an OSM file of your own, it downloads the Geofabrik extract of Zuid-Holland once.

Which roads each transport may use, and in which direction, follows the route planner [Valhalla](https://github.com/valhalla/valhalla) 3.9.1: its tag rules (`graph.lua`) and the access changes in its C++ code, written out in Python. There is one deliberate difference: private roads (`access=private`) stay closed unless a tag for that transport opens them. Valhalla keeps them open but avoids them as a through route. Walking also follows the one change this repository makes to Valhalla for the 300: pedestrians may use cycle paths (RVV 1990 art. 4).

## Install

1. In QGIS: Processing > Toolbox.
2. Click the Python icon at the top of the Toolbox, choose "Add Script to Toolbox..." and pick `isochronen_osm.py`.
3. The tool is now under Scripts > Bereikbaarheid > "Isochrones from points (OSM)". Double-click it to open its form, or drag it into the Model Designer as a step.

## The form

| Field | What |
|---|---|
| Start points | any point layer |
| Point name field | optional, copied to the result |
| Transport | walking (5.1 km/h), cycling (15 km/h), or car, truck, motorcycle or bus (maxspeed, or a default per road type; trucks and buses at most 80 km/h, RVV 1990 art. 21) |
| Measure | time in minutes, or distance in metres |
| Values | comma separated, e.g. `5, 10, 15` |
| Merge points | one isochrone per value for all points together, instead of one per point |
| OSM download area | one or more provinces, or the whole Netherlands, downloaded once from Geofabrik to `%LOCALAPPDATA%\isochronen_osm`. A `*` marks the provinces that border Zuid-Holland (Noord-Holland, Utrecht, Gelderland, Noord-Brabant, Zeeland): tick that neighbour as well for points near the border, or the isochrone stops there |
| OSM file | your own `.osm.pbf`, instead of a download |
| Advanced | walking or cycling speed, cell size, buffer around reached roads, snap distance, and the Expansion output |

The result is a polygon layer in RD New with one feature per point per value: `point_id`, `point_name`, `mode`, `measure`, `value`, `unit`, `km2`.

The Expansion checkbox (under Advanced) adds a temporary layer "Expansion ..." to the map when the tool finishes. It works like Valhalla's expansion. It has every street segment the route search reached, as a line with `cost_from` and `cost_to` (the time or distance at both ends; `cost_to` is empty where the search stops halfway) and `order` (1 = reached first).

## Travel time and route choice

The isochrone shows travel time (or distance). Like Valhalla, the route search may weigh some roads more heavily than the time they take:

- Steps (cycling): ridden at 1 km/h while carrying the bike, and weighted 8 times that, so a route takes them only when nothing else is close.
- `bicycle=dismount`: walked with the bike, at 5.1 km/h.
- Driveways, parking aisles and `access=destination` roads: 600 s added on entering them. They are reached as a destination but almost never used as a short cut. The isochrone counts their real travel time; we checked this in Valhalla's own expansion.

Valhalla's other costs are not copied: its preference for cycle paths over busy roads, road surface, hills, turns and traffic lights.

## Agreement with Valhalla

`vergelijk_valhalla.py` checks the network of every transport against the access bits in Valhalla's tiles, way by way (see the head of that script). It reads the tiles directly. pyvalhalla does not expose a road's access, so the script was checked against edge lengths and against Valhalla's own `locate`. `vergelijk_valhalla.py stats <pbf> <tile folder>` does all transports at once for a whole extract. It writes the totals and every way that differs to `data/processed/`.

The whole Netherlands, OSM of 10 November 2026 (2.83 million ways), private roads left out:

| Transport | Same as Valhalla | Different | Same, share |
|---|---|---|---|
| Walking | 315,286 km | 125.7 km | 99.96% |
| Cycling | 315,797 km | 123.7 km | 99.96% |
| Car | 315,885 km | 0 km | 100% |
| Truck | 315,922 km | 0 km | 100% |
| Motorcycle | 315,893 km | 0 km | 100% |
| Bus | 315,898 km | 0 km | 100% |

Zuid-Holland on its own (OSM of 12 October 2026): walking 9.8 km different, cycling 12.8 km, the motor vehicles 0 km.

What differs for walking and cycling is about 700 slip roads with `motorroad=yes`, nearly all of it. Valhalla closes motorroads to pedestrians and cyclists, but in our build it left these slip roads open. We did not find the cause in its code. The script keeps them closed, as the road sign requires.

The Netherlands brought out two rules that Zuid-Holland hardly uses. Both are now in the script. Mountain-bike tags (`mtb:scale` and the like) open a path to bicycles, even over `bicycle=no` or `oneway:bicycle`, unless the way is closed to every transport. A closed way (a loop) with a `leisure`, `landuse`, `building` or `natural` key is left out, for example a cycle racing track or an ice rink.

## Run times

Three stations (Delft, Den Haag CS, Gouda) on a 6-core workstation, after the one-time OSM read of about 30 s: walking 5/10/15 min about 15 s, cycling 1/3 km merged 18 s, car 10/20 min 56 s.

## Limits

Times by car, truck, motorcycle and bus are optimistic: traffic lights, junctions, congestion and turn restrictions are not modelled. Weight and height limits for trucks are not modelled either. There's no public transport. An extract ends at its province border, so tick the neighbouring province or the whole Netherlands for points near a border or for long car trips. The method and every choice are described at the top of the script.
