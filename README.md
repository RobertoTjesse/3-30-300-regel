# 3-30-300-regel: the 3-30-300 rule per home, buurt, wijk and gemeente

*Nederlands: [README.nl.md](README.nl.md)*

The 3-30-300 rule (Konijnendijk, 2023) asks for green in three ways:

| | Rule | Measured here as | How it is made | Folder |
|---|---|---|---|---|
| 3 | 3 trees visible from every home | trees visible from just outside the facade (30 m radius, AHN surface model) | Python + GDAL, this repository | [`indicator_3_bomen/`](indicator_3_bomen/) |
| 30 | 30% tree canopy in every neighbourhood | area under crowns (BKB 2024, Friedenau Society, from lidar) / land area per CBS buurt, wijk and gemeente (2025) | Python + GDAL, this repository | [`indicator_30_kroonbedekking/`](indicator_30_kroonbedekking/) |
| 300 | a park within 300 m of every home | home within a 5-minute walk (Valhalla isochrone) of a park or wood >= 300 m2 | FME + PostGIS + Valhalla | [`indicator_300_park/`](indicator_300_park/) |

All three are computed for the province of Zuid-Holland and published as one web map with a 3 / 30 / 300 switch, per gemeente, wijk, buurt and home: **https://robertotjesse.github.io/3-30-300-regel/**. The site and its explanation pages are in Dutch, with an English version ([map](https://robertotjesse.github.io/3-30-300-regel/?lang=en), `/uitleg/en/`). The 3 and the 30 run for another province without code changes ([Another province](#another-province)). The 300 still depends on FME; moving it to open-source Python is planned (`indicator_3_bomen/IMPROVEMENTS.md`).

## Repository layout

```
330300regel/
├── README.md                     this file: the project as a whole
├── README.nl.md                  the same in Dutch
├── WORKLOG.md                    what was done and found, step by step
├── indicator_3_bomen/            the 3: full pipeline, see its README
│   ├── etl/                      stages 01-06, config.py, config_local.py (gitignored)
│   ├── arcgis_tests/             experiments: ArcGIS comparisons, exact test (local only, not on GitHub)
│   ├── sde_reexport/             one-off re-export of corrupted DEMs
│   ├── fme/                      the original FME workbenches for the 3
│   └── README.md, ARCHITECTURE.md, BENCHMARKS.md, IMPROVEMENTS.md
├── indicator_30_kroonbedekking/  the 30: etl/ (BKB 2024 per area) + the earlier FME workbenches
├── indicator_300_park/           the 300: FME workbenches + README
├── web/                          the web map for all three (published site)
├── qgis_validation/              builds the QGIS validation project for all three
├── data/                         all data (gitignored), see "Data"
└── logs/                         run logs (gitignored)
```

Folder names start with a letter and contain no hyphens because the old GRID engine behind ArcGIS's Viewshed and Visibility tools fails on paths like `D:\Repositories\3-regel`. For the same reason the local clone is called `330300regel`, although the GitHub repository is `3-30-300-regel`. That folder name still starts with a digit, so the ArcGIS scripts copy their inputs to a plain work folder under `D:\Temp` before running.

## Getting started

You need a QGIS / OSGeo4W install (its Python has GDAL/OGR and, for the validation project, PyQGIS; nothing comes from pip) and access to the source data (see [Data](#data)). The ArcGIS comparisons also need ArcGIS Pro with Spatial Analyst.

1. Clone into a folder without hyphens or spaces:
   `git clone https://github.com/RobertoTjesse/3-30-300-regel.git 330300regel`
2. Copy `indicator_3_bomen/etl/config_local.example.py` to `indicator_3_bomen/etl/config_local.py` and fill in `OSGEO4W_ROOT` and `VIEWANALYSE_DIR`. This file is gitignored. The shared settings are in `indicator_3_bomen/etl/config.py`, which every script in the repository imports.
3. Run everything from the repository root with that Python, e.g. `C:\...\OSGeo4W\apps\Python312\python.exe indicator_3_bomen\etl\01_tile_dem.py`.

What to run:

| Goal | Command (from the repository root) |
|---|---|
| The 3, all stages | `indicator_3_bomen/etl/01_tile_dem.py` to `06_area_summaries.py`; see [`indicator_3_bomen/README.md`](indicator_3_bomen/README.md#pipeline) |
| The 30 | `python indicator_30_kroonbedekking/etl/kroonbedekking_gebieden.py` (after stage 6 of the 3, which writes the areas); see [`indicator_30_kroonbedekking/README.md`](indicator_30_kroonbedekking/README.md) |
| The 300 | run the workbench in FME; the results go to `data/fme_output/` (see its README) |
| Web map tiles | `python web/build_tiles.py` (the 3), `python web/build_tiles_30_300.py` (the 30 and 300) and `python web/build_tiles_groen.py` (the 300's green) |
| Preview the map | `python web/serve.py`, then http://localhost:8000 |
| QGIS validation project | `C:\...\OSGeo4W\bin\python-qgis-ltr.bat qgis_validation\build_project.py` |

## Data

Everything under `data/` is gitignored:

| Path | What | Source |
|---|---|---|
| `VIEWANALYSE_DIR` (not copied) | per municipality: AHN DSM `.tif` + NEO trees `.gpkg`, 119 GB | `R:\ESRI\BEHEER\Projecten\Tijdelijk_Roberto\3-30-300\fme_input\viewanalyse` |
| `data/raw/` | `bkb_2024.tif`: Boomkroonbedekking 2024 (Friedenau Society, from lidar), the Netherlands at 0.25 m, 9.2 GB (`config.BKB_TIF`) | Friedenau Society |
| `data/interim/` | `province_dem.vrt`, `province_trees.gpkg`, BAG buildings/addresses, gemeenten, wijken, buurten, CBS 2025 land area (`cbs2025_oppervlak.gpkg`); tiles during a run | built locally (PDOK, see the 3's README) |
| `data/processed/` | the 3: `<name>_viewshed.tif`, `<name>_woningen.gpkg`, `ZuidHolland_*.gpkg/csv`; the 30: `ZuidHolland_kroonbedekking.csv`; `experiments/` (e.g. the corrected ArcGIS benchmark) | pipeline output |
| `data/fme_output/` | the FME results: `300.gdb` (300); `30_regel_v2.gdb`, the earlier 30, kept for comparison; `3_lijst.gdb` | copy of `R:\…\3-30-300\fme output` |
| `data/fme_input/` | FME inputs for the 300: `gemeentes`, `groenvoorzieningen`, `localeversie_osm` | copy of `R:\…\3-30-300\fme_input` (`panden`, 8.8 GB, stays on R:) |
| `data/studiegebied/` | study-area comparison for one Delft buurt | `indicator_3_bomen/arcgis_tests/studiegebied.py` |
| `web/data/` | `3.pmtiles`, `30-300.pmtiles`, `looptijd.pmtiles`, `groen.pmtiles` (each under GitHub's 100 MB limit) | `web/build_tiles*.py` |

The ArcGIS benchmark `visibility_Delft` and the AHN5 DSM used in the comparisons are read from `R:\ESRI\DATA\RUIMTELIJKE ONTWIKKELING\PERSOONLIJK\Chris\test\data.gdb`.

## The web map

A 3 / 30 / 300 switch shows the three indicators on the same areas: gemeenten (zoom < 10), CBS wijken 2025 (< 11.5), CBS buurten 2025 (< 13), and from there every residential building (the 3 and the 300; the 30 stays on buurten). The (i) button lists the sources and their dates. The background is the PDOK grey map or an aerial photo, and the address search uses the PDOK Locatieserver, limited to the province.

| File | What |
|---|---|
| `web/index.html` | the map (MapLibre + PMTiles); settings (`PROVINCE`, `REPO`, colours, texts) at the top of the `<script>`; Dutch, English with `?lang=en` (every text as `L("Nederlands", "English")`) |
| `web/uitleg/*.html`, `web/uitleg/en/*.html` | explanation pages in Dutch and English: method, choices, the Konijnendijk paper, how to repeat it |
| `web/build_tiles.py` | `data/3.pmtiles` from `<Province>_gebieden.gpkg` and `_woningen.gpkg` |
| `web/build_tiles_30_300.py` | `data/30-300.pmtiles` (areas, homes) and `data/looptijd.pmtiles` (walking zones and entrances, in a file of their own so that switching them on or off does not re-process the building tiles), from the 30 per area (`<Province>_kroonbedekking.csv`), the FME results of the 300 (`config.FME_OUTPUT_DIR`, default `data/fme_output`) and the 3's areas |
| `web/build_tiles_groen.py` | `data/groen.pmtiles`: the parks and woods of the 300, from the workbench's green input (`config.FME_INPUT_DIR`) |
| `web/serve.py` | local preview server (supports the Range requests PMTiles needs) |

The 30 comes directly from BKB 2024 on the map's own buurten; wijken and gemeenten are the sums of their buurten. The 300 is recounted per area from the buildings' walking class. From zoom 11 the 300 also shows the parks and woods themselves, selected from the workbench's input `data/fme_input/groenvoorzieningen/groenkaart.gdb` (`config.FME_INPUT_DIR`) the way the workbench does: no TOP10NL water, at least 300 m2, perimeter / area at most 0.35.

To publish, copy `web/` without the `.py` files to the `gh-pages` branch, add an empty `.nojekyll`, and push. GitHub Pages serves that branch at https://robertotjesse.github.io/3-30-300-regel/.

## Validation

- The QGIS validation project (`qgis_validation/build_project.py` builds `qgis_validation/330300regel_validatie.qgz`, gitignored) holds the 3 per municipality (viewshed and homes), all trees, the DEM, 3D BAG, background maps, the experiments (the corrected and the original ArcGIS benchmark), the one-tree and tree-group comparison and the study area. A re-run only adds new layers and removes layers whose file is gone, so changes made in QGIS are kept; delete the `.qgz` to rebuild it.
- The ArcGIS benchmark for the 3 had its observers at twice the intended height. With the intended observer, ArcGIS and this pipeline's GDAL engine agree closely. See [`indicator_3_bomen/README.md`](indicator_3_bomen/README.md#validation-against-arcgis).
- The 30 from BKB 2024 was compared with the earlier FME calculation on Delft and per gemeente; see [`indicator_30_kroonbedekking/README.md`](indicator_30_kroonbedekking/README.md).

## Another province

Everything that differs per province is a setting or an input file, so no code changes are needed.

1. Put the inputs per municipality in one folder (`VIEWANALYSE_DIR`): `<name>.tif` (AHN DSM 0.5 m *ruw*, Float32, RD New) and `<name>.gpkg` (tree points, RD New). Build `province_dem.vrt` and `province_trees.gpkg` (the 3's README, "Setup").
2. In `indicator_3_bomen/etl/config_local.py`, set `PROVINCE` (exactly as in PDOK's bestuurlijke gebieden, e.g. `"Utrecht"`), `VIEWANALYSE_DIR`, `OSGEO4W_ROOT`, `MUNICIPALITIES = []`, and `FME_OUTPUT_DIR` if you have 300 results. Outputs are named after the province (`Utrecht_woningen.gpkg`).
3. Download the BAG with `python indicator_3_bomen/etl/download_bag_pdok.py`. The commands for the gemeenten and provincies are in `05_merge_province.py`, and for the CBS wijken and buurten in `06_area_summaries.py`, with your province's extent as `spatFilter`.
4. Run stages `01` to `06`, then the 30 (`kroonbedekking_gebieden.py`; BKB 2024 covers the whole country) and `web/build_tiles.py`.
5. The 300 still needs FME results for that province. Without them, leave the 300 out.
6. On the map, set `PROVINCE` and `REPO` at the top of the script in `web/index.html`; the map opens on the extent of `data/3.pmtiles`.
7. Publish `web/` as described above. The explanation pages (Dutch and English) describe Zuid-Holland's run, so adapt the figures there.

## Documents

| Document | What |
|---|---|
| [Wiki](https://github.com/RobertoTjesse/3-30-300-regel/wiki) | overview for new readers: getting started, the three indicators, data, validation, known issues, run times |
| [`WORKLOG.md`](WORKLOG.md) | chronological log of the work and findings |
| [`indicator_3_bomen/README.md`](indicator_3_bomen/README.md) | the 3: setup, pipeline, validation, known data issues |
| [`indicator_3_bomen/ARCHITECTURE.md`](indicator_3_bomen/ARCHITECTURE.md) | the 3: design decisions in depth |
| [`indicator_3_bomen/BENCHMARKS.md`](indicator_3_bomen/BENCHMARKS.md) | the 3: run times (generated) |
| [`indicator_3_bomen/IMPROVEMENTS.md`](indicator_3_bomen/IMPROVEMENTS.md) | the 3: improvements over the original prototype; planned work, including the 300 in Python |
| [`indicator_30_kroonbedekking/README.md`](indicator_30_kroonbedekking/README.md) | the 30 |
| [`indicator_300_park/README.md`](indicator_300_park/README.md) | the 300 |
| `web/uitleg/` | explanation pages for the public (Dutch; English in `web/uitleg/en/`) |
| [`README.nl.md`](README.nl.md) | this README in Dutch |

## History

The project started as `RobertoTjesse/3-regel` (the pipeline of the 3). The web map was then built in an earlier repository with this same name, `3-30-300-regel`. On 2026-09-29 both were combined into this repository with their full history and restructured into one folder per indicator, and the two older repositories were deleted. Their issues were transferred here ([issues](https://github.com/RobertoTjesse/3-30-300-regel/issues)), and the first published site with only the 3 is kept in the branch `archief/3-regel-gh-pages`.
