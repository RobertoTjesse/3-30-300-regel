# Improvements over the original prototype

What changed between the original QGIS/PyQGIS prototype (`bomen_extract.fmw`
/ `QGIS/Script.py` on the network share) and this pipeline, and why each
change mattered. For how the pipeline works today, see `ARCHITECTURE.md`.
For current timing numbers, see `BENCHMARKS.md`.

The original script's approach: for every tree, call QGIS's `gdal:viewshed`
Processing algorithm against the **entire municipality DEM**, and write the
result as a **separate GeoTIFF file**. Reported real-world cost: roughly 60
hours to process Delft via the ArcPy equivalent of the same approach. This
pipeline does Delft (87,837 trees, 180,200 per-tree viewsheds including
the tile halos) in about half an hour, and all 52 municipalities of the
province in about 16 hours on 4 cores (`BENCHMARKS.md`) — a difference
explained by architecture, not a smarter algorithm (it's the same
`ViewshedGenerate` underneath).

## Performance-critical changes

1. **Accumulate instead of write-per-tree.** The original wrote one output
   raster per tree — hundreds of thousands of files for a whole city, most
   of the cost being file creation/write overhead, not the actual viewshed
   math. This pipeline accumulates every tree's contribution into an
   in-memory array and writes to disk once per *tile* (thousands of trees
   at once).

2. **A small tile window instead of the full DEM, per viewshed call.** The
   original ran the viewshed tool against the entire municipality raster
   for every single tree, regardless of city size — for Delft, a ~700MB
   file opened and processed once per tree. This pipeline pre-splits the
   DEM into ~570m tiles (500m inner + 35m buffer), so every
   `ViewshedGenerate()` call only ever touches a small window, independent
   of municipality size.

3. **GeoPackage + spatial index instead of bare shapefiles.** The original
   tree layers were plain `.shp` files with no spatial index — a bounding-box
   query against an unindexed shapefile scans close to the entire file.
   This pipeline queries per-tile, so unindexed cost scaled with
   `tile_count x total_features`, not the size of each query's actual
   result. Measured: ~280x slower per query without an index vs. the same
   data in a GeoPackage (which has a built-in R-tree). This was the single
   largest fix for runtime on the bigger municipalities — throughput
   dropped disproportionately as tile count grew until this was fixed.

4. **Direct in-process GDAL calls instead of a framework/tool invocation
   per tree.** QGIS's Processing framework (and, per the 60-hour ArcPy
   figure, ArcPy's tool invocation) carries meaningful per-call overhead —
   validation, environment setup, sometimes a subprocess spawn — charged on
   every single tree. This pipeline calls `gdal.ViewshedGenerate()` as a
   plain C-API function inside an already-running Python process, in a
   tight loop.

5. **Parallelism.** See its own section below — the original had none.

## Parallelism — exactly what is and isn't parallel

It's worth being precise here, since the parallelism is narrower in scope
than it might sound:

**Parallelized**: `02_compute_viewsheds.py` (the dominant cost) processes
one municipality's tiles concurrently via
`ProcessPoolExecutor(max_workers=NUM_WORKERS)` (`NUM_WORKERS=4`). One tile
= one unit of work handed to one worker **process** — deliberately
processes, not threads, since viewshed computation is CPU-bound and a
process sidesteps Python's GIL entirely rather than depending on GDAL
happening to release it internally.

**Not parallelized**:
- **Within a tile**: trees are processed one at a time in a plain Python
  loop calling `ViewshedGenerate()` sequentially. The unit of parallelism
  is the tile, not the tree.
- **`01_tile_dem.py`**: writes tiles one at a time via `gdal.Translate()`.
- **`03_merge_tiles.py`**: one VRT build + one `gdal.Translate()` call,
  sequential.
- **Across municipalities**: `run_all_municipalities.sh` processes
  municipalities strictly one at a time. This was a deliberate choice made
  partway through this project, once per-municipality (rather than
  per-*stage*) progress visibility was needed — running all 52
  municipalities through stage 1, then all through stage 2, then all
  through stage 3 (the original approach) is also not inter-municipality
  parallel, just organized by stage instead of by municipality.

**Not explored**: whether 4 workers is actually optimal for a given
machine, or whether a higher worker count helps further. `NUM_WORKERS=4`
is a config default, not a benchmarked/tuned value.

## Correctness fixes required by tiling

Splitting work into tiles (the performance fix in §2 above) introduces
seam problems that a naive, non-tiled implementation like the original
script never had to solve. These were found and fixed during this project:

1. **The halo-then-crop pattern.** A tree near a tile's edge can still
   affect pixels in the *neighbouring* tile within `MAX_DISTANCE`.
   Querying trees and writing output within a tile's own footprint only
   left a visible seam artefact at every internal tile boundary,
   undercounting a regular grid covering roughly 10-12% of every
   municipality's area. Fixed by querying over a buffered "halo" extent
   (trees near the edge are legitimately processed by both neighbouring
   tiles) but only ever writing the non-overlapping *inner* window to disk.

2. **Viewshed result placement.** `gdal.ViewshedGenerate()` returns a small
   window centred on the observer (sized by `MAX_DISTANCE`), not a raster
   the size of the input tile. Code that assumed otherwise caused a numpy
   broadcast error on every tree, silently dropping all tree contributions
   for a period before being caught. Fixed by pasting the small result
   into the tile's accumulator at the pixel offset implied by comparing
   the two geotransforms.

3. **NoData=0 conflation.** Early output flagged pixel value `0` as
   NoData. But "zero trees within 30m" is a real, common, meaningful
   answer (most of any municipality) — flagging it as missing data broke
   `ComputeStatistics()` and would make GIS tools render large legitimate
   areas as blank. Fixed by not setting a NoData value on output at all.

4. **CRS validation.** Added an explicit check comparing the DEM's and
   tree layer's CRS before processing a municipality. Without it, a
   missing/mismatched `.prj` makes OGR's spatial filter silently match
   zero features — producing an all-zero municipality output
   indistinguishable from "genuinely no trees here," which would go
   unnoticed in an unattended multi-municipality run.

## Extending the halo-crop pattern across municipality boundaries

The same seam problem in §1 above applies at municipality boundaries, not
just tile boundaries within one municipality. Rather than teach every
script about municipal adjacency, this pipeline builds two combined,
province-wide sources once (`PROVINCE_DEM_VRT`, `PROVINCE_TREES_GPKG` — VRT
mosaics/merges, not physical data duplication) and points the existing
per-tile logic at them instead of at one municipality's own files. A tile
near a municipality's edge now reads real neighbour context instead of
hitting a hard clamp. See `ARCHITECTURE.md` §8 for the mechanics.

## Other improvements

- **COG driver instead of GTiff + separate `BuildOverviews()`.** The
  two-step approach produced a file GDAL itself flags as having broken
  Cloud-Optimized-GeoTIFF layout (overviews appended after the full-res
  data instead of before it). Using GDAL's dedicated `COG` format target
  builds overviews as part of the same write, correctly laid out.
- **Internally tiled TIFF output (512x512 blocks) instead of strip
  organization.** Several original source DEMs are strip-organized (one
  block per full-width scanline), which massively amplifies the cost of
  the many small windowed reads this pipeline does per municipality.
  Every raster this pipeline produces is written `TILED=YES`; the
  corrected SDE re-exports (see below) apply the same standard.
- **Variable per-tree height sampled from the DEM** (the max value within
  a 1.5m radius, approximating canopy top on the source surface model)
  instead of one flat constant for every tree, with a plausibility clamp
  (35m) guarding against the raster's lack of point classification (no
  way to distinguish a power line or pylon from a tree canopy in raw
  elevation values alone).

## Correctness fix: input DEM NoData not honored by the viewshed algorithm

**Status: fixed 2026-09-08.** `gdal.ViewshedGenerate()` treats a NoData
sentinel in the *input* DEM as literal terrain elevation — GDAL's own docs
say it does "no special processing of input cells at a nodata value." A
gap in the source raster (water, bridges, or a municipality's own padding
around its real coverage — see the 12-corrupted-DEM item below) that a
tree's 30m radius happened to touch could silently produce a phantom cliff
or mountain, with no error from the pipeline. Fixed in `01_tile_dem.py`:
every tile now runs through `gdal.FillNodata()` immediately after being
cut, before any viewshed call ever sees it.

Getting this right took three passes. The first fill used
`maxSearchDist=300px` (150m): 2 of Delft's 208 tiles had gaps up to
~265x140px that went unfilled while the NoData flag was cleared anyway,
leaving `-9999` disguised as real elevation. The second interpolated
everything with `maxSearchDist=2000px` — correct, but hours of work on
coastal municipalities with large water areas. The current fill (since
2026-09-27) interpolates small gaps up to 50 px (25 m) and sets whatever is
left (open water) to the tile's lowest value, a flat surface that can never
block a line of sight; a tile with no valid pixel at all keeps its NoData
flag. All 52 municipalities were re-tiled with it in the full run of
2026-09-27/28. Details: `ARCHITECTURE.md` §13.

## Methodology change: target (eye/window) height

`TARGET_HEIGHT` changed from `0.0` (ground level) to `1.8` (eye/window
height), matching a reference ArcGIS Pro methodology for the same analysis
that raises every evaluated surface cell before the line-of-sight test
rather than testing strictly at ground level. On Delft this raised the
mean visible-tree count from 3.25 to 4.68 — a large, expected shift in the
direction of "eye-height clears more small ground obstructions," not a
bug. A separate issue found at the same time — `observerHeight` was fed as
an absolute elevation while `ViewshedGenerate` adds it to the DEM's own
value at the observer's pixel, placing observers roughly 1.4-2x too high —
was fixed on 2026-09-27 (`ARCHITECTURE.md` §6).

## Not a code change, but the highest-stakes catch of the project

**12 of 52 municipality source DEMs were found to be 0-3.4% real elevation
data — the rest exactly `0.0`, with no NoData flag set.** A flat/zero DEM
looks to the viewshed algorithm like perfectly unobstructed terrain, so the
pipeline ran to completion, reported zero errors, and produced
plausible-looking output for all 12 — this failure mode was invisible from
the output alone; only direct pixel inspection revealed it. Root cause:
whatever process produced these particular `fme_input` files exported
void/gap areas as literal `0.0` rather than a flagged NoData value. Fixed
by re-exporting the affected extents from the authoritative source
(`Geo_raster.TOPOGRAFIE.AHN4_05M_RUW`, an SDE raster) — see
`ARCHITECTURE.md` §11 and `sde_reexport/` for the full process. This is the
kind of error that no amount of pipeline-code correctness would have
caught on its own; it required deliberately checking the *input* data,
not just verifying the pipeline ran without errors.

## Planned: the 30 and the 300 as open source (future work)

The 3 is fully in this repository (Python + GDAL). The 30 and the 300 are
still FME workbenches (`indicator_30_kroonbedekking/fme/30_2024.fmw`, the
version behind the map, and `indicator_300_park/fme/300_2025 regel.fmw`)
reading a SQL Server tree-crown table and a PostGIS road network, so others
can read but not rerun them; their results enter the map through
`web/build_tiles_30_300.py`. The goal is the same pipeline shape for all
three, so any province can repeat the whole rule:

- **30** — known bug in the FME version: it looks up each area in
  `GRENZEN.CBS_WIJKKAART_2022_VERSIE3` by its 2023 code, so the 114 buurten and 19
  wijken with new codes in 2023 (all of Voorne aan Zee, 28 buurten in Schiedam, ...)
  get no crowns at all, and areas whose boundary moved are counted over the 2022
  polygon but divided by the 2023 land area. BOMEN_KRONEN itself is complete.
- **30** (`etl/30_canopy.py`): clip the crown polygons to each area instead
  of counting every crown that touches it (removes the double counting on
  boundaries; one buurt now reaches 282%), sum, divide by the CBS land area,
  directly on the map's gemeenten/wijken/buurten 2025. Open alternative for
  the purchased NEO crowns: derive crowns from AHN (height above ground) or
  the classified AHN point cloud.
- **300** (`etl/300_walk.py`): green from OSM + TOP10NL with the same filters
  (>= 300 m2, perimeter/area <= 0.35) and an option for the WHO's 1 ha;
  entrances = walkable OSM ways crossing the green edge (GDAL); 5 / 15 minute
  pedestrian isochrones from a local Valhalla (already open source; its
  matrix API could replace one request per entrance); classify BAG homes
  from the same BAG download as the 3.
- The 3 also for schools and workplaces, as the paper asks.

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
the 300 come from FME and are only joined in `web/build_tiles_30_300.py`. A
fixed set of values per building makes the three rules comparable and
allows a combined result, as the Yggdrasil handbook and Cobra Groeninzicht
do (`docs/COMPARISON_COBRA.md`). Proposed fields in `<Province>_woningen.gpkg`:

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
| `loop_300_min` | walking minutes to the nearest entrance of green >= the chosen size (needs the Python 300 with times, not only 5/15-minute zones) |
| `voldoet_3`, `voldoet_30`, `voldoet_300` | 0/1 per rule |
| `regels_voldaan` | 0–3, the number of rules met (the handbook's simple combined score) |
| `cijfer_3`, `cijfer_30`, `cijfer_300`, `cijfer_totaal` | optional mark 0–10, 6 = just meets the rule, on Cobra's scale so results can be compared: 30 = % / 5; 300 = 10 − 0.02 × (metres − 100); 3 = 0→1, 1→3, 2→5, 3→6, 4→7, 5→8, 6→9, 7+→10. Total with equal weights by default (Cobra: 0.25 / 0.5 / 0.25), the weights in `config.py` |

Per area, report shares of homes, not means: the share meeting each rule,
the share meeting all three, and the distribution of `regels_voldaan`. A
mean mark lets a good 300 make up for a poor 30, which is not how the rule
is meant (each number is a minimum). The map would get a fourth button "3-30-300"
coloured by `regels_voldaan`.
