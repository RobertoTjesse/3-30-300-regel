# The 300: green within 300 m of every home

Does every home lie within a 5-minute walk (about 300-425 m over the street network) of a park or wood of at least 300 m2? The 300 is still made with FME, PostGIS and a pedestrian isochrone calculator (Valhalla). This folder holds the workbenches; the results are in `data/fme_output/` (gitignored, copied from `R:\ESRI\BEHEER\Projecten\Tijdelijk_Roberto\3-30-300\fme output`).

| File | What |
|---|---|
| `fme/300_2025 regel.fmw` | latest version of the rule |
| `fme/300 regel.fmw`, `fme/300_2024.fmw`, `fme/300_2025.fmw` | earlier versions |

The inputs of `300_2025 regel.fmw` (the readers that are switched on):

| Input | Dataset | Date |
|---|---|---|
| Parks and woods | `data/fme_input/groenvoorzieningen/groenkaart.gdb`, layer `groen_uit_osm_top10_2024` (OSM + TOP10NL) | 2024 |
| Paths, for the entrances | `data/fme_input/localeversie_osm/gis_osm_roads_free_1.shp` (Geofabrik extract Zuid-Holland) | OSM as of 2025-08-26 |
| Home buildings | SDE table `TOPOGRAFIE.BAG_PAND_PZH` (connection "Topografie"), read during the run | run of 2025-12-09 |
| Walking isochrones | a local Valhalla, called over HTTP | network date not recorded |

A PostGIS OSM reader (connection `osm_reader_20250509`, one test municipality) is switched off. This version does not read the copied `gemeentes/` or the building input `panden` (8.8 GB, on R:).

## Results used downstream

- `data/fme_output/300.gdb` holds `_300regel` and `_300regel_15` (every BAG pand, with `_related_suppliers` = 1 when it lies in a 5- or 15-minute isochrone from an entrance), `isochrones_dissolved(_15)` and `ingang_parken`. `web/build_tiles_30_300.py` reads them and writes the zones and entrances to `web/data/looptijd.pmtiles`.
- The green itself is not in the results. To show the parks and woods on the map (`web/data/groen.pmtiles`), `web/build_tiles_groen.py` reads them from the input `groenkaart.gdb` with the workbench's own selection: its reader's `bron <> 'top 10 water'` and its Tester's `_area >= 300` and `Shape_Length / Shape_Area <= 0.35`.

Method and caveats: `web/uitleg/300.html` (Dutch) and `web/uitleg/en/300.html` (English).

## The open pipeline (in development)

`etl/` computes the 300 without FME: a walking distance in metres from every home to the nearest park entrance, with two routing engines so their results can be compared. Tested on Delft; the map still shows the FME result.

| Step | Script | What |
|---|---|---|
| 0 | `etl/00_valhalla_tiles.py` | Valhalla's walking network from the OSM extract, with `valhalla/graph_fietspad.lua`: Valhalla's own tag rules with cycle paths open to pedestrians. Once per extract |
| 1 | `etl/01_prepare.py` | Parks (`PARKS_PATH` in `etl/config.py`), park entrances, the walkable OSM network (cut at shared OSM nodes) and the BAG homes of the 3, for `MUNICIPALITIES` plus a margin |
| 2 | `etl/02_valhalla.py` | Valhalla expansion from every entrance, Valhalla's own snapping of every home (`locate`), and the classic isochrone polygons for comparison. Calls `etl/valhalla_worker.py` in the pyvalhalla environment |
| 3 | `etl/03_grass.py` | GRASS `v.net.distance` from all entrances at once, on the network of step 1 |
| 4 | `etl/04_compare.py` | Both engines, the isochrones and the old FME result per home; summary CSV, scatter plot and a map of where they disagree |
| | `etl/explainer_expansion.py` | The pictures on the explanation page: expansion from the Provinciehuis, isochrone against expansion, and a cycle-path example |

Parks: layer `parken_vlakken` of the provincial voorzieningen geodatabase (OpenStreetMap and TOP10NL, peildatum 17 September 2025), copied to `data/raw/voorzieningen/voorzieningen.gdb` (gitignored). Parks under 300 m2 and strips narrower than about 6 m (perimeter / area > 0.35) are left out.

Cycle paths count as walking routes in both engines: Dutch traffic law (RVV 1990, art. 4) lets pedestrians use one where there is no footpath or pavement. Bridleways do not count.

Entrances: where a walkable way crosses the park edge, OSM gates and entrances on the edge, and for parks with neither, points every 25 m along the edge where a walkable way runs within 10 m.

Run from the repository root with the OSGeo4W Python, after setting `PARKS_*` and `MUNICIPALITIES` (in `etl/config_local_300.py`, gitignored, or in `config.py`):

```
python indicator_300_park/etl/00_valhalla_tiles.py
python indicator_300_park/etl/01_prepare.py
python indicator_300_park/etl/02_valhalla.py
python indicator_300_park/etl/03_grass.py
python indicator_300_park/etl/04_compare.py
```

For the province the work comes in two phases. `02` and `03` with `--parks-only` do the parks: entrances, expansion, isochrones and the distance to every junction. With `--homes-only` they add the homes later, reusing that work. `02` and `03` also take `--reuse` to skip the routing when its output is newer than step 1's. `GRASS_EXCLUDE=cycleway` runs GRASS (and `04`) without cycle paths, for comparison; the outputs get the suffix `_zonder_cycleway`.

One-time setup:

- OSM: `data/raw/osm/zuid-holland-latest.osm.pbf` from [Geofabrik](https://download.geofabrik.de/europe/netherlands/zuid-holland.html). Step 1 turns it into `data/interim/osm/osm_ways_points.gpkg` once.
- Valhalla: `python -m venv %USERPROFILE%\valhalla-venv` with the OSGeo4W Python (3.12), then `pip install pyvalhalla==3.9.1`. Step 0 builds the tiles (Zuid-Holland: under a minute, 161 MB). It calls `valhalla_build_tiles.exe` directly, because `python -m valhalla` cannot find the executables on Windows in 3.9.1.
- GRASS: `grass84.bat` from OSGeo4W, run with `--tmp-project EPSG:28992`; nothing to install.

Delft (55,123 homes, old green layer), share within 300 m: GRASS 91.5%, Valhalla expansion 91.4%, Valhalla isochrones 96.7%. The engines agree on 98% of homes; the distance differs by 10 m or less for 71% and by 50 m or less for 94%. What is left comes mainly from private roads: Valhalla can reach a home over its own private road, while the GRASS network leaves `access=private` out. With Valhalla's default profile (cycle paths closed), only 87.0% was within 300 m. Run times for Delft: step 0 45 s, step 1 16 s, step 2 4 min, step 3 1 min.
