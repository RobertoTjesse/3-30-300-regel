# Benchmarks and hardware

How long each part of the analysis takes, on which machine, and how much memory and disk it needs. Measured run times come from `logs/benchmark.csv` (written by the pipeline of the 3), the run logs and the [work log](https://github.com/RobertoTjesse/3-30-300-regel/blob/master/WORKLOG.md); the per-municipality table of the 3 is generated in [`BENCHMARKS.md`](https://github.com/RobertoTjesse/3-30-300-regel/blob/master/indicator_3_bomen/BENCHMARKS.md).

## The machine

All runs below were made on one Windows workstation, a virtual machine:

| | |
|---|---|
| Type | VMware virtual machine (VMware7,1) |
| CPU | Intel Xeon Platinum 8380 @ 2.30 GHz, 6 cores / 6 threads |
| Memory | 64 GB RAM |
| Disks | two virtual SSDs of 100 GB: `C:` (system, 5 GB free) and `D:` (repository and data, 18 GB free on 2026-10-04) |
| Network share | `R:`: the elevation models and trees per municipality (119 GB), read in place, never copied |
| OS | Windows 11 Enterprise (10.0.22631) |
| Software | QGIS LTR / OSGeo4W: Python 3.12.13, GDAL 3.12.2, NumPy 2.4.2; ArcGIS Pro 3.6.1 (comparisons only); FME (the 30 and the 300) |

Free disk space is the tightest limit: large intermediate files (a province-wide clip of a raster, all viewshed tiles at once) do not fit, so every step works in tiles and deletes its intermediates. Province-wide, the viewshed tiles of the 3 would need ~250 GB; `run_all_municipalities.sh` therefore deletes a municipality's tiles once its result is written.

## The 3: visible trees

Settings: tiles of 500 m (1000 px) with a 35 m halo, 4 worker processes (`NUM_WORKERS`), radius 30 m; see [The 3 - visible trees](the-3.md).

Full province run, 27-28 September 2026 (Zuid-Holland, 52 municipalities): from 21:03 to 05:45, 8 h 42 min wall clock, as two queues of municipalities side by side. Process time per stage (latest run per municipality):

| Stage | Total | Median per municipality | Fastest | Slowest | Volume |
|---|---:|---:|---|---|---|
| 1. Tile the DEM (`01_tile_dem.py`) | 7.8 h | 250 s | Brielle | Goeree-Overflakkee, 60 min | 28,723 tiles |
| 2. Viewsheds (`02_compute_viewsheds.py`) | 2.4 h | 101 s | Hillegom, 25 s | Rotterdam, 21 min | 13,547,122 tree viewsheds |
| 3. Merge tiles (`03_merge_tiles.py`) | 0.85 h | 26 s | Papendrecht, 7 s | Rotterdam, 6.6 min | one raster per municipality |
| 4. Score homes (`04_score_buildings.py`) | 1.1 h | 38 s | Zoeterwoude, 11 s | Zuidplas, 22 min | 978,428 residential buildings |
| 5–6. Province and areas | not logged | | | | 50 gemeenten, 518 wijken, 2,509 buurten |

- Tiling takes longer than the viewsheds. It reads the elevation model from the network share and fills its holes (water, under bridges); which of the two costs most was not measured separately. Goeree-Overflakkee is the largest municipality (4,410 tiles, much of it water).
- The 13.5 million viewsheds are more than the 5.2 million trees: a tree near a tile edge is computed in every tile whose halo it falls in.
- Viewsheds run at roughly 1,000 trees per second per municipality with 4 workers (the per-municipality figures are in `BENCHMARKS.md`).

Input downloads (`download_bag_pdok.py`, PDOK WFS, 204 blocks of 5 km per layer): BAG buildings 2.2 million in 10 min, addresses 2.3 million in 12 min.

## The 30: canopy cover from BKB 2024

`indicator_30_kroonbedekking/etl/bkb_per_gebied.py` reads the BKB 2024 canopy raster (Friedenau Society, lidar; the Netherlands at 0.25 m, 1.9 trillion pixels, 9.2 GB compressed) in tiles of 1 km.

| Measurement | Result |
|---|---|
| Reading one 1 km × 1 km window (4000 × 4000 px, 16 MB) | 0.04-0.06 s |
| Delft, 91 buurten, 48 tiles, 5 workers | 3 s |
| Zuid-Holland, 2,509 buurten, 6,225 tiles, 5 workers | 3.6 min (counting 185 s; fetching the CBS land area once and writing the rest) |
| Memory per worker | ~100 MB (by design: 16 MB raster window, 64 MB area grid, masks) |

The whole raster would be 1.9 TB in memory, so it is never read at once.

The FME version of the 30 (NEO crowns, SQL Server) was run once by a colleague; its run time was not recorded.

## The 300: walking distance to green

Made with FME, PostGIS and a local Valhalla isochrone calculator (27,111 entrances, 5- and 15-minute isochrones); run times were not recorded. See [The 300 - green within walking distance](the-300.md).

## The web map tiles

| Script | Output | Time |
|---|---|---|
| `web/build_tiles_30_300.py` | `30-300.pmtiles` (87 MB): writing the tiles | 6 min (plus reading 970,529 buildings and summarising per area beforehand) |
| | `looptijd.pmtiles` (7.5 MB) | 20 s |
| `web/build_tiles_groen.py` | `groen.pmtiles` (6.9 MB, 20,973 parks and woods) | a few minutes |
| `web/build_tiles.py` | `3.pmtiles` (85 MB) | not logged |

Each tile file must stay under GitHub's 100 MB file limit. When the green was still part of `30-300.pmtiles` (zoom 11–16), writing took 16 min and the file grew to 117 MB; that is why the parks and woods, and later the walking zones, got files of their own.

## ArcGIS (arcpy) against GDAL

A controlled speed test with ArcGIS's classic Viewshed tool and GDAL on the same inputs, three study areas and 1-6 processes is on its own page: [Speed test ArcGIS against GDAL](speedtest.md). GDAL was 41 to 50 times faster, comparing the fastest setup of each, with the same verdict for 99.4-100% of the homes.

Earlier, less controlled ArcGIS runs on Delft (different elevation model and observer, see [Validation against ArcGIS](validation-arcgis.md)): Visibility over all of Delft in one call was stopped after 2.5 hours; in 500 m tiles with 5 processes it took about 55 minutes, against 32 min 24 s for the pipeline's stages 1-3 with 4 workers. The original prototype's approach (one raster per tree) reportedly took about 60 hours for Delft in ArcPy.

## Tips

- Tiling (stage 1) reads the 119 GB of elevation models from the network share; a local copy would probably speed it up, if the disk space is there.
- Keep 20+ GB free on the data disk; the scripts work in tiles to stay within memory, not within disk.
- On a machine with more cores, raise `NUM_WORKERS` (the 3) or `--workers` (the 30); both scale per tile.
