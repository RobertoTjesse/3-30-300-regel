# Isochrones from your own points (QGIS Processing script)

`isochronen_osm.py` is a QGIS Processing script. It draws isochrones around any point layer, by walking, cycling or car, as time (minutes) or distance (metres), over OpenStreetMap. It needs only QGIS: no data from this repository, no GRASS, no Valhalla. Without an OSM file of your own, it downloads the Geofabrik extract of Zuid-Holland once.

## Install

1. In QGIS: Processing > Toolbox.
2. Click the Python icon at the top of the Toolbox, choose "Add Script to Toolbox..." and pick `isochronen_osm.py`.
3. The tool is now under Scripts > Bereikbaarheid > "Isochrones from points (OSM)". Double-click it to open its form, or drag it into the Model Designer as a step.

## The form

| Field | What |
|---|---|
| Start points | any point layer |
| Point name field | optional, copied to the result |
| Transport | walking (5.1 km/h), cycling (15 km/h) or car (maxspeed, or a default per road type) |
| Measure | time in minutes, or distance in metres |
| Values | comma separated, e.g. `5, 10, 15` |
| Merge points | one isochrone per value for all points together, instead of one per point |
| OSM download area | one or more provinces, or the whole Netherlands, downloaded once from Geofabrik to `%LOCALAPPDATA%\isochronen_osm`. A `*` marks the provinces that border Zuid-Holland (Noord-Holland, Utrecht, Gelderland, Noord-Brabant, Zeeland): tick that neighbour as well for points near the border, or the isochrone stops there |
| OSM file | your own `.osm.pbf`, instead of a download |
| Advanced | walking or cycling speed, cell size, buffer around reached roads, snap distance, and the Expansion output |

The result is a polygon layer in RD New with one feature per point per value: `point_id`, `point_name`, `mode`, `measure`, `value`, `unit`, `km2`.

The optional Expansion output (under Advanced, untick "Skip output") works like Valhalla's expansion. It has every street segment the route search reached, as a line with `cost_from` and `cost_to` (the time or distance at both ends; `cost_to` is empty where the search stops halfway) and `order` (1 = reached first).

## Run times

Three stations (Delft, Den Haag CS, Gouda) on a 6-core workstation, after the one-time OSM read of about 30 s: walking 5/10/15 min about 15 s, cycling 1/3 km merged 18 s, car 10/20 min 56 s.

## Limits

Car times are optimistic: traffic lights, junctions, congestion and turn restrictions are not modelled. There's no public transport. An extract ends at its province border, so tick the neighbouring province or the whole Netherlands for points near a border or for long car trips. The method and every choice are described at the top of the script.
