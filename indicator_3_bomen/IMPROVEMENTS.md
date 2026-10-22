# Improvements over the original prototype

What changed between the original QGIS/PyQGIS prototype (`bomen_extract.fmw` / `QGIS/Script.py` on the network share) and this pipeline, and why each change mattered. For how the pipeline works today, see `ARCHITECTURE.md`; for current timings, `BENCHMARKS.md` and the wiki page [Benchmarks and hardware](https://github.com/RobertoTjesse/3-30-300-regel/wiki/Benchmarks-and-hardware).

The original script called QGIS's `gdal:viewshed` Processing algorithm for every tree against the entire municipality DEM, and wrote each result as a separate GeoTIFF file. The ArcPy equivalent of the same approach reportedly took about 60 hours for Delft. This pipeline does Delft (87,837 trees, 180,200 per-tree viewsheds including the tile halos) in about half an hour, and all 52 municipalities of the province in about 16 hours on 4 cores (`BENCHMARKS.md`). The viewshed function underneath is the same (`ViewshedGenerate`), so the difference comes from how the work is organised.

## Changes for speed

1. Accumulate instead of writing one raster per tree. The original wrote one output raster per tree, hundreds of thousands of files for a whole city, and most of the cost was creating and writing files rather than the viewshed itself. This pipeline adds every tree's result into an array in memory and writes to disk once per tile, for thousands of trees at once.

2. A small tile window instead of the full DEM for each viewshed call. The original ran the viewshed tool on the entire municipality raster for every tree, whatever the size of the city; for Delft that meant opening and processing a ~700 MB file once per tree. This pipeline first splits the DEM into tiles of about 570 m (500 m inner plus a 35 m buffer), so every `ViewshedGenerate()` call touches a small window, independent of the size of the municipality.

3. GeoPackage with a spatial index instead of bare shapefiles. The original tree layers were `.shp` files without a spatial index, and a bounding-box query on an unindexed shapefile scans almost the whole file. Because this pipeline queries once per tile, that cost grew with `tile_count x total_features` instead of with the number of trees a query returns. Measured: about 280x slower per query than the same data in a GeoPackage, which has a built-in R-tree. This was the largest single runtime fix for the bigger municipalities, whose throughput fell off as their tile count grew.

4. Direct GDAL calls instead of a tool invocation per tree. QGIS's Processing framework, and judging by the 60-hour figure also ArcPy's tool invocation, adds overhead to every call: validation, environment setup, sometimes a new subprocess. The original paid that for every tree. This pipeline calls `gdal.ViewshedGenerate()` as a plain C-API function inside a running Python process, in a tight loop.

5. Parallelism, which the original did not have; see the next section.

## What is and is not parallel

The parallelism is narrower than it might sound.

Parallel: `02_compute_viewsheds.py`, the largest cost, processes one municipality's tiles at the same time with `ProcessPoolExecutor(max_workers=NUM_WORKERS)` (`NUM_WORKERS=4`). One tile is one unit of work for one worker process. These are processes and not threads on purpose: the viewshed computation is CPU-bound, and a separate process avoids Python's GIL instead of depending on GDAL to release it.

Not parallel:
- Within a tile, the trees are processed one at a time in a plain Python loop that calls `ViewshedGenerate()`. The unit of parallelism is the tile, not the tree.
- `01_tile_dem.py` writes tiles one at a time with `gdal.Translate()`.
- `03_merge_tiles.py` is one VRT build and one `gdal.Translate()` call.
- Across municipalities, `run_all_municipalities.sh` handles one municipality at a time. That was chosen partway through the project, when progress per municipality (instead of per stage) became important. Running all 52 municipalities through stage 1, then through stage 2, then through stage 3 (the earlier approach) was not parallel across municipalities either, only organised by stage.

Not explored: whether 4 workers is the best number for a given machine. `NUM_WORKERS=4` is a default, not a measured optimum.

## Fixes that tiling required

Splitting the work into tiles (change 2 above) creates seam problems that an untiled implementation like the original never had. These were found and fixed during the project:

1. The halo-then-crop pattern. A tree near a tile's edge can still affect pixels in the neighbouring tile, up to `MAX_DISTANCE` away. Querying trees and writing output only within each tile's own footprint left a visible seam at every internal tile boundary, an undercount on a regular grid covering about 10-12% of every municipality. The fix: query over a buffered "halo" extent (so trees near an edge are processed by both neighbouring tiles, on purpose) but write only the non-overlapping inner window to disk.

2. Placing the viewshed result. `gdal.ViewshedGenerate()` returns a small window centred on the observer, sized by `MAX_DISTANCE`, not a raster the size of the input tile. Code that assumed otherwise raised a numpy broadcast error on every tree and dropped all tree contributions for a while before it was caught. The fix pastes the small result into the tile's accumulator at the pixel offset that follows from the two geotransforms.

3. NoData = 0. Early output flagged the value `0` as NoData. But "no tree within 30 m" is a real and common answer (most of any municipality), and flagging it as missing broke `ComputeStatistics()` and would make GIS tools draw large legitimate areas as blank. The output now has no NoData value at all.

4. CRS check. The pipeline now compares the CRS of the DEM and the tree layer before processing a municipality. Without that check, a missing or mismatched `.prj` makes OGR's spatial filter match zero features. The result is an all-zero municipality that looks the same as "there are really no trees here", which would go unnoticed in an unattended run over many municipalities.

## The halo across municipality boundaries

The seam problem of fix 1 also occurs at municipality boundaries. Rather than teach every script which municipalities are neighbours, the pipeline builds two province-wide sources once, `PROVINCE_DEM_VRT` and `PROVINCE_TREES_GPKG` (a VRT mosaic and a merged GeoPackage, without copying the elevation data), and points the per-tile logic at them instead of at one municipality's own files. A tile near a municipal edge now reads the neighbour's real terrain and trees instead of stopping at a hard edge. See `ARCHITECTURE.md` §8.

## Other improvements

- The COG driver instead of GTiff followed by `BuildOverviews()`. The two-step approach produced a file that GDAL itself flags as a broken Cloud-Optimized GeoTIFF, with the overviews after the full-resolution data instead of before it. GDAL's `COG` format builds the overviews in the same write, in the right place.
- Internally tiled TIFF output (512x512 blocks) instead of strips. Several original source DEMs store one block per full-width scanline, which makes the many small windowed reads of this pipeline much more expensive. Every raster the pipeline writes is `TILED=YES`, and the corrected SDE re-exports (below) follow the same rule.
- A height per tree, taken from the DEM (the maximum within 1.5 m, approximating the canopy top on the surface model), instead of one constant for every tree. A plausibility limit of 35 m covers the raster's lack of point classification: the raw elevation values cannot tell a power line or pylon from a tree canopy.

## Fix: the viewshed algorithm ignored NoData in the input DEM

Fixed on 2026-09-08. `gdal.ViewshedGenerate()` treats a NoData value in the input DEM as a real elevation; GDAL's documentation says it does "no special processing of input cells at a nodata value". A gap in the source raster (water, bridges, or a municipality's own padding around its real coverage, see the 12 corrupted DEMs below) within a tree's 30 m radius could produce a phantom cliff or mountain without any error. `01_tile_dem.py` now runs every tile through `gdal.FillNodata()` right after cutting it, before any viewshed sees it.

It took three attempts to get this right. The first fill used `maxSearchDist=300px` (150 m): 2 of Delft's 208 tiles had gaps of up to ~265x140 px that stayed unfilled while the NoData flag was cleared anyway, which left `-9999` looking like real elevation. The second interpolated everything with `maxSearchDist=2000px`, which was correct but took hours on coastal municipalities with large water areas. The current fill (since 2026-09-27) interpolates small gaps up to 50 px (25 m) and sets what remains (open water) to the tile's lowest value, a flat surface that can never block a line of sight; a tile without any valid pixel keeps its NoData flag. All 52 municipalities were re-tiled with it in the full run of 2026-09-27/28. Details: `ARCHITECTURE.md` §13.

## Method change: target (eye) height

`TARGET_HEIGHT` changed from `0.0` (ground level) to `1.8` (eye height), as in the reference ArcGIS Pro method for the same analysis, which raises every evaluated cell before the line-of-sight test. On Delft this raised the mean number of visible trees from 3.25 to 4.68, an expected shift because an eye at 1.8 m looks over more small obstacles on the ground. A separate problem found at the same time was fixed on 2026-09-27 (`ARCHITECTURE.md` §6): `observerHeight` had been passed as an absolute elevation, while `ViewshedGenerate` adds it to the DEM value at the observer's pixel, which put observers roughly 1.4-2x too high.

## The input check that mattered most

12 of the 52 municipality source DEMs turned out to hold only 0-3.4% real elevation data; the rest was exactly `0.0`, without a NoData flag. To the viewshed algorithm a flat zero surface is open terrain, so the pipeline finished, reported no errors and produced plausible-looking output for all 12. Only looking at the pixels themselves showed the problem. Whatever made these input files had exported empty areas as `0.0` instead of NoData. The fix was to re-export those extents from the authoritative source, the AHN surface model in the SDE raster store; see `ARCHITECTURE.md` §11 and `sde_reexport/`. No amount of correct pipeline code would have caught this: it took checking the input data itself, beyond checking that the pipeline ran without errors.

## The 30 and the 300 as open source

The 3 is in this repository (Python + GDAL), and since 2026-10-04 so is the 30 (`indicator_30_kroonbedekking/etl/`). Since 2026-10-19 it measures the canopy cover from the BKB 2024 crown raster (Friedenau Society, lidar, 0.25 m) on land within 500 m of every home's facade, and builds the buurten, wijken and gemeenten 2025 up from their homes. It replaced the earlier calculation, which looked up 2023 codes in the 2022 CBS map (so 114 buurten and 19 wijken got no crowns) and counted a crown in full in every buurt it touched (one buurt reached 282%).

Since 2026-10-13 the 300 has an open pipeline too (`indicator_300_park/etl/`), on the same OSGeo4W installation: GDAL finds the park entrances and builds the walking network from OpenStreetMap, and GRASS `v.net.distance` gives every home a walking distance in metres to the nearest entrance, using the same BAG addresses as the 3. Valhalla computes the same distances as a check; the two agree for 98% of homes. It replaces the earlier calculation, which needed a PostGIS road network and a Valhalla server and only gave inside or outside a 5- or 15-minute zone. What is left before the map switches over: cutting open water out of the green (see `indicator_300_park/README.md`).

## Planned work

The one list of planned work for all three; the sections below work out the larger items.

- The 3 per floor for multi-storey buildings (below).
- Values per home and a mark, including the number of rules met (below).
- The 3 also for schools and workplaces, as the paper asks.
- The 300 with the WHO threshold of 1 ha, as a filter on the same green areas.
- The 300: green areas without an entrance ([issue #3](https://github.com/RobertoTjesse/3-30-300-regel/issues/3)).

## Planned: multi-storey buildings (future work)

The 3 is measured at eye height (`TARGET_HEIGHT` = 1.8 m) in a ring just
outside the facade, so every home in a flat gets the street-level view.
From higher floors you look over hedges, fences, cars and lower crowns, and
you see trees the street does not. The opposite also happens: a young tree
seen from the 10th floor is a crown far below, not a view of a tree. One DSM
surface cannot express this, but the facade ring can be lifted per floor.

- **Floors per building**: 3D BAG (`b3_bouwlagen`, roof heights
  `b3_h_dak_*`), from the 3D BAG download (the QGIS project only shows it as
  WMS and 3D Tiles). Floor height ~3 m; window
  height per floor `k` = 1.8 + 3k m above ground.
- **Visibility per floor**: `ViewshedGenerate` takes one `targetHeight`
  per call, so run stage 02 once per height level (1.8, 4.8, 7.8, ... m,
  capped at e.g. 10 floors) into one accumulator per level, or test the
  facade points of tall buildings directly with the exact line-of-sight test
  used for validation (cheaper: only buildings with 3+ floors need it). The
  ring cells lie on the ground in front of the facade, so "height above the
  DSM" there is height above the street, as it should be.
- **Which floor a home is on**: the BAG has no floor number. Spread a
  building's homes evenly over its residential floors, or use house number
  additions where they encode floors (`-1`, `-2`, `bis`) as a check. Shops on
  the ground floor (non-residential addresses) move homes up.
- **Data to check first**: how many homes are in buildings with 3+ floors in
  Zuid-Holland, and how much the score changes for them on one test buurt
  (Delft Poptahof or Rotterdam Ommoord) before a full run.

## Planned: values per home and a mark (future work)

Today a building has `bomen_zichtbaar` and `klasse` for the 3; the 30 and
the 300 are only joined in `web/build_tiles_30_300.py`. A
fixed set of values per building makes the three rules comparable and
allows a combined result, as the Yggdrasil handbook and Cobra Groeninzicht
do (wiki page "Comparison with Cobra Groeninzicht"). Proposed fields in `<Province>_woningen.gpkg`:

| Field | Meaning |
|---|---|
| `pand_id`, `n_woningen` | as now |
| `n_bouwlagen` | floors (3D BAG); NULL when unknown |
| `bomen_3` | visible trees, best place in the facade ring (as now `bomen_zichtbaar`) |
| `bomen_3_groot` | the same, counting only crowns >= 28 m² (handbook, Cobra) |
| `bomen_3_per_laag` | visible trees per floor, e.g. `"4;6;9"`; NULL for 1–2 floors |
| `woningen_3` | homes that see >= 3 trees, summed over floors (homes spread over floors) |
| `kroon_30_buurt` | canopy % of the buurt (the paper's neighbourhood) |
| `kroon_30_500m` | canopy % of the land within 500 m of the building (handbook, Cobra), for comparison |
| `loop_300_m` | walking distance in metres to the nearest entrance of green >= the chosen size (`gr_m` from `indicator_300_park/etl/03_grass.py`) |
| `voldoet_3`, `voldoet_30`, `voldoet_300` | 0/1 per rule |
| `regels_voldaan` | 0–3, the number of rules met (the handbook's simple combined score) |
| `cijfer_3`, `cijfer_30`, `cijfer_300`, `cijfer_totaal` | optional mark 0–10, 6 = just meets the rule, on Cobra's scale so results can be compared: 30 = % / 5; 300 = 10 − 0.02 × (metres − 100); 3 = 0→1, 1→3, 2→5, 3→6, 4→7, 5→8, 6→9, 7+→10. Total with equal weights by default (Cobra: 0.25 / 0.5 / 0.25), the weights in `config.py` |

Per area, report shares of homes, not means: the share meeting each rule,
the share meeting all three, and the distribution of `regels_voldaan`. A
mean mark lets a good 300 make up for a poor 30, which is not how the rule
is meant (each number is a minimum). The map would get a fourth button "3-30-300"
coloured by `regels_voldaan`.
