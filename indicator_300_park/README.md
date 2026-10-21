# The 300: green within 300 m of every home

Does every home lie within 300 m, or a 5-minute walk, of a park or other public green? `etl/` computes this for every home in the province as a walking distance in metres to the nearest entrance of the green, over the street and path network. The web map shows this result.

**QGIS does most of the work.** GDAL/OGR, part of QGIS in [OSGeo4W](https://trac.osgeo.org/osgeo4w/), prepares the data, cuts out the water, finds the entrances and builds the walking network; GRASS GIS 8.4 (`v.net.distance`) computes the walking distances; QGIS's own Python (with numpy and scipy) runs the steps; QGIS shows the results. Valhalla, an open-source route planner outside QGIS (`pip install pyvalhalla`, no server), computes the same distances a second time. Where GRASS puts a home beyond 300 m, the map takes Valhalla's distance if it is shorter. No licence or server.

## Steps

| Step | Script | What |
|---|---|---|
| 1 | `etl/01_prepare.py` | Green areas (water cut out, merged), entrances, the walkable OSM network (cut at shared OSM nodes) and the BAG homes of the 3, for `MUNICIPALITIES` plus a margin |
| 2 | `etl/03_grass.py` | GRASS `v.net.distance` from all entrances at once, then the walking distance per home |
| | `etl/kaart_isochronen.py` | Maps of the green areas and walking zones, province and The Hague/Delft |
| 3 | `etl/02_valhalla.py` | The same distances with Valhalla (expansion and `locate`), and the isochrone polygons of the old method |
| 4 | `etl/05_combine.py` | The distance the map shows: GRASS, or Valhalla where GRASS has more than 300 m and Valhalla finds a shorter way |
| | `etl/00_valhalla_tiles.py` | Once per OSM extract: Valhalla's walking network, with `valhalla/graph_fietspad.lua` (cycle paths open to pedestrians) |
| check | `etl/04_compare.py` | Optional: GRASS, Valhalla and the isochrones per home |
| | `etl/explainer_expansion.py` | The pictures on the explanation page (expansion from the Provinciehuis, zone against walk, cycle-path example) |
| | `etl/run_300.sh` | Runs steps and logs each step's run time to `logs/300_benchmark.csv` |

The web map's tiles come from `web/build_tiles_30_300.py` (reads `300.gpkg`) and `web/build_tiles_groen.py` (reads the green areas).

```
bash indicator_300_park/etl/run_300.sh "01_prepare.py" "03_grass.py --parks-only" "03_grass.py --homes-only"   "02_valhalla.py" "05_combine.py"
python web/build_tiles_groen.py
python web/build_tiles_30_300.py
```

`--parks-only` does the green (the distance to every junction); `--homes-only` then adds the homes, reusing that work. Settings are in `etl/config.py`; machine-specific ones go in `etl/config_local_300.py` (gitignored, full paths). `MUNICIPALITIES = []` runs the whole province; `MUNICIPALITY_OVERRIDE=Delft` runs one municipality.

## Method

**Green.** Three sources, all gitignored under `data/raw/`:

| Source (`bron`) | Layer | What counts |
|---|---|---|
| `voorzieningen` | `voorzieningen/voorzieningen.gdb`, layer `parken_vlakken` | parks from OpenStreetMap and TOP10NL in the provincial facilities map, peildatum 17 September 2025 |
| `top10nl` | `top10_groen/top10_groen.gdb`, layer `functioneel_gebied_groen` | TOP10NL functioneel gebied: botanische tuin, heemtuin, landgoed, nationaal park, natuurgebied, recreatiegebied, park |
| `top10nl recreatie` | `top10_recreatiegebieden/Top10_recreatiegebieden.shp` | TOP10NL functioneel gebied: begraafplaats and volkstuinen |

Duck decoys are left out: they are mostly fenced and closed to visitors.

**Water.** Nature areas such as the Haringvliet, the Voordelta and the Biesbosch are largely open water, so a path along their shore would be a park entrance. The water parts of the CBS municipal map 2025 (PDOK) and the OSM water polygons are cut out of every green polygon first (`CUT_WATER`; cached in `data/interim/osm/water.gpkg`): 103,076 ha in the province.

**Filter and merge.** Polygons under 300 m2 and strips narrower than about 6 m (perimeter / area above 0.35) drop out. The rest is merged where polygons overlap or touch, so a path from one green polygon into the next is not an entrance: 3,660 green areas. Each keeps its sources, kinds and names.

**Entrances.** Where a walkable way crosses the edge of a green area, an OSM gate or entrance on the edge, and, for green areas with neither, points every 25 m along the edge where a walkable way runs within 10 m: 14,924 entrances (11,091 paths crossing the edge, 1,323 OSM gates, 2,510 open edges along a street).

**Walking network.** The Geofabrik OpenStreetMap extract of Zuid-Holland (12 October 2026): footways, paths, cycle paths, streets and roads, without motorways and trunk roads, and without ways tagged `access=private` or `foot=no` unless `foot=` allows walking. Cycle paths count because Dutch traffic law (RVV 1990, art. 4) lets pedestrians use one where there is no footpath. The ways are cut at every shared OSM node, so a path on a bridge does not meet the street below it.

**Distance per home.** GRASS gives every junction its walking distance to the nearest entrance, in one run from all entrances at once. A home (a BAG address with woonfunctie in use, as in the 3) takes the nearest point on the network within 75 m, the shorter way along that segment to one of its junctions, and the straight line from the home to the network. A home inside a green area is in the green already: 0 m, however far the way to an entrance (3,047 homes).

**A second look with Valhalla.** Where GRASS puts a home beyond 300 m, or finds no way at all, Valhalla's distance counts if it is shorter. Valhalla walks the same OSM extract by its own rules: it can reach a home over its own private or destination-only road, which the network for GRASS leaves out, and it ties a home to the street it lies on rather than to the nearest one. A home wrongly shown as too far is the worse error, so the map gives it the benefit of the doubt. This puts 31,587 more homes within 300 m and 27,892 more within 5 minutes.

The thresholds are 300 m, 5 minutes (425 m at 5.1 km/h) and 15 minutes (1,275 m). On the map a building takes the class of its nearest home; the shares per area count every home.

## Results, Zuid-Holland

1,797,970 home addresses: **38.5% within 300 m, 57.6% within 5 minutes, 98.1% within 15 minutes.** Per municipality, between 21.4% (Voorne aan Zee) and 87.8% (Oegstgeest) within 5 minutes, and between 11.5% (Hardinxveld-Giessendam) and 62.5% (Oegstgeest) within 300 m. GRASS alone gives 36.7%, 56.0% and 97.8%.

**GRASS against Valhalla.** Valhalla alone puts 38.0% within 300 m and 57.0% within 5 minutes. GRASS and Valhalla agree for 97.8% of homes on 300 m. For 1,757,808 homes both give a distance: the difference is at most 10 m for 71% of them and at most 50 m for 93%, with a median of 2 m. Where they differ, Valhalla is shorter more often (5.2% of homes by more than 50 m, against 1.9% longer): it can reach a home over its own private road, while the network for GRASS leaves `access=private` out.

**Zones against walking distance.** Valhalla's isochrone polygons also cover the inside of blocks, water and railway land that the walk goes round: 46.4% of homes inside the 300 m zones, 64.0% inside the 5-minute zones.

**The earlier calculation** had 79.1% of homes within 5 minutes; per home it agrees with GRASS for 69%. Most of the difference is the green: that run counted every polygon of its green map, including small woods, scrub, gardens, golf courses and sports grounds. The rest comes from its zones.

## Run times

The province on a 6-core workstation with 64 GB RAM (`logs/300_benchmark.csv`):

| Step | Time |
|---|---|
| Green, water, entrances, network, homes (`01_prepare.py`, GDAL) | 8.3 min |
| Distance to every junction (GRASS) | 18.1 min |
| 1.8 million homes (GRASS result) | 4.6 min |
| Map tiles (`build_tiles_30_300.py`, `build_tiles_groen.py`) | 15 min |
| Valhalla: network, expansion and homes | 0.7 + 13.4 + 12.2 min |
| GRASS and Valhalla combined (`05_combine.py`) | 5.3 min |
| The check: comparison and figures (`04_compare.py`) | 19 min |

Delft takes about a minute.

## Known issues

See the wiki page [Known issues](https://github.com/RobertoTjesse/3-30-300-regel/wiki/Known-issues), among them the green areas without an entrance ([issue #3](https://github.com/RobertoTjesse/3-30-300-regel/issues/3)).

## One-time setup

- OSM: `data/raw/osm/zuid-holland-latest.osm.pbf` from [Geofabrik](https://download.geofabrik.de/europe/netherlands/zuid-holland.html). Step 1 turns it into `data/interim/osm/osm_ways_points.gpkg` once.
- GRASS: `grass84.bat` from OSGeo4W, run with `--tmp-project EPSG:28992`; nothing to install.
- Valhalla: `python -m venv %USERPROFILE%\valhalla-venv` with the OSGeo4W Python (3.12), then `pip install pyvalhalla==3.9.1`, then `00_valhalla_tiles.py`. It calls `valhalla_build_tiles.exe` directly, because `python -m valhalla` cannot find the executables on Windows in 3.9.1.

## In QGIS

`qgis_validation/build_project.py` puts the group "De 300 – loopafstand tot groen" in the validation project: the walking distance per home as on the map, the entrances and the green areas.

## The earlier method

Until 13 October 2026 the map's 300 came from an earlier calculation with PostGIS and a Valhalla server. That run used OSM and TOP10NL green of 2024, the BAG of 9 December 2025, and 5- and 15-minute isochrones around 27,111 entrances; a building counted when any part of it lay in a 5-minute zone.

Method and caveats for readers: `web/uitleg/300.html` (Dutch) and `web/uitleg/en/300.html` (English).
