# Another province

For the 3 and the 30, everything that differs per province is a setting or an input file, so no code changes are needed.

1. Put the inputs per municipality in one folder (`VIEWANALYSE_DIR`): `<name>.tif` (AHN DSM 0.5 m *ruw*, Float32, RD New) and `<name>.gpkg` (tree points, RD New). Without NEO data, a municipal tree register or another tree mapping works too. Build `province_dem.vrt` and `province_trees.gpkg` (see [Getting started](getting-started.md)).
2. Set the province in `indicator_3_bomen/etl/config_local.py`:
   ```python
   PROVINCE = "Utrecht"                 # exactly as in PDOK's bestuurlijke gebieden
   VIEWANALYSE_DIR = Path(r"D:\data\utrecht")
   MUNICIPALITIES = []                  # empty = all
   ```
   plus `OSGEO4W_ROOT`, and `FME_OUTPUT_DIR` if you have 300 results. Outputs are named after the province (`Utrecht_woningen.gpkg`).
3. Download the BAG with `python indicator_3_bomen/etl/download_bag_pdok.py`. The commands for the gemeenten and provincies are in `05_merge_province.py`, and for the CBS wijken and buurten in `06_area_summaries.py`, with your province's extent as `spatFilter`.
4. Run stages `01` to `06`, then the 30 (`indicator_30_kroonbedekking/etl/kroonbedekking_gebieden.py`; BKB 2024 covers the whole country) and `web/build_tiles.py`.
5. The 300 still needs FME results for that province. Without them, leave the 300 out.
6. On the map, set `PROVINCE` and `REPO` at the top of the script in `web/index.html`. The map opens on the extent of `data/3.pmtiles`.
7. Publish `web/` as described in [Web map and publishing](web-map.md). The explanation pages describe Zuid-Holland's run, so adapt the figures there.

## Checks worth doing

- DEMs must be floating point. `01_tile_dem.py` stops on integer or scaled DEMs; an Int32 export once truncated the heights to whole metres.
- Look at the DEM hillshade in the [QGIS validation project](qgis-validation.md) before a long run. Large areas of exactly 0 without a NoData flag once made 12 of 52 municipalities look like flat, open terrain.
- `06_area_summaries.py` checks its gemeente totals against stage 5 and prints a warning when they differ.
