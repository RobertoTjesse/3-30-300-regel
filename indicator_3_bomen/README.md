# The 3: three visible trees from every home

For every 0.5 m cell of the surface model: from how many trees (within 30 m)
is it visible? Every home then gets the maximum in a 1.5 m ring just outside
its facade, and the results are summarised per gemeente, wijk and buurt.
It is open-source Python + GDAL (from a QGIS/OSGeo4W install) and runs for a
whole province (Zuid-Holland: 52 municipalities, 5.2 million trees).

An earlier QGIS/PyQGIS prototype wrote one output raster per tree, which does
not scale to a province. This pipeline adds up the visible-cell counts per DEM
tile and merges once per municipality.

| Document | What |
|---|---|
| this README | how to run it, how it works, known data issues |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | design decisions in depth (§6 observer height, §11 DEM re-export, §14 ArcGIS comparison) |
| [`BENCHMARKS.md`](BENCHMARKS.md) | run times per municipality (generated) |
| [`IMPROVEMENTS.md`](IMPROVEMENTS.md) | what this pipeline improved over the original prototype; the 30, now open source, and the planned open-source 300 |
| [`../WORKLOG.md`](../WORKLOG.md) | what was done and found, step by step |

## Folder

| Path | What |
|---|---|
| `etl/` | the pipeline: `config.py` (all settings), `config_local.py` (this machine, gitignored), stages `01`-`06`, helpers (below) |
| `arcgis_tests/` | experiments, kept locally and not published on GitHub: comparisons with ArcGIS and an exact line-of-sight test, and the corrected ArcGIS benchmark (see [Validation](#validation-against-arcgis)) |
| `sde_reexport/` | one-off re-export of 12 corrupted municipality DEMs from the SDE (`ARCHITECTURE.md` §11), in three steps: `export_from_sde.py` (arcpy, extents from `municipality_extents.csv`), `watch_and_finish.sh` (finishes each raw clip with `gdal_translate` as soon as it is written), `replace_on_share.py` (swaps the files on the share, keeping the originals) |

## Setup

GDAL/OGR comes from a QGIS / OSGeo4W install; nothing is installed with pip.

1. Copy `etl/config_local.example.py` to `etl/config_local.py` (gitignored).
2. Fill in `OSGEO4W_ROOT` (your QGIS/OSGeo4W install), `VIEWANALYSE_DIR`
   (see [Source data](#source-data)) and `MUNICIPALITIES` (`[]` = all).
3. Run the scripts with the Python of that install, e.g.
   `C:\...\OSGeo4W\apps\Python312\python.exe`, from the repository root.

Build two combined sources once (see [Cross-municipality context](#cross-municipality-context)):

```
gdalbuildvrt data/interim/province_dem.vrt "<VIEWANALYSE_DIR>\*.tif"
# and merge every municipality's tree .gpkg into data/interim/province_trees.gpkg
# (gdal.VectorTranslate(); ogr2ogr's CLI mis-quotes some accented/apostrophe names)
```

### BAG buildings, addresses and areas

Stage 4 needs `data/interim/province_buildings.gpkg` (BAG pand) and
`province_addresses.gpkg` (BAG verblijfsobject with *gebruiksdoel* and
*status*): `etl/download_bag_pdok.py` fetches both from PDOK (~25 min,
~1 GB); an SDE export works too (field names: `BAG_*` in `config.py`). Which
address statuses count as lived in: `BAG_STATUSES_IN_USE`. Stage 5 needs
`data/interim/gemeenten.gpkg` and `provincies.gpkg` (PDOK bestuurlijke
gebieden; command in the `05_merge_province.py` docstring). Stage 6 needs
`data/interim/wijken.gpkg` and `buurten.gpkg` (CBS Wijk- en Buurtkaart 2025;
command in the `06_area_summaries.py` docstring).

## Source data

One DEM (`.tif`, AHN DSM *ruw*, 0.5 m, Float32, RD New / EPSG:28992) and one
tree-position layer (`.gpkg`, NEO tree data) per municipality, sharing a
basename (`Papendrecht.tif` / `Papendrecht.gpkg`), in the folder
`VIEWANALYSE_DIR` points to. For Zuid-Holland that is
`R:\ESRI\BEHEER\Projecten\Tijdelijk_Roberto\3-30-300\fme_input\viewanalyse`
(119 GB, read in place, never copied).

The tree layers originally shipped as `.shp` without a spatial index, which
made every tile's bounding-box query scan the whole file (measured ~280x
slower per query than a GeoPackage with its built-in R-tree).

`CORRUPTED_DEM_MUNICIPALITIES` in `config_local.py` is always excluded,
whatever `MUNICIPALITIES` says; it is empty since the 2026-09-07 re-export.

## Pipeline

| Stage | Script | Does |
|---|---|---|
| Extract | `etl/01_tile_dem.py` | Per municipality: splits the DEM into tiles with a buffer halo, reading pixels from `config.PROVINCE_DEM_VRT` (not the municipality's own .tif), so a tile near a municipal boundary still gets its neighbour's terrain. Writes `tile_index.json` (buffered *and* inner extent per tile). Stops on integer or scaled DEMs. |
| Transform | `etl/02_compute_viewsheds.py` | Per tile: reads the trees within the *buffered* extent from `config.PROVINCE_TREES_GPKG`, sets each tree's observer height, runs `gdal.ViewshedGenerate` per tree, accumulates the counts, crops to the *inner* window. Parallel across tiles. |
| Load | `etl/03_merge_tiles.py` | Per municipality: mosaics the tiles via a VRT into one Cloud-Optimized GeoTIFF, `data/processed/<name>_viewshed.tif`. |
| Score | `etl/04_score_buildings.py` | Every residential building (BAG address in use with *woonfunctie*) gets the maximum count in a 1.5 m ring outside its facade: `<name>_woningen.gpkg`. Inside a footprint the surface is the roof, so the ring gives street/garden-level eye height instead. |
| Province | `etl/05_merge_province.py` | Merges all `<name>_woningen.gpkg` (overlapping DEM rectangles) into `ZuidHolland_woningen.gpkg`, each building once and in its current municipality, plus `ZuidHolland_samenvatting.csv`. |
| Areas | `etl/06_area_summaries.py` | Assigns every building to its CBS buurt and wijk: `ZuidHolland_gebieden.gpkg` (gemeenten, wijken, buurten with the figures) and CSVs; checks its gemeente totals against stage 5. |

Run in order, from the repository root:

```
python indicator_3_bomen/etl/01_tile_dem.py
python indicator_3_bomen/etl/02_compute_viewsheds.py --workers 4 --resume
python indicator_3_bomen/etl/03_merge_tiles.py
python indicator_3_bomen/etl/04_score_buildings.py
python indicator_3_bomen/etl/05_merge_province.py
python indicator_3_bomen/etl/06_area_summaries.py
```

Or stages 1-3 one municipality at a time, with a progress line per
municipality and the QGIS project updated after each:
`indicator_3_bomen/etl/run_all_municipalities.sh [name ...]` (Git Bash). It
deletes a municipality's intermediate tiles once its output is written
(province-wide they would need ~250 GB); they are kept when a stage fails or
logs tile errors, or with `KEEP_TILES=1`.

All settings (radius, tile size, workers, heights) are in `etl/config.py`.
The helpers in `etl/` are `download_bag_pdok.py` (BAG from PDOK),
`run_all_municipalities.sh` with `print_municipality_summary.py` (stages 1-3
per municipality), `add_tree_heights.py <municipality>` (writes every tree's
canopy top, local ground, height and observer offset as used by stage 2 to
`data/processed/<name>_tree_heights.gpkg`, for checking in QGIS), and
`generate_benchmark_report.py`, which turns `logs/benchmark.csv` into
`BENCHMARKS.md`; the main run times are also on the wiki page
[Benchmarks and hardware](https://github.com/RobertoTjesse/3-30-300-regel/wiki/Benchmarks-and-hardware).

### Why halo-then-crop

A tree just inside one tile can see a cell just inside the neighbouring
tile, within and across municipal boundaries. So DEM cells and trees are
read over the buffered extent (boundary trees are processed by both
neighbours, intentionally), but only the non-overlapping inner window is
written, and the tiles fit edge to edge without seams.

### Cross-municipality context

- `PROVINCE_DEM_VRT` (`data/interim/province_dem.vrt`): a `gdalbuildvrt`
  mosaic of every municipality's DEM, so a tile's halo past the municipal
  edge gets the neighbour's real terrain instead of a hard edge.
- `PROVINCE_TREES_GPKG` (`data/interim/province_trees.gpkg`): all tree
  layers in one, so a neighbour's tree within 30 m still counts.

`TILE_BUFFER_PX` (35 m) exceeds the 30 m radius, so no extra buffer is needed.

### Which trees count

The NEO tree registry records a status per point (`current_st`). Since
2026-10-08 stage 2 leaves out the trees marked as disappeared or small,
1,275,676 of the 5.2 million points in Zuid-Holland (24.5%):

| `current_st` | trees | counted |
|---|---:|---|
| seen | 3,891,042 | yes |
| disappeared, small tree | 1,116,245 | no |
| not seen once, small tree | 159,431 | no |
| (empty) | 24,972 | yes |
| not seen once | 13,934 | yes |

The list is `TREE_STATUSES_EXCLUDED` in `config.py`; an empty tuple counts
every point again. The filter is an attribute filter on the tree query, so
`province_trees.gpkg` stays complete. `add_tree_heights.py` uses the same
filter.

### Per-tree observer height

Each tree's observer sits at its canopy top: the maximum surface value
within `TREE_HEIGHT_BUFFER_RADIUS` (1.5 m) of the tree point, ignoring
building pixels, passed to `ViewshedGenerate` as an offset on the tree's own
cell. The exact tree point would put the observer inside its own crown,
which then blocks its view (`ARCHITECTURE.md` §6). A tree falls back to
`OBSERVER_HEIGHT` when its height above local ground (canopy top minus the
lowest surface within 5 m) is not above 0 or exceeds 35 m: the DSM has no
point classification, so a power line or pylon is only caught by that
plausibility check. Optional own-crown removal (`OWN_CROWN_RADIUS`, off by
default) flattens the tree's own crown first.

## Validation against ArcGIS

The ArcGIS benchmark for Delft had its observers at twice the intended height; with the intended observer, ArcGIS and GDAL agree. The full comparison is in the wiki ([Validation against ArcGIS](https://github.com/RobertoTjesse/3-30-300-regel/wiki/Validation-against-ArcGIS)) and in `ARCHITECTURE.md` §14. The ArcGIS scripts are run by hand from the ArcGIS Pro Python Command Prompt, with Pro closed.

## Known issues

See the wiki page [Known issues](https://github.com/RobertoTjesse/3-30-300-regel/wiki/Known-issues).
