# The 30: 30% tree canopy per neighbourhood

Share of every CBS buurt covered by tree crowns (NEO crown polygons). Still
made with FME; this folder holds the workbenches, the results are in
`data/fme_output/` (gitignored, copied from
`R:\ESRI\BEHEER\Projecten\Tijdelijk_Roberto\3-30-300\fme output`).

| File | What |
|---|---|
| `fme/30_2024.fmw` | writes `30_regel_v2.gdb` (the version used by the web map) |
| `fme/30_2025.fmw` | 2025 version of the workbench |

Result used downstream:

- `data/fme_output/30_regel_v2.gdb`, layer `FeatureClass_buurt`: crown area
  (sum of the NEO crowns touching the buurt) and land area per CBS buurt 2023.
  `web/build_tiles_30_300.py` carries this over to the map's gemeenten, wijken
  and buurten (2025) in proportion to overlapping area.

Known issues are described in `web/uitleg/30.html` and `WORKLOG.md`
(missing values in some municipalities: an FME bug, not a gap in the crown
data). Moving this to open-source Python is planned
(`indicator_3_bomen/IMPROVEMENTS.md`).
