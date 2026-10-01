# The 300: green within 300 m of every home

Whether every home lies within a 5-minute walk (about 300-425 m over the
street network) of a park or wood of at least 300 m2. Still made with FME,
PostGIS and a pedestrian isochrone calculator (Valhalla); this folder holds
the workbenches, the results are in `data/fme_output/` (gitignored, copied
from `R:\ESRI\BEHEER\Projecten\Tijdelijk_Roberto\3-30-300\fme output`).

| File | What |
|---|---|
| `fme/300_2025 regel.fmw` | latest version of the rule |
| `fme/300 regel.fmw`, `fme/300_2024.fmw`, `fme/300_2025.fmw` | earlier versions |

Inputs of `300_2025 regel.fmw` (the readers that are switched on):

| Input | Dataset | Date |
|---|---|---|
| Parks and woods | `data/fme_input/groenvoorzieningen/groenkaart.gdb`, layer `groen_uit_osm_top10_2024` (OSM + TOP10NL) | 2024 |
| Paths, for the entrances | `data/fme_input/localeversie_osm/gis_osm_roads_free_1.shp` (Geofabrik extract Zuid-Holland) | OSM as of 2025-08-26 |
| Home buildings | SDE table `TOPOGRAFIE.BAG_PAND_PZH` (connection "Topografie"), read during the run | run of 2025-12-09 |
| Walking isochrones | a local Valhalla, called over HTTP | network date not recorded |

A PostGIS OSM reader (connection `osm_reader_20250509`, one test
municipality) is switched off. The copied `gemeentes/` and the building
input `panden` (8.8 GB, on R:) are not read by this version.

Result used downstream:

- `data/fme_output/300.gdb`: `_300regel` / `_300regel_15` (every BAG pand,
  `_related_suppliers` = 1 when it lies in a 5 / 15-minute isochrone from an
  entrance), `isochrones_dissolved(_15)`, `ingang_parken`. Read by
  `web/build_tiles_30_300.py` (zones and entrances go to `web/data/looptijd.pmtiles`).
- The green itself is not in the results; `web/build_tiles_groen.py` reads
  it from the input `groenkaart.gdb` with the workbench's own selection (its
  reader's `bron <> 'top 10 water'` and its Tester: `_area >= 300` and
  `Shape_Length / Shape_Area <= 0.35`) to show the parks and woods on the
  map (`web/data/groen.pmtiles`).

Method and caveats: `web/uitleg/300.html`.
