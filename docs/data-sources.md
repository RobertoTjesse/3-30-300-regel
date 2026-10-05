# Data sources

Everything under `data/` is gitignored, so no large file ends up in the repository.

## Inputs

| Data | Source | Date | Used by |
|---|---|---|---|
| Surface model | AHN4 DSM, 0.5 m, *ruw* (`Geo_raster.TOPOGRAFIE.AHN4_05M_RUW`), one `.tif` per municipality | flown 2020-2022 | 3 |
| Tree points | NEO tree data (purchased, not public), one `.gpkg` per municipality | 2020-2022 | 3 |
| Tree crowns | Boomkroonbedekking 2024 (BKB 2024), Friedenau Society, from lidar; the Netherlands at 0.25 m (`data/raw/bkb_2024.tif`) | 2024 | 30 |
| Land area per buurt, wijk and gemeente | CBS Wijk- en Buurtkaart 2025 via PDOK (`oppervlakteLandInHa`, whole hectares) | 2025 | 30 |
| Crown polygons (earlier 30, for comparison) | NEO `BOMEN_KRONEN` (purchased) | not recorded | — |
| Buildings and addresses | BAG via PDOK (`download_bag_pdok.py`) | 27 September 2026 | 3 |
| Buildings with a woonfunctie | BAG, provincial copy (SDE `TOPOGRAFIE.BAG_PAND_PZH`), read during the FME run | 9 December 2025 | 300 |
| Gemeente and provincie boundaries | Bestuurlijke gebieden (Kadaster) via PDOK | 28 September 2026 | 3, 30, 300 |
| Wijken and buurten | CBS Wijk- en Buurtkaart 2025 via PDOK | 2025 | 3, 30, 300 (map) |
| Buurten and land area (earlier 30, for comparison) | CBS Wijk- en Buurtkaart 2023 | 2023 | — |
| Parks and woods | OpenStreetMap and TOP10NL (`groenkaart.gdb`, layer `groen_uit_osm_top10_2024`) | 2024 | 300 |
| Paths and roads (entrances) | OpenStreetMap, Geofabrik extract Zuid-Holland (`gis_osm_roads_free_1.shp`) | 2025 | 300 |

## Where the files are

| Path | What |
|---|---|
| `VIEWANALYSE_DIR` (not copied, 119 GB) | per municipality: AHN DSM `.tif` and NEO trees `.gpkg` |
| `data/interim/` | `province_dem.vrt`, `province_trees.gpkg` (5.2 million trees), BAG buildings and addresses, gemeenten, provincies, wijken, buurten; tiles during a run |
| `data/processed/` | `<name>_viewshed.tif`, `<name>_woningen.gpkg`, `ZuidHolland_woningen.gpkg`, `ZuidHolland_gebieden.gpkg`, `ZuidHolland_kroonbedekking.csv` (the 30), summary CSVs; `experiments/` |
| `data/raw/` | `bkb_2024.tif`, the crown raster of the 30 (9.2 GB) |
| `data/fme_output/` | FME results: `300.gdb` (300); `30_regel_v2.gdb`, the earlier 30, kept for comparison |
| `data/fme_input/` | FME inputs for the 300; the map's green areas are read from `groenvoorzieningen/groenkaart.gdb` (`config.FME_INPUT_DIR`) |
| `web/data/` | `3.pmtiles`, `30-300.pmtiles`, `looptijd.pmtiles`, `groen.pmtiles` (published on `gh-pages` only) |

## Dates

The surface model and the trees are from the same period (2020-2022), the buildings from 2026. Homes built after the surface model appear in the surroundings of 2020-2022: 3.0% of homes are in buildings from 2023 or later, and 5.4% in buildings from 2020 or later. They are not filtered out, and the map says so.
