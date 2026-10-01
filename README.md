# 3-30-300-regel — the 3-30-300 rule per home, buurt, wijk and gemeente

*Nederlands: [README.nl.md](README.nl.md)*

The **3-30-300 rule** (Konijnendijk, 2023) asks for green in three ways:

| | Rule | Measured here as | How it is made | Folder |
|---|---|---|---|---|
| **3** | 3 trees visible from every home | trees visible from just outside the facade (30 m radius, AHN surface model) | Python + GDAL, this repository | [`indicator_3_bomen/`](indicator_3_bomen/) |
| **30** | 30% tree canopy in every neighbourhood | crown area (NEO) / land area per CBS buurt | FME (workbenches in this repository) | [`indicator_30_kroonbedekking/`](indicator_30_kroonbedekking/) |
| **300** | a park within 300 m of every home | home within a 5-minute walk (Valhalla isochrone) of a park or wood >= 300 m2 | FME + PostGIS + Valhalla | [`indicator_300_park/`](indicator_300_park/) |

All three are computed for the province of **Zuid-Holland** and published
together as one web map with a 3 / 30 / 300 switch, per gemeente, wijk,
buurt and home: **https://robertotjesse.github.io/3-30-300-regel/**, with
explanation pages. The site is in Dutch, with an English version
([map](https://robertotjesse.github.io/3-30-300-regel/?lang=en),
`/uitleg/en/`). The 3 can be run for another
province without code changes ([Another province](#another-province)); the
30 and 300 still depend on FME, and moving them to open-source Python is
planned (`indicator_3_bomen/IMPROVEMENTS.md`).

## Repository layout

```
330300regel/
├── README.md                     this file: the project as a whole
├── README.nl.md                  the same in Dutch
├── WORKLOG.md                    what was done and found, step by step
├── indicator_3_bomen/            the 3 — full pipeline, see its README
│   ├── etl/                      stages 01-06, config.py, config_local.py (gitignored)
│   ├── arcgis_tests/             experiments: ArcGIS comparisons, exact test (local only, not on GitHub)
│   ├── sde_reexport/             one-off re-export of corrupted DEMs
│   ├── fme/                      the original FME workbenches for the 3
│   └── README.md, ARCHITECTURE.md, BENCHMARKS.md, IMPROVEMENTS.md
├── indicator_30_kroonbedekking/  the 30 — FME workbenches + README
├── indicator_300_park/           the 300 — FME workbenches + README
├── web/                          the web map for all three (published site)
├── qgis_validation/              builds the QGIS validation project for all three
├── data/                         all data (gitignored), see "Data"
└── logs/                         run logs (gitignored)
```

Folder names start with a letter and contain no hyphens on purpose: the old
GRID engine behind ArcGIS's Viewshed and Visibility tools fails on paths like
`D:\Repositories\3-regel`. For the same reason the local clone is called
`330300regel` (without hyphens) although the GitHub repository is
`3-30-300-regel`. The folder name still starts with a digit, so the ArcGIS
scripts copy their inputs to a plain work folder under `D:\Temp` before
running.

## Getting started

Requirements: a **QGIS / OSGeo4W** install (its Python has GDAL/OGR and,
for the validation project, PyQGIS; nothing comes from pip), access to the
source data (see [Data](#data)), and for the ArcGIS comparisons ArcGIS Pro
with Spatial Analyst.

1. Clone into a folder without hyphens or spaces:
   `git clone https://github.com/RobertoTjesse/3-30-300-regel.git 330300regel`
2. Copy `indicator_3_bomen/etl/config_local.example.py` to
   `indicator_3_bomen/etl/config_local.py` and fill in `OSGEO4W_ROOT` and
   `VIEWANALYSE_DIR`. This file is gitignored; all shared settings are in
   `indicator_3_bomen/etl/config.py`, which every script in the repository
   imports.
3. Run everything **from the repository root** with that Python, e.g.
   `C:\...\OSGeo4W\apps\Python312\python.exe indicator_3_bomen\etl\01_tile_dem.py`.

What to run:

| Goal | Command (from the repository root) |
|---|---|
| The 3, all stages | `indicator_3_bomen/etl/01_tile_dem.py` … `06_area_summaries.py` — see [`indicator_3_bomen/README.md`](indicator_3_bomen/README.md#pipeline) |
| The 30 and 300 | run the workbenches in FME; results go to `data/fme_output/` — see their READMEs |
| Web map tiles | `python web/build_tiles.py` (the 3), `python web/build_tiles_30_300.py` (the 30 and 300) and `python web/build_tiles_groen.py` (the 300's green) |
| Preview the map | `python web/serve.py` → http://localhost:8000 |
| QGIS validation project | `C:\...\OSGeo4W\bin\python-qgis-ltr.bat qgis_validation\build_project.py` |

## Data

Everything under `data/` is gitignored. What is where:

| Path | What | Source |
|---|---|---|
| `VIEWANALYSE_DIR` (not copied) | per municipality: AHN DSM `.tif` + NEO trees `.gpkg`, 119 GB | `R:\ESRI\BEHEER\Projecten\Tijdelijk_Roberto\3-30-300\fme_input\viewanalyse` |
| `data/interim/` | `province_dem.vrt`, `province_trees.gpkg`, BAG buildings/addresses, gemeenten, wijken, buurten; tiles during a run | built locally (PDOK, see the 3's README) |
| `data/processed/` | the 3: `<name>_viewshed.tif`, `<name>_woningen.gpkg`, `ZuidHolland_*.gpkg/csv`; `experiments/` (e.g. the corrected ArcGIS benchmark) | pipeline output |
| `data/fme_output/` | the FME results: `30_regel_v2.gdb` (30), `300.gdb` (300), `3_lijst.gdb` | copy of `R:\…\3-30-300\fme output` |
| `data/fme_input/` | FME inputs for the 30/300: `gemeentes`, `groenvoorzieningen`, `localeversie_osm` | copy of `R:\…\3-30-300\fme_input` (`panden`, 8.8 GB, stays on R:) |
| `data/studiegebied/` | study-area comparison for one Delft buurt | `indicator_3_bomen/arcgis_tests/studiegebied.py` |
| `web/data/` | `3.pmtiles`, `30-300.pmtiles`, `groen.pmtiles` (each under GitHub's 100 MB limit) | `web/build_tiles*.py` |

The ArcGIS benchmark `visibility_Delft` and the AHN5 DSM used in the
comparisons are read from
`R:\ESRI\DATA\RUIMTELIJKE ONTWIKKELING\PERSOONLIJK\Chris\test\data.gdb`.

## The web map

The 3, the 30 and the 300 behind a 3 / 30 / 300 switch, on the same areas:
gemeenten (zoom < 10), CBS wijken 2025 (< 11.5), CBS buurten 2025 (< 13),
then every residential building (the 3 and the 300; the 30 stays on
buurten). Sources and data dates behind the (i) button, PDOK grey basemap
or aerial photo, address search (PDOK Locatieserver, filtered to the
province).

| File | What |
|---|---|
| `web/index.html` | the map (MapLibre + PMTiles); settings (`PROVINCE`, `REPO`, colours, texts) at the top of the `<script>`; Dutch, English with `?lang=en` (every text as `L("Nederlands", "English")`) |
| `web/uitleg/*.html`, `web/uitleg/en/*.html` | explanation pages in Dutch and English: method, choices, the Konijnendijk paper, how to repeat it |
| `web/build_tiles.py` | `data/3.pmtiles` from `<Province>_gebieden.gpkg` and `_woningen.gpkg` |
| `web/build_tiles_30_300.py` | `data/30-300.pmtiles` from the FME results (`config.FME_OUTPUT_DIR`, default `data/fme_output`) and the 3's areas |
| `web/build_tiles_groen.py` | `data/groen.pmtiles`: the parks and woods of the 300, from the workbench's green input (`config.FME_INPUT_DIR`) |
| `web/serve.py` | local preview server (supports the Range requests PMTiles needs) |

The 30 was computed per CBS buurt 2023; crown and land area are carried over
to the 2025 areas in proportion to overlapping area. Buurten without crown
data (all of Voorne aan Zee; 28 buurten in Schiedam and some in Westland,
Leiderdorp, Pijnacker-Nootdorp and Delft) show "geen gegevens": the FME
query looks each buurt up by its 2023 code in the CBS Wijk- en Buurtkaart
*2022* (`GRENZEN.CBS_WIJKKAART_2022_VERSIE3`), and 114 of the 115 empty
buurten have codes that are new in 2023 — an FME bug, not a gap in the
crown data. The 300 is recounted per area from the
buildings' walking class. The 300 also shows the parks and woods themselves
(from zoom 11), selected from the workbench's input
`data/fme_input/groenvoorzieningen/groenkaart.gdb` (`config.FME_INPUT_DIR`)
as the workbench does: no TOP10NL water, >= 300 m2, perimeter / area <= 0.35.

**Publishing:** copy `web/` without the `.py` files to the `gh-pages` branch
(plus an empty `.nojekyll`); GitHub Pages serves that branch at
https://robertotjesse.github.io/3-30-300-regel/.

## Validation

- **QGIS validation project** (`qgis_validation/build_project.py` →
  `qgis_validation/330300regel_validatie.qgz`, gitignored): the 3 per
  municipality (viewshed and homes), all trees, the DEM, 3D BAG, background
  maps, experiments (the corrected and the original ArcGIS benchmark), the
  one-tree / tree-group comparison and the study area. Re-runs only add new
  layers and remove ones whose file is gone, so changes made in QGIS are
  kept; delete the `.qgz` to rebuild it.
- **The 3 against ArcGIS**: the ArcGIS benchmark turned out to have its
  observers at twice the intended height; with the intended observer ArcGIS
  and this pipeline's GDAL engine agree closely. See
  [`indicator_3_bomen/README.md`](indicator_3_bomen/README.md#validation-against-arcgis).

## Another province

Everything province-specific is a setting or an input file; no code changes.

1. **Inputs per municipality** in one folder (`VIEWANALYSE_DIR`): `<name>.tif`
   (AHN DSM 0.5 m *ruw*, Float32, RD New) and `<name>.gpkg` (tree points, RD
   New). Build `province_dem.vrt` and `province_trees.gpkg` (the 3's README,
   "Setup").
2. **`indicator_3_bomen/etl/config_local.py`**: `PROVINCE` (exactly as in
   PDOK's bestuurlijke gebieden, e.g. `"Utrecht"`), `VIEWANALYSE_DIR`,
   `OSGEO4W_ROOT`, `MUNICIPALITIES = []`, and `FME_OUTPUT_DIR` if you have
   30/300 results. Outputs are named after the province (`Utrecht_woningen.gpkg`).
3. **BAG**: `python indicator_3_bomen/etl/download_bag_pdok.py`.
   **Gemeenten/provincies**: command in `05_merge_province.py`.
   **CBS wijken/buurten**: command in `06_area_summaries.py`, with your
   province's extent as `spatFilter`.
4. **Run** stages `01` … `06`, then `web/build_tiles.py`.
5. **The 30 and 300** still need FME results for that province; without
   them, publish the 3 only.
6. **Map**: set `PROVINCE` and `REPO` at the top of the script in
   `web/index.html`; the map opens on the extent of `data/3.pmtiles`.
7. **Publish** `web/` as described above. The explanation pages (Dutch and
   English) describe Zuid-Holland's run; adapt the figures there.

## Documents

| Document | What |
|---|---|
| [Wiki](https://github.com/RobertoTjesse/3-30-300-regel/wiki) | overview for new readers: getting started, the three indicators, data, validation, known issues |
| [`WORKLOG.md`](WORKLOG.md) | chronological log of the work and findings |
| [`indicator_3_bomen/README.md`](indicator_3_bomen/README.md) | the 3: setup, pipeline, validation, known data issues |
| [`indicator_3_bomen/ARCHITECTURE.md`](indicator_3_bomen/ARCHITECTURE.md) | the 3: design decisions in depth |
| [`indicator_3_bomen/BENCHMARKS.md`](indicator_3_bomen/BENCHMARKS.md) | the 3: run times (generated) |
| [`indicator_3_bomen/IMPROVEMENTS.md`](indicator_3_bomen/IMPROVEMENTS.md) | the 3: improvements over the original prototype; planned work, incl. the 30 and 300 in Python |
| [`indicator_30_kroonbedekking/README.md`](indicator_30_kroonbedekking/README.md) | the 30 |
| [`indicator_300_park/README.md`](indicator_300_park/README.md) | the 300 |
| `web/uitleg/` | explanation pages for the public (Dutch; English in `web/uitleg/en/`) |
| [`README.nl.md`](README.nl.md) | this README in Dutch |

## History

The project started as `RobertoTjesse/3-regel` (the 3 pipeline); the web map
was then built in an earlier repository with this same name,
`3-30-300-regel`. On 2026-09-29 both were combined into this repository,
full history kept, and restructured into one folder per indicator; the two
older repositories were deleted afterwards. Their issues were transferred
here ([issues](https://github.com/RobertoTjesse/3-30-300-regel/issues)), and
the first published 3-only site is kept in the branch
`archief/3-regel-gh-pages`.
