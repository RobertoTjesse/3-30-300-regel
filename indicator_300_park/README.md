# The 300: green within 300 m of every home

Does every home lie within 300 m, or a 5-minute walk, of a park or other public green? `etl/` computes this for every home in the province as a walking distance in metres to the nearest park entrance over the street and path network.

The whole analysis runs on OSGeo software from [OSGeo4W](https://trac.osgeo.org/osgeo4w/), the same installation that comes with QGIS: GDAL/OGR prepares the data, finds the entrances and builds the walking network, GRASS GIS 8.4 (`v.net.distance`) computes the walking distances, and QGIS shows the results. The scripts use OSGeo4W's own Python with numpy and scipy. Valhalla, an open-source route planner outside OSGeo, computes the same distances a second time as an independent check.

The web map still shows the earlier FME result (see the end of this page). It moves to this method once open water has been cut out of the green (see "Known issues").

## Steps

| Step | Script | What |
|---|---|---|
| 1 | `etl/01_prepare.py` | Green areas, park entrances, the walkable OSM network (cut at shared OSM nodes) and the BAG homes of the 3, for `MUNICIPALITIES` plus a margin |
| 2 | `etl/03_grass.py` | GRASS `v.net.distance` from all entrances at once, then the walking distance per home |
| 3 | `etl/02_valhalla.py` | The check: Valhalla expansion from every entrance, Valhalla's own placing of every home (`locate`), and the isochrone polygons of the old method |
| 4 | `etl/04_compare.py` | GRASS, Valhalla, the isochrones and the old FME result per home; summary CSV, scatter plot and a map of where GRASS and Valhalla disagree |
| | `etl/00_valhalla_tiles.py` | Once per OSM extract: Valhalla's walking network, with `valhalla/graph_fietspad.lua` (Valhalla's own tag rules with cycle paths open to pedestrians) |
| | `etl/kaart_isochronen.py` | Maps of the green areas and isochrones for the province and for The Hague and Delft |
| | `etl/explainer_expansion.py` | The pictures on the explanation page: expansion from the Provinciehuis, isochrone against expansion, and a cycle-path example |
| | `etl/run_300.sh` | Runs steps and logs each step's run time to `logs/300_benchmark.csv` |

For the province the work comes in two phases. With `--parks-only`, steps 2 and 3 do the parks: the distance to every junction of the network (GRASS), and the expansion and isochrones (Valhalla). With `--homes-only` they add the homes, reusing that work, so a new set of homes or addresses does not redo the routing.

```
bash indicator_300_park/etl/run_300.sh "01_prepare.py" "03_grass.py --parks-only" "02_valhalla.py --parks-only"
bash indicator_300_park/etl/run_300.sh "03_grass.py --homes-only" "02_valhalla.py --homes-only" "04_compare.py"
```

Settings are in `etl/config.py`; machine-specific ones go in `etl/config_local_300.py` (gitignored, full paths). `MUNICIPALITIES = []` runs the whole province; `MUNICIPALITY_OVERRIDE=Delft` runs one municipality.

## Method

**Green.** Three sources, all gitignored under `data/raw/`:

| Source (`bron`) | Layer | What counts |
|---|---|---|
| `voorzieningen` | `voorzieningen/voorzieningen.gdb`, layer `parken_vlakken` | parks from OpenStreetMap and TOP10NL in the provincial facilities map, peildatum 17 September 2025 |
| `top10nl` | `top10_groen/top10_groen.gdb`, layer `functioneel_gebied_groen` | TOP10NL functioneel gebied: botanische tuin, heemtuin, landgoed, nationaal park, natuurgebied, recreatiegebied, park |
| `top10nl recreatie` | `top10_recreatiegebieden/Top10_recreatiegebieden.shp` | TOP10NL functioneel gebied: begraafplaats and volkstuinen |

Duck decoys are left out: they are mostly fenced and closed to visitors. Polygons under 300 m2 and strips narrower than about 6 m (perimeter / area above 0.35) drop out. The rest is merged where polygons overlap or touch, so a path from one green polygon into the next is not an entrance. Each green area keeps its sources, kinds and names.

**Entrances.** Where a walkable way crosses the edge of a green area, an OSM gate or entrance on the edge, and, for green areas with neither, points every 25 m along the edge where a walkable way runs within 10 m. Entrances of one area closer than 10 m are merged.

**Walking network.** The Geofabrik OpenStreetMap extract of Zuid-Holland: footways, paths, streets and roads, without motorways and trunk roads, and without ways tagged `access=private` or `foot=no` unless `foot=` allows walking. Cycle paths count: Dutch traffic law (RVV 1990, art. 4) lets pedestrians use one where there is no footpath or pavement. Bridleways do not count. The ways are cut at every shared OSM node, so a path on a bridge does not meet the street below it.

**Distance per home.** GRASS gives every junction its walking distance to the nearest entrance, in one run from all entrances at once. A home (a BAG address with woonfunctie in use, as in the 3) takes the nearest point on the network within 75 m, the shorter way along that segment to one of its junctions, and the straight line from the home to the network. Valhalla does the same with its own network: the expansion from every entrance gives the distance per junction, and a home takes the shorter of two walks, via the street Valhalla puts it on or via the nearest street the expansion reached.

The thresholds are 300 m, 5 minutes (425 m at 5.1 km/h) and 15 minutes (1,275 m). Every home gets a distance in metres, so these are filters on one result.

## Results, Zuid-Holland

1,797,970 home addresses, 1,891 green areas, 11,270 entrances (8,229 where a path crosses the edge, 1,140 OSM gates, 1,901 open edges along a street). 207 green areas have no entrance. 99 of them lie inside the province (81 ha in all): 64 allotment complexes and 17 cemeteries without a path in and no street within 10 m, and some small parks and nature areas. The rest lie across the border, where the network stops.

| Share of homes within | GRASS | Valhalla | Valhalla isochrones | FME (old) |
|---|---|---|---|---|
| 300 m | 36.8% | 38.1% | 46.4% | not computed |
| 5 minutes (425 m) | 56.1% | 57.1% | 64.1% | 79.1% |
| 15 minutes (1,275 m) | 97.9% | 98.1% | 98.2% | 99.2% |

Per municipality, GRASS puts between 11% (Hardinxveld-Giessendam, Lisse) and 60% (Oegstgeest) of homes within 300 m.

**GRASS against Valhalla.** They agree for 97.8% of homes on 300 m and 97.9% on 5 minutes. For 1,758,168 homes both give a distance: the difference is at most 10 m for 71% of them and at most 50 m for 93%, with a median of 2 m. Where they differ, Valhalla is shorter more often (5.1% of homes by more than 50 m, against 1.9% longer): Valhalla can reach a home over its own private or destination-only road, while the GRASS network leaves `access=private` out.

**Against the isochrones.** An isochrone polygon also covers the inside of blocks, water and railway land that the walk goes round, so it counts 8 to 10 points more homes than the walking distance.

**Against FME.** The old FME run put 79.1% of homes within 5 minutes; GRASS puts 56.1% there. Per home they agree for 69% (Valhalla: 72%). 467,433 homes are within 5 minutes for FME but not for GRASS; 55,044 the other way round. Most of the difference is the green: the FME run counted every polygon of its green map, including woods, scrub, gardens, golf courses and sports grounds, while the new sources only hold parks, nature, recreation areas, estates, cemeteries and allotments. The rest comes from FME's isochrone zones.

## Run times

The province on a 6-core workstation with 64 GB RAM (`logs/300_benchmark.csv`):

| Step | GRASS | Valhalla |
|---|---|---|
| Walking network (once per OSM extract) | part of the parks phase | 43 s (`00_valhalla_tiles.py`) |
| Parks phase: distance to every junction / expansion and isochrones | 18.5 min | 10.5 min |
| Homes phase: 1.8 million addresses | 4.8 min | 12.1 min |
| Total | 23.4 min | 23.3 min |

Shared: step 1 (green, entrances, network, homes) 7.7 min; step 4 (comparison and figures) 19 min. Delft takes about 1 minute per engine.

## Known issues

- **Water counts as green.** TOP10NL's natuurgebied and nationaal park include large water areas (Haringvliet, Hollands Diep, Voordelta, Oude Maas, Biesbosch). A path along such a shore becomes a park entrance. Next step: cut open water out of the green before finding entrances, then move the web map to this method.
- **300 m2 is far below the WHO's 1 ha.** A run with 1 ha is a filter on the green areas.
- **Public access is not checked** beyond the choice of sources (no duck decoys) and the OSM access tags of the paths. Cemeteries close at night, and some allotment complexes are locked.

## One-time setup

- OSM: `data/raw/osm/zuid-holland-latest.osm.pbf` from [Geofabrik](https://download.geofabrik.de/europe/netherlands/zuid-holland.html). Step 1 turns it into `data/interim/osm/osm_ways_points.gpkg` once.
- GRASS: `grass84.bat` from OSGeo4W, run with `--tmp-project EPSG:28992`; nothing to install.
- Valhalla (the check only): `python -m venv %USERPROFILE%\valhalla-venv` with the OSGeo4W Python (3.12), then `pip install pyvalhalla==3.9.1`, then `00_valhalla_tiles.py`. It calls `valhalla_build_tiles.exe` directly, because `python -m valhalla` cannot find the executables on Windows in 3.9.1.

## In QGIS

`qgis_validation/build_project.py` adds the group "De 300: open methode" to the validation project: the green areas, the green per source and the entrances; the Valhalla isochrones; the walking distance per home from GRASS; and per home GRASS against Valhalla (300 m) and GRASS and Valhalla against FME (5 minutes).

## The earlier FME method

The web map's 300 still comes from an FME workbench with PostGIS and a Valhalla server. The workbenches are in `fme/` (`300_2025 regel.fmw` is the latest); their results are in `data/fme_output/300.gdb` (gitignored, copied from `R:\ESRI\BEHEER\Projecten\Tijdelijk_Roberto\3-30-300\fme output`).

| Input | Dataset | Date |
|---|---|---|
| Parks and woods | `data/fme_input/groenvoorzieningen/groenkaart.gdb`, layer `groen_uit_osm_top10_2024` (OSM + TOP10NL) | 2024 |
| Paths, for the entrances | `data/fme_input/localeversie_osm/gis_osm_roads_free_1.shp` (Geofabrik extract Zuid-Holland) | OSM as of 2025-08-26 |
| Home buildings | SDE table `TOPOGRAFIE.BAG_PAND_PZH`, read during the run | run of 2025-12-09 |
| Walking isochrones | a local Valhalla, called over HTTP | network date not recorded |

`300.gdb` holds `_300regel` and `_300regel_15` (every BAG pand, `_related_suppliers` = 1 when it lies in a 5- or 15-minute isochrone), `isochrones_dissolved(_15)` and `ingang_parken`. `web/build_tiles_30_300.py` reads them for the map; `web/build_tiles_groen.py` reads the parks and woods from `groenkaart.gdb` with the workbench's own selection (no TOP10NL water, at least 300 m2, perimeter / area at most 0.35).

Method and caveats for readers: `web/uitleg/300.html` (Dutch) and `web/uitleg/en/300.html` (English).
