# The 3: visible trees

Does every home see at least three trees?

Here a tree counts as visible from a point when there is a free line of sight from the top of its crown to that point at 1.8 m (eye height), within 30 m. A home sees N trees when somewhere just in front of its facade (within 1.5 m outside the building) there is a spot that sees N trees.

The 3 is open source: Python and GDAL, in [`indicator_3_bomen/`](https://github.com/RobertoTjesse/3-30-300-regel/tree/master/indicator_3_bomen).

## Pipeline

| Stage | Script | What it does |
|---|---|---|
| 1 Extract | `01_tile_dem.py` | Cuts each municipality's DEM into 500 m tiles with a 35 m halo. It reads the pixels from the province mosaic, so a tile at a municipal boundary gets its neighbour's terrain. Small gaps are interpolated and large water is set to the tile's lowest value. It stops on integer or scaled DEMs. |
| 2 Transform | `02_compute_viewsheds.py` | Per tile, every tree in the buffered extent gets its observer at its canopy top and a GDAL `ViewshedGenerate` run of 30 m. The visible cells are counted and the result is cropped to the inner tile. Tiles run in parallel. |
| 3 Load | `03_merge_tiles.py` | Mosaics the tiles into one Cloud-Optimized GeoTIFF per municipality, `<name>_viewshed.tif`. |
| 4 Score | `04_score_buildings.py` | Gives every residential building the maximum count in a 1.5 m ring outside its facade: `<name>_woningen.gpkg`. |
| 5 Province | `05_merge_province.py` | Merges the municipalities, with each building once and in its current municipality, and writes the summary per gemeente. |
| 6 Areas | `06_area_summaries.py` | Assigns every building to its CBS buurt and wijk (2025) and summarises per gemeente, wijk and buurt. |

## Choices and why

| Choice | Why |
|---|---|
| Radius 30 m | The rule gives no distance. 30 m is the radius of the earlier 3 analysis, and at that distance a tree is still clearly recognisable as a tree. |
| Raw surface model (AHN4 DSM, 0.5 m) | Buildings, hedges and crowns have to be able to block the view. In a terrain model everything is open. |
| Observer at the canopy top | This is the highest surface point within 1.5 m of the tree, ignoring roofs. At the exact tree point the observer sits inside its own crown, which blocks its view: in Delft, 35.5% of the trees then saw almost nothing. |
| Height check of 0-35 m above local ground | The DSM has no point classification, so a power line or roof edge could be taken for a crown. Implausible trees (fewer than 1 in 80,000) get a fixed 1.7 m instead. |
| Target height 1.8 m | The eye height of a standing person. |
| Score = maximum in a 1.5 m ring around the facade | Inside the footprint the surface model is the roof. The maximum, because the rule asks whether you see trees from your home, not from every window. |
| Home = BAG address in use with a woonfunctie | Homes above shops count too. Addresses still to be built do not. |
| Classes 0, 1-2, 3-5, 6-7, 8+ | 3 is the rule itself; the other classes show how far below or above it a home is. |
| Halo of 35 m around each tile | It must be at least the 30 m radius, or cells at tile edges miss the neighbour's trees. Without it, seams showed in 10-12% of the area. |

## Tiles and accumulation

The original prototype wrote one raster per tree against the whole municipality DEM, which took about 60 hours for Delft. This pipeline adds up the visible-cell counts in memory per 500 m tile and writes once per tile. All 52 municipalities take about 16 hours on 4 cores, for 13.5 million per-tree viewsheds (a tree near a tile edge is computed by every tile it reaches). Run times per municipality are in [`BENCHMARKS.md`](https://github.com/RobertoTjesse/3-30-300-regel/blob/master/indicator_3_bomen/BENCHMARKS.md) and on [Benchmarks and hardware](benchmarks.md).

## Settings

All in `indicator_3_bomen/etl/config.py`:

| Setting | Value |
|---|---|
| `MAX_DISTANCE` | 30 m |
| `TREE_HEIGHT_BUFFER_RADIUS` | 1.5 m (canopy-top search) |
| `TREE_GROUND_SEARCH_RADIUS` | 5 m (local ground, for the height check) |
| `TREE_HEIGHT_MAX_PLAUSIBLE` | 35 m |
| `OBSERVER_HEIGHT` | 1.7 m (fallback) |
| `TARGET_HEIGHT` | 1.8 m |
| `FACADE_RING_M` | 1.5 m |
| `TILE_PIXELS` / `TILE_BUFFER_PX` | 1000 px (500 m) / 70 px (35 m) |
| `OWN_CROWN_RADIUS` | 0, off (removing the tree's own crown changed the verdict for only 0.1% of Delft's buildings) |

Every design decision is explained in [`ARCHITECTURE.md`](https://github.com/RobertoTjesse/3-30-300-regel/blob/master/indicator_3_bomen/ARCHITECTURE.md).
