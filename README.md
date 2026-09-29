# 3-30-300-regel — the 3-30-300 rule per home, buurt, wijk and gemeente

The 3-30-300 rule (Konijnendijk 2023): 3 trees visible from every home, 30%
tree canopy in every neighbourhood, 300 m to the nearest park. This repository
computes the **3** for a whole province (Zuid-Holland first) and publishes it
with the **30** and the **300** as one web map:
**https://robertotjesse.github.io/3-30-300-regel/** — with explanation pages in
Dutch (`/uitleg/`). To run it for another province, see
[Another province](#another-province). The 30 and 300 are still made with FME
(inputs to `web/build_tiles_30_300.py`); moving them into this repository as
open-source Python is planned (`IMPROVEMENTS.md`).

The 3: for every pixel of a municipality's DEM, how many trees is it visible
from within a 30 m radius; every home gets the maximum just outside its facade.
(History: this repository started as `RobertoTjesse/3-regel`.)

This replaces an earlier QGIS/PyQGIS prototype which wrote one output raster
**per individual tree** — infeasible at province scale (millions of trees).
Instead this pipeline accumulates visible-pixel counts per DEM tile and
merges once per municipality.

Recent work, step by step: [`WORKLOG.md`](WORKLOG.md).

## Setup

GDAL/OGR comes from a **QGIS / OSGeo4W install**, not pip. Machine-specific
settings (where that install lives, where the source data lives, which
municipalities to process) are kept out of the tracked config:

1. Copy `etl/config_local.example.py` to `etl/config_local.py`.
2. Fill in `OSGEO4W_ROOT` (your QGIS/OSGeo4W install) and `VIEWANALYSE_DIR`
   (see "Source data" below).
3. `etl/config_local.py` is gitignored — it never gets committed, so real
   paths are safe there.

Run scripts with the same Python that has access to that OSGeo4W
`site-packages` (or from an OSGeo4W shell).

Before running the pipeline, build two combined sources once (see
"Cross-municipality context" below):

```
gdalbuildvrt data/interim/province_dem.vrt "<VIEWANALYSE_DIR>\*.tif"
# then merge every municipality's tree .gpkg into one combined file —
# see git history for the exact commands used (ogr2ogr's CLI has a
# filename-quoting bug with some accented/apostrophe basenames; the Python
# API's gdal.VectorTranslate() was used as a workaround for those).
```

### BAG buildings, addresses and municipal boundaries

Stage 4 needs `data/interim/province_buildings.gpkg` (BAG pand) and
`province_addresses.gpkg` (BAG verblijfsobject with *gebruiksdoel* and
*status*). `etl/download_bag_pdok.py` fetches both from PDOK (~25 min, ~1 GB);
an SDE export works too — field names are set in `config.py` (`BAG_*`).
Which address statuses count as lived in: `BAG_STATUSES_IN_USE`. Stage 5
needs `data/interim/gemeenten.gpkg` and `provincies.gpkg` from PDOK's
*bestuurlijkegebieden* WFS (command in the `05_merge_province.py` docstring).
Stage 6 needs `data/interim/wijken.gpkg` and `buurten.gpkg`: CBS Wijk- en
Buurtkaart 2025 from PDOK (command in the `06_area_summaries.py` docstring).

## Source data

One DEM (`.tif`, 0.5 m RD New / EPSG:28992) + one tree-position layer
(`.gpkg`) per municipality, sharing a basename (e.g. `Papendrecht.tif` /
`Papendrecht.gpkg`), pointed at by `VIEWANALYSE_DIR` in your
`config_local.py`.

The tree layers originally shipped as `.shp` with no spatial index, which
made every tile's bounding-box query scan the *entire* file — cost that
scales with tile-count × total-features, and got dramatically worse on
bigger municipalities (measured ~280x slower per query on an unindexed
file vs. one converted to GeoPackage, which has a built-in R-tree index).

Some municipality DEMs are tens of gigabytes, so the pipeline reads
directly from wherever `VIEWANALYSE_DIR` points — nothing is copied locally
or committed to git.

`MUNICIPALITIES` in `config_local.py` restricts which municipalities are
processed; `[]` means every municipality found. `CORRUPTED_DEM_MUNICIPALITIES`
is always excluded regardless of `MUNICIPALITIES` — see "Known data issues".

## Pipeline (ETL)

| Stage | Script | Does |
|---|---|---|
| Extract | `etl/01_tile_dem.py` | Per municipality: splits its DEM into tiles with a buffer halo on each side, reading pixel data from `config.PROVINCE_DEM_VRT` (not the municipality's own .tif) so a tile near a municipality edge still gets real neighbour context. Writes `tile_index.json` (each tile's buffered *and* inner extents). |
| Transform | `etl/02_compute_viewsheds.py` | Per municipality, per tile: reads trees from `config.PROVINCE_TREES_GPKG` within the tile's *buffered* extent (so a neighbour-owned tree near any boundary is still counted), samples each tree's height from the DEM, runs `gdal.ViewshedGenerate`, accumulates visible-pixel counts, then crops the result down to the tile's non-overlapping *inner* window before writing. Parallel across tiles. |
| Load | `etl/03_merge_tiles.py` | Per municipality: mosaics all (non-overlapping) tile results via a VRT and translates to one Cloud-Optimized GeoTIFF (COG) per municipality. |
| Score | `etl/04_score_buildings.py` | Per municipality: every residential building (BAG address in use with *woonfunctie*) gets the max viewshed value in a 1.5 m ring outside its facade → `<name>_woningen.gpkg`. Inside a footprint the surface model is the roof, so roof pixels mean "1.8 m above the roof" — the ring gives street/garden-level eye height instead. |
| Province | `etl/05_merge_province.py` | Merges all `<name>_woningen.gpkg` (which overlap: each covers its DEM rectangle) into `ZuidHolland_woningen.gpkg`, each building once, assigned to its *current* municipality, plus `ZuidHolland_samenvatting.csv` per municipality. |
| Areas | `etl/06_area_summaries.py` | Assigns every building to its CBS buurt (and so wijk) and writes the same summary per gemeente, wijk and buurt: `ZuidHolland_gebieden.gpkg` (polygons with the figures), `ZuidHolland_wijken.csv`, `ZuidHolland_buurten.csv`. Checks its gemeente figures against stage 5. |

Run in order:

```
python etl/01_tile_dem.py
python etl/02_compute_viewsheds.py --workers 4 --resume
python etl/03_merge_tiles.py
python etl/04_score_buildings.py
python etl/05_merge_province.py
python etl/06_area_summaries.py
```

Or run one municipality fully (all three stages) at a time with
`etl/run_all_municipalities.sh [name1 name2 ...]` — useful for getting a
per-municipality progress signal on a long multi-municipality run, since the
three scripts above each process *every* municipality for that one stage
before moving to the next stage.

The runner also deletes each municipality's intermediate tiles
(`dem_tiles/<name>`, `viewshed_tiles/<name>`) once its final output is
written — province-wide they'd need ~250 GB, and `01_tile_dem.py` reuses
any tile it finds, so leftovers from an older run could leak into a newer
one. Tiles are kept if any stage fails or stage 2 logs tile errors; set
`KEEP_TILES=1` to keep them regardless.

### Why the halo-then-crop step matters

A tree (or terrain feature) just inside one tile's boundary can still be
within the 30 m viewshed radius of a pixel just inside the *neighbouring*
tile — and the same is true across municipality boundaries, not just tile
boundaries within one municipality. Querying only within an inner extent
(and mosaicking full/unclipped tiles) would leave seam artefacts at every
boundary — a real bug caught during review, see git history. The fix: query
DEM pixels and trees over the buffered extent (each boundary tree/pixel gets
processed by both neighbours, which is intentional), but only ever write the
non-overlapping inner window to disk, so tiles fit together edge-to-edge
with nothing for the final mosaic to get wrong.

### Cross-municipality context

`config.PROVINCE_DEM_VRT` and `config.PROVINCE_TREES_GPKG` (both under
`data/interim/`, gitignored — rebuild locally) extend that same halo-then-crop
approach across municipality boundaries, not just tile boundaries within one
municipality:

- `PROVINCE_DEM_VRT`: a `gdalbuildvrt` mosaic of every municipality's DEM.
  `01_tile_dem.py` reads pixel data from this instead of the individual
  municipality `.tif`, so a tile whose buffer extends past this
  municipality's own raster edge still gets real elevation data from the
  neighbour, rather than a hard edge.
- `PROVINCE_TREES_GPKG`: every municipality's tree GeoPackage merged into
  one. `02_compute_viewsheds.py` queries this instead of a single
  municipality's own tree layer, so a tree owned by the neighbouring
  municipality but within `MAX_DISTANCE` of this side still contributes.

`TILE_BUFFER_PX` (35 m) already exceeds the required 30 m, so no separate
buffer constant is needed for the municipality-boundary case.

### Per-tree height

Each tree's observer is placed at its canopy top: `_prepare_tree()` in
`02_compute_viewsheds.py` takes the max DEM value within
`TREE_HEIGHT_BUFFER_RADIUS` (1.5 m) of the tree and passes it minus the DEM
value at the tree's own pixel, since `ViewshedGenerate`'s observer height is
an offset on top of that pixel, not an absolute elevation. (The exact tree
point, as in the reference ArcGIS method, puts the observer inside its own
crown, which then blocks its view — see `ARCHITECTURE.md` §6.) Optional
own-crown removal (`OWN_CROWN_RADIUS`, off by default) flattens the tree's
own crown before its viewshed; it needs building footprints
(`data/interim/province_buildings.gpkg`) so buildings are never flattened.
Falls back to `OBSERVER_HEIGHT` if the sample is out of
bounds, or if the tree's height above local ground (canopy top minus the
lowest surface value within `TREE_GROUND_SEARCH_RADIUS`, 5 m — judged on
height above ground, not absolute NAP, since the province spans ~-6 m to
~+40 m NAP) is not above 0 or exceeds `TREE_HEIGHT_MAX_PLAUSIBLE` (35 m) — the
source raster carries no point classification, so there's no way to tell a
power line, pylon, or building corner apart from a tree canopy in the raw
elevation values; the clamp catches the height-plausibility half of that
(revisit once AHN's classified point cloud, which does distinguish wires
from vegetation, is incorporated instead of the derived raster).

## QGIS validation project

`qgis_validation/build_project.py` creates (or updates)
`qgis_validation/3-regel_validation.qgz` with everything the pipeline has
produced, for visual checking:

- **Viewshed (aantal zichtbare bomen)** — every
  `data/processed/<name>_viewshed.tif`: 0 red, 1-2 orange, 3-5 green,
  6-7 darker green, 8+ dark green.
- **Bomen** — all trees (`province_trees.gpkg`, only drawn when zoomed in
  past 1:10,000) and each `<name>_tree_heights.gpkg`.
- **3D BAG** — LoD2.2 buildings as WMS (2D) and as 3D Tiles (for QGIS's 3D
  map view).
- **Achtergrond** — PDOK BRT grijs (WMTS) and PDOK luchtfoto (WMS).
- **Experimenten** — anything placed in `data/processed/experiments/`
  (rasters get the viewshed styling, vectors an outline; off by default).
- **Eén boom / boomgroep** — the one-tree and tree-group comparison
  (`arcgis_tests/one_tree.py`), one subgroup per case: each result as the
  number of the case's trees that see a cell, the benchmark
  (`visibility_Delft`) and pipeline results cut out around the trees, a
  difference map per result against the exact line-of-sight test (orange =
  counts more trees, blue = fewer), the cells within 30 m of other trees
  (grey, not compared), the trees with their 30 m circles, and the DEM.

`run_all_municipalities.sh` runs it after every municipality. Re-runs only
*add* new layers, so styling or other changes made in QGIS are kept;
delete the `.qgz` to rebuild from scratch. It needs QGIS's own Python:

```
C:\...\OSGeo4W\bin\python-qgis-ltr.bat qgis_validation\build_project.py
```

The `.qgz` is gitignored (generated, machine-specific data behind it); the
script is tracked.

## Web map

Public map: https://robertotjesse.github.io/3-30-300-regel/ — the 3, the 30
and the 300 behind a 3 / 30 / 300 switch, all on the same areas: gemeenten
(zoom < 10), CBS wijken 2025 (< 11.5), CBS buurten 2025 (< 13), then every
residential building (3 and 300; the 30 stays on buurten). Sources and data
dates behind the (i) button, PDOK grey basemap, address search (PDOK
Locatieserver, filtered to the province). Explanation pages in Dutch — method,
every choice and why, the Konijnendijk (2023) paper, and how to repeat it for
another province — are under `web/uitleg/` (site: `/uitleg/`).

`web/` is the site as published:

| File | What |
|---|---|
| `index.html` | the map (MapLibre + PMTiles); settings (`PROVINCE`, `REPO`, colours, texts) at the top of the `<script>` |
| `uitleg/*.html`, `uitleg/uitleg.css` | explanation pages |
| `data/3.pmtiles` | the 3 — `python web\build_tiles.py` (after `06_area_summaries.py`) |
| `data/30-300.pmtiles` | the 30 and the 300 — `python web\build_tiles_30_300.py` |
| `build_tiles*.py`, `serve.py` | build scripts and a local preview server (not published) |

`web/data/` is gitignored; each tile file must stay under GitHub's 100 MB
file limit (now ~85 and ~94 MB).

`build_tiles_30_300.py` reads the FME results (`config.FME_OUTPUT_DIR`:
`30_regel_v2.gdb`, `300.gdb`) and the areas of the 3
(`<Province>_gebieden.gpkg`). The 30 was computed per CBS buurt 2023 (sum of
the NEO crowns touching the buurt / land area); crown and land area are carried
over to the 2025 areas in proportion to overlapping area. Buurten without crown
data (all of Voorne aan Zee; 28 buurten in Schiedam, some in Westland, Leiderdorp, Pijnacker-Nootdorp and Delft — not a gap in the crown table: the FME query looks up each area in GRENZEN.CBS_WIJKKAART_2022_VERSIE3 by its 2023 code, and 114 of the 115 empty buurten have codes that are new in 2023) are left out, so such areas show "geen gegevens".
The 300 is recounted per area from the buildings' walking class.

Preview and publish:

```
python web\serve.py              # http://localhost:8000 (supports Range requests, as PMTiles needs)
```

then copy `web/` without the `.py` files to the `gh-pages` branch (plus an
empty `.nojekyll`). GitHub Pages serves that branch.

## Another province

Everything province-specific is a setting or an input file; no code changes.

1. **Inputs per municipality** in one folder (`VIEWANALYSE_DIR`): `<name>.tif`
   (AHN4 DSM 0.5 m, *ruw*, Float32, RD New) and `<name>.gpkg` (tree points,
   RD New). Build `province_dem.vrt` and `province_trees.gpkg` (see "Setup").
2. **`etl/config_local.py`**: `PROVINCE` (exactly as in PDOK's bestuurlijke
   gebieden, e.g. `"Utrecht"`), `VIEWANALYSE_DIR`, `OSGEO4W_ROOT`,
   `MUNICIPALITIES = []`, and `FME_OUTPUT_DIR` if you have 30/300 results.
   Output files are named after the province (`config.PROVINCE_SLUG`, e.g.
   `Utrecht_woningen.gpkg`).
3. **BAG**: `python etl\download_bag_pdok.py` (blocks follow the extents of
   your DEMs). **Gemeenten/provincies**: command in `05_merge_province.py`.
   **CBS wijken/buurten**: command in `06_area_summaries.py` — replace its
   `spatFilter` with your province's extent.
4. **Run** `etl/01` … `06`, then `web\build_tiles.py`.
5. **The 30 and 300** still need FME results for that province (the future
   open-source version is sketched in `IMPROVEMENTS.md`); without them, publish
   the 3 only.
6. **Map**: set `PROVINCE` and `REPO` at the top of the script in
   `web/index.html` (and the texts/sources if your data differ). The map opens
   on the extent of `data/3.pmtiles` by itself.
7. **Publish** `web/` as described above.

The explanation pages describe Zuid-Holland's run (figures, data dates); adapt
the numbers there.

## Data layout

```
data/
  raw/          # local scratch only — the real source lives wherever VIEWANALYSE_DIR points
  interim/
    province_dem.vrt                     # combined DEM mosaic, gitignored — see "Cross-municipality context"
    province_trees.gpkg                  # combined tree layer, gitignored
    dem_tiles/<municipality>/            # generated by stage 1, gitignored
    viewshed_tiles/<municipality>/       # generated by stage 2, gitignored
  processed/
    <municipality>_viewshed.tif          # final merged COG output, gitignored
logs/           # run logs + logs/benchmark.csv, gitignored
```

Merging uses GDAL VRTs (lightweight references to the source tiles) instead
of physically copying data, keeping disk and git usage small — only the
pipeline code and config are tracked.

All tunables (viewshed radius, tile size, worker count, output dtype) live
in `etl/config.py`; machine-specific paths live in `etl/config_local.py`.
`etl/generate_benchmark_report.py` turns `logs/benchmark.csv` (populated
automatically as the pipeline runs) into `BENCHMARKS.md`.

## Known data issues

- **[OPEN]** Tree status: 21% of the trees the pipeline counts are marked
  "disappeared, small tree" in the source registry (`current_st`), 3% "not
  seen once". Whether they should be excluded is unclear —
  [issue #1](https://github.com/RobertoTjesse/3-regel/issues/1).
- **[OPEN]** ArcGIS comparison: ArcGIS's Visibility tool sees clearly more
  trees than this pipeline on identical inputs (Viewshed2 is much closer);
  the remaining Visibility variants could not be run because the tool
  fails on ArcGIS Pro 3.6.1 here — `ARCHITECTURE.md` §14,
  [issue #2](https://github.com/RobertoTjesse/3-regel/issues/2).
- DEMs must be **floating point** (AHN: Float32). `01_tile_dem.py` checks
  the province VRT and every source behind it before tiling and stops if
  one is stored as integers (heights truncated to whole metres — happened
  once in an AHN5 export) or carries a scale/offset (e.g. centimetre
  integers), which the pipeline would not apply.
- **[RESOLVED 2026-09-07]** 12 of 52 municipality DEMs (`Barendrecht`,
  `Dordrecht`, `Goeree-Overflakkee`, `Gorinchem`, `Hardinxveld-Giessendam`,
  `Hellevoetsluis`, `Hendrik-Ido-Ambacht`, `Hoeksche Waard`, `Nissewaard`,
  `Papendrecht`, `Sliedrecht`, `Zwijndrecht`) were found to be 0-3.4% real
  elevation data, the rest exactly zero with no NoData flag — invisible
  from the output alone, since a flat/zero DEM just makes the viewshed
  algorithm treat it as unobstructed terrain (plausible-looking output,
  effectively "trees within 30m" rather than real terrain-based
  visibility). Root cause: whatever process produced these `fme_input`
  files exported void areas as literal `0.0`. Fixed by re-exporting the
  affected extents from the authoritative source
  (`Geo_raster.TOPOGRAFIE.AHN4_05M_RUW`, an SDE raster) — see
  `sde_reexport/` and `ARCHITECTURE.md` §11 for the full process. Verified
  on R:\ (real elevation data, correct NoData=-9999). No longer excluded in
  `config_local.py`.
- Output pixel value `0` means "no tree within 30 m," not "no data" — no
  NoData value is set, intentionally, so GIS tools render it correctly.
- Large municipality DEMs observed to be strip-organized rather than
  internally tiled (e.g. Rotterdam), which means the many small windowed
  reads in stage 1 pull more data off disk than a tiled source would
  require.
- Tree height is sampled from a DSM-like surface raster with no point
  classification (see "Per-tree height" above) — a power line or pylon near
  a tree can't be distinguished from canopy except by the plausibility
  clamp. Revisit if AHN's classified point cloud becomes available.
