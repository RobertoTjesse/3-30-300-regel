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

Inputs (copied to `data/fme_input/`): `groenvoorzieningen/` (green from OSM
and TOP10NL), `localeversie_osm/` (OSM extracts), `gemeentes/`. The building
input `panden` (8.8 GB) stays on R:.

Result used downstream:

- `data/fme_output/300.gdb`: `_300regel` / `_300regel_15` (every BAG pand,
  `_related_suppliers` = 1 when it lies in a 5 / 15-minute isochrone from an
  entrance), `isochrones_dissolved(_15)`, `ingang_parken`. Read by
  `web/build_tiles_30_300.py`.

Method and caveats: `web/uitleg/300.html`.
