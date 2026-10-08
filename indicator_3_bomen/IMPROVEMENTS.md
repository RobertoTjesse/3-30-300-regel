# Improvements over the original prototype

What changed between the original QGIS/PyQGIS prototype (`bomen_extract.fmw` / `QGIS/Script.py` on the network share) and this pipeline, and why each change mattered. For how the pipeline works today, see `ARCHITECTURE.md`; for current timings, `BENCHMARKS.md` and the documentation site's [Benchmarks and hardware](https://robertotjesse.github.io/3-30-300-regel/docs/benchmarks/).

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

12 of the 52 municipality source DEMs turned out to hold only 0-3.4% real elevation data; the rest was exactly `0.0`, without a NoData flag. To the viewshed algorithm a flat zero surface is open terrain, so the pipeline finished, reported no errors and produced plausible-looking output for all 12. Only looking at the pixels themselves showed the problem. Whatever made these `fme_input` files had exported empty areas as `0.0` instead of NoData. The fix was to re-export those extents from the authoritative source, `Geo_raster.TOPOGRAFIE.AHN4_05M_RUW` (an SDE raster); see `ARCHITECTURE.md` §11 and `sde_reexport/`. No amount of correct pipeline code would have caught this: it took checking the input data itself, beyond checking that the pipeline ran without errors.

## The 30 and the 300 as open source

The 3 is in this repository (Python + GDAL), and since 2026-10-04 so is the 30 (`indicator_30_kroonbedekking/etl/`). It computes canopy cover from the BKB 2024 crown raster (Friedenau Society, lidar, 0.25 m), assigns every pixel to the buurt that holds its centre, and works directly on the map's gemeenten, wijken and buurten 2025. It replaced the FME workbench (`indicator_30_kroonbedekking/fme/30_2024.fmw`), which looked up 2023 codes in the 2022 CBS map (so 114 buurten and 19 wijken got no crowns) and counted a crown in full in every buurt it touched (one buurt reached 282%).

The 300 is still an FME workbench (`indicator_300_park/fme/300_2025 regel.fmw`) that reads a PostGIS road network, so others can read it but not rerun it. The aim is the same pipeline shape for all three, so any province can repeat the whole rule:

- 300 (`etl/300_walk.py`): green from OSM + TOP10NL with the same filters (>= 300 m2, perimeter/area <= 0.35) and an option for the WHO's 1 ha; entrances where walkable OSM ways cross the edge of the green (GDAL); 5- and 15-minute pedestrian isochrones from a local Valhalla (already open source; its matrix API could replace one request per entrance); BAG homes classified from the same BAG download as the 3.
- The 3 also for schools and workplaces, as the paper asks.
