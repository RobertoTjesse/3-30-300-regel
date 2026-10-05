# Getting started

## Requirements

- A QGIS / OSGeo4W install. Its Python has GDAL/OGR, and PyQGIS for the validation project; nothing is installed with pip.
- Access to the source data (see [Data sources](data-sources.md)).
- ArcGIS Pro with Spatial Analyst, only for the ArcGIS comparisons.
- For now, FME and a local Valhalla for the 300.
- For the 30, the BKB 2024 crown raster in `data/raw/bkb_2024.tif`.

## Setup

1. Clone into a folder without hyphens or spaces, because the old GRID engine behind ArcGIS's Viewshed and Visibility tools fails on paths like `D:\Repositories\3-regel`:
   ```
   git clone https://github.com/RobertoTjesse/3-30-300-regel.git 330300regel
   ```
2. Copy `indicator_3_bomen/etl/config_local.example.py` to `indicator_3_bomen/etl/config_local.py` (gitignored) and fill in:
   - `OSGEO4W_ROOT`: your QGIS/OSGeo4W install
   - `VIEWANALYSE_DIR`: the folder with one DEM `.tif` and one tree `.gpkg` per municipality
   - `MUNICIPALITIES`: `[]` for all, or a list of names
   - `FME_OUTPUT_DIR`: only if the FME results are not in `data/fme_output/`
3. Build the two province-wide sources once:
   ```
   gdalbuildvrt data/interim/province_dem.vrt "<VIEWANALYSE_DIR>\*.tif"
   ```
   and merge every municipality's tree `.gpkg` into `data/interim/province_trees.gpkg` with `gdal.VectorTranslate()`. The `ogr2ogr` command line mis-reads names that start with an apostrophe (`'s-Gravenhage`).
4. Download the BAG buildings and addresses with `python indicator_3_bomen/etl/download_bag_pdok.py` (about 25 min, 1 GB). The commands for the gemeente and provincie boundaries and the CBS wijken and buurten are in the docstrings of `05_merge_province.py` and `06_area_summaries.py`.

Run everything from the repository root with the OSGeo4W Python, e.g. `C:\...\OSGeo4W\apps\Python312\python.exe`.

## What to run

| Goal | Command (from the repository root) |
|---|---|
| The 3, stages 1-3 | `python indicator_3_bomen/etl/01_tile_dem.py`, `02_compute_viewsheds.py --workers 4 --resume`, `03_merge_tiles.py`; or all three per municipality with `indicator_3_bomen/etl/run_all_municipalities.sh` (Git Bash) |
| The 3, stages 4-6 | `python indicator_3_bomen/etl/04_score_buildings.py`, `05_merge_province.py`, `06_area_summaries.py` |
| The 30 | `python indicator_30_kroonbedekking/etl/kroonbedekking_gebieden.py` (after stage 6 of the 3) |
| The 300 | run the workbench in FME and copy the results to `data/fme_output/` |
| Web map tiles | `python web/build_tiles.py` (the 3), `python web/build_tiles_30_300.py` (the 30 and 300) and `python web/build_tiles_groen.py` (the 300's green) |
| Preview the map | `python web/serve.py`, then open http://localhost:8000 |
| QGIS validation project | `C:\...\OSGeo4W\bin\python-qgis-ltr.bat qgis_validation\build_project.py` |
| Run-time report | `python indicator_3_bomen/etl/generate_benchmark_report.py` writes `BENCHMARKS.md` |

## Repository layout

```
330300regel/
├── indicator_3_bomen/            the 3: etl/ (stages 01-06, config), arcgis_tests/, sde_reexport/, fme/
├── indicator_30_kroonbedekking/  the 30: etl/ (BKB 2024 per area), earlier FME workbenches
├── indicator_300_park/           the 300: FME workbenches
├── web/                          the web map and its tile builders
├── qgis_validation/              builds the QGIS validation project
├── data/                         all data (gitignored)
└── logs/                         run logs (gitignored)
```

The settings every script shares are in `indicator_3_bomen/etl/config.py`; paths for your own machine go in `config_local.py`.
