# Work log

## 2026-09-28 — code review, ArcGIS comparison, web map

**1. Code review and simplification** (no change in results; stage 2 tile,
stage 4 scores for Hillegom and the stage 5 summary verified identical
before/after on real data)
- Stage 2: removed the unused `gdal_viewshed` subprocess fallback,
  simplified the tree bbox query, fixed the algorithm docstring.
- `config.require_municipality_pairs()` replaces a check copy-pasted in
  stages 1-3 (whose message said tif+shp; the pipeline reads tif+gpkg).
- Removed `OUTPUT_DTYPE` (never read; stages 2/3 always write UInt32).
- Stage 4 uses `config.final_output_path`; stage 5 builds its
  per-municipality and province summary rows in one code path.
- Not done: removing the unreferenced `sde_reexport/watch_and_finish.sh`
  and `sde_reexport/municipality_extents.json` (left for a manual decision).

**2. QGIS validation project**: added the province DEM mosaic
(`province_dem.vrt`, hillshade, off by default) in a "Hoogtemodel" group,
for spotting integer terraces, NoData holes and seams in the input DEM.

**3. Comparison with ArcGIS Pro** (details: `ARCHITECTURE.md` §14)
- A reference Delft result made with ArcGIS's Visibility tool differed
  strongly from this pipeline and showed less self-occlusion.
- Replaying the reference observers through GDAL on the same AHN5 DEM
  showed the viewshed engine, not the tree-height rule, is the main cause.
- `arcgis_tests/visibility_variants.py`: runs Visibility / Viewshed2
  parameter variants on a small test area; Viewshed2 results sit between
  Visibility and GDAL, much closer to GDAL.
- Checked Esri and GDAL documentation (radius 3D vs 2D, default observer
  offset 1 m, Viewshed2 documented as more accurate).
- Open: Visibility fails on ArcGIS Pro 3.6.1 here (GitHub issue #2).

**4. Web map** — https://robertotjesse.github.io/3-regel/ (see README,
"Web map")
- `web/build_tiles.py` builds one PMTiles file (83 MB): municipalities
  (share of homes with >= 3 visible trees) at zoom 6-12, all 978,428
  residential buildings in their class from zoom 13.
- `web/index.html`: MapLibre page on the PDOK grey basemap, legend,
  popups, PDOK address search, map position in the URL; title "3-regel".
- Published with GitHub Pages from the `gh-pages` branch (one commit,
  force-pushed; the tile file is never committed to `master`).
- Considered and dropped: MapStore (can't read PMTiles; would need
  GeoServer), 3D buildings, the Cartiqo basemap, a tree layer.

**5. Tree status found** (GitHub issue #1): 21% of the trees the pipeline
counts are marked "disappeared, small tree" in the source registry;
another 3% "not seen once". What these statuses mean, and whether those
trees should be excluded, is still open.

**6. Gemeente / wijk / buurt**
- `etl/06_area_summaries.py`: assigns every building to its CBS buurt
  (Wijk- en Buurtkaart 2025) and summarises per gemeente, wijk and buurt;
  the gemeente figures match stage 5 exactly. CBS names Rijswijk
  "Rijswijk (ZH.)" — matched by stripping the suffix.
- Web map: the area level follows the zoom (gemeente < 10, wijk < 11.5,
  buurt < 13, then buildings), one colour scale for all levels
  (< 80 / 80-90 / 90-95 / 95-98 / >= 98% of homes with >= 3 trees),
  grey for areas with fewer than 10 homes.

**7. Temporal consistency of the inputs** — shown on the map under (i)
- AHN4 (height model): flown 2020-2022 depending on the area (ahn.nl
  confirms Hollandse Delta in 2020; the year for the rest of the province
  is not stated there).
- Trees: status dates 2020-2022 (mostly 2022); the `date` field is a
  placeholder (2000 for all). Height model and trees are from the same
  period.
- BAG buildings/addresses: downloaded 2026-09-27; CBS areas 2025;
  municipal boundaries 2026-09-28.
- Homes built after the height model: 53,166 homes (3.0%) are in
  buildings with BAG construction year >= 2023, 96,823 (5.4%) >= 2020, of
  1,797,328. Their surroundings are those of 2020-2022. Decision: not
  filtered out; stated on the map instead.

**8. One-tree comparison** (issue #2) — `arcgis_tests/one_tree.py` +
`one_tree_arcgis.py`
- One ~26 m tree 245 m from the Markt in Delft; all tools get the same
  AHN5 DEM clip (NoData filled as in stage 1), the same absolute observer
  elevation (27.85 m NAP, offset 0), target 1.8 m, radius 30 m.
- First result (GDAL vs an exact line-of-sight reference): 37.7% vs 36.4%
  of the cells within 30 m visible, same verdict on 95.5% of cells. For
  this tree GDAL is close to exact; the ArcGIS results are still to come.
- Lesson: with an unfilled NoData sentinel (3.4e38) in the clip the
  results were meaningless for every tool — the fill matters.
- Tree data: NEO (purchased); the web map now says so.
- Result: GDAL 37.7% of cells visible, exact reference 36.4%, ArcGIS
  Viewshed2 47.3% (2D radius). Of the cells only Viewshed2 sees, 83% are
  blocked in the exact test by the tree's own crown within 5 m (median
  1.2 m from the tree, 0.27 m above the sightline): Viewshed2 hardly lets
  the surface right around the observer block. GDAL is close to exact.
- The classic ArcGIS Viewshed tool crashes Pro 3.6.1 here too.
- Water/NoData: no rerun needed — stage 1 fills NoData in every tile, and
  all 52 municipalities were tiled after that fix; only the ad-hoc test
  clip (cut straight from the ArcGIS DEM) needed filling.
- Rerun (2026-09-28): Viewshed2 with a 3D radius sees 32.8% (2D: 47.3%).
  Near the tree it is the same as the 2D run (63.5% at 0-5 m); at 20-30 m
  it drops to 19.2% because the 3D distance from a 27.85 m observer to the
  ground reaches 30 m before the horizontal distance does. Pro crashed
  again on the classic Viewshed tool, so there are still no arc_vs_* or
  arc_vis_* results.
- Run from the Python Command Prompt, one process per tool: classic
  Viewshed (2D, 3D) and Visibility (2D, 3D) all end with exit code -1
  without a Python error. Viewshed2 works. Next test: the classic tools in
  a plain folder (D:\Temp\onetree), because the old GRID engine they share
  is known to fail on folder names like "3-regel".
- Cause of the -1 exits: the path. Classic Viewshed and Visibility (old
  GRID engine) fail on inputs under `D:\Repositories\3-regel`; copied to
  `D:\Temp\onetree` they run (the script now does that).
- Result, share of cells within 30 m visible (same verdict as exact):
  exact 36.4%; classic Viewshed = Visibility 34.0% (97.2%); GDAL 37.7%
  (95.5%); Viewshed2 47.3% (88.5%). Classic Viewshed and Visibility give
  identical rasters and are the closest to exact; Viewshed2 is the outlier.
- The classic tools ignore the 3D radius: RADIUS2 = +30 / outer radius 30
  gives exactly the same raster as -30 (visible cells up to 30.0 m
  horizontal, 38.5 m in 3D). So the reference visibility_Delft run, with
  outer radius 30, is effectively 2D as well, like our pipeline.
- All results in the QGIS validation project (group "Eén boom"), with
  difference maps against the exact test.

## 2026-09-29 — benchmark observer found, one repository, 3-30-300 map

**9. Isolated tree and tree group vs the benchmark**
- `one_tree.py` now runs cases: `tree_68418` (16 m tree, no other tree
  within 56 m), `group_5` (a row of 5 trees 3-7 m tall, no other tree within
  50 m), `tree_58448` (the first tree). The observer is set as in the
  benchmark: RASTERVALU + 1 m. Results are counts (FREQUENCY). The benchmark
  (`visibility_Delft`) and `Delft_viewshed.tif` are cut out around the
  trees and compared only on cells no other tree can see.
- Note: `bomen_Delft` and `bomen_Delft_met_hoogte_uit_AHN05ruw` number the
  trees differently; the fids here are the latter's.
- tree_68418: exact 1.8% of the circle visible, GDAL 1.8%, benchmark
  91.9% (same count as exact on 9.9% of cells). The observer (16.99 m) is
  inside the tree's own crown (up to 17.7 m), which blocks almost
  everything beyond 5 m in the exact test; the benchmark sees past it.
- group_5: exact 71.9%, GDAL 72.5%, benchmark 77.6% (mean count 2.00 /
  1.91 / 2.09); the benchmark agrees with exact on 76.7% of cells.
- The pipeline's Delft_viewshed.tif is on its own DEM: for tree 68418 the
  point cell there is 10.8 m below the canopy top, in AHN5 at the top — not
  a like-for-like comparison.
- Next: `one_tree_arcgis.py` with the new `arc_bench` variant (Visibility
  with exactly the benchmark's settings) to see whether ArcGIS reproduces
  the benchmark on these trees.
- ArcGIS on all three cases (2026-09-29). `arc_bench` (Visibility with the
  documented benchmark settings: raw DSM, observer_elevation RASTERVALU,
  offset left out) does NOT reproduce the benchmark: tree_68418 0.4% seen
  vs benchmark 91.9%; group_5 mean 0.48 vs 2.09. It sees even less than
  RASTERVALU + 1 m, so Visibility seems to add no 1 m default offset.
- **The benchmark's observer was surface + RASTERVALU**, i.e. RASTERVALU
  used as the observer OFFSET. Fitting the observer height for tree 68418
  gives 31.5-32 m NAP = 2 x RASTERVALU (15.99). With that observer: exact
  test = benchmark on 99.4% (tree_68418) and 87.7% (group_5) of cells,
  GDAL 98.5% / 79.0%; with the documented observer 9.9% / 76.7%.
  So the benchmark vs pipeline difference is the observer height, not the
  viewshed engine. Consequences in the benchmark: tall trees get their
  height twice (median RASTERVALU 8.1 m; 41.9% of trees >= 10 m), and the
  6,516 trees (7.4%) with RASTERVALU < 0 (below NAP) have their observer
  BELOW the surface.
- `pipeline_ahn5` (the pipeline's own canopy-top logic on the same AHN5
  clip): tree_68418 12.0%, group_5 73.4%, tree_58448 35.9% seen; exact
  1.7 / 71.9 / 36.4%. On AHN5 the pipeline is consistent with the exact
  test; the old `pipeline_Delft` cut-out (87% for tree_68418) comes from
  the production DEM, where that tree point is 10.8 m below its canopy.
- Next: `arc_bench_offset` (Visibility with observer_offset = RASTERVALU)
  to confirm in ArcGIS itself.
- **Confirmed in ArcGIS:** `arc_bench_both` — Visibility with
  observer_elevation = RASTERVALU AND observer_offset = RASTERVALU —
  reproduces visibility_Delft exactly: the same count on 100.0% of cells
  for tree_68418 and for group_5. The benchmark's observers were at
  2 x RASTERVALU m NAP. (`arc_bench_offset`, surface + RASTERVALU: 99.4% /
  71.4%; the documented settings, `arc_bench`: 8.5% / 24.4%.)
- Corrected benchmark: `arcgis_tests/benchmark_corrected.py` reruns
  visibility_Delft with observer_elevation = RASTERVALU and an explicit
  observer_offset of 1 m (everything else as the original; trees without
  RASTERVALU left out). `compare_benchmark.py` compares it with the
  original and with Delft_viewshed.tif (mean, 0 / >= 3 trees, pixel r).
  `one_tree_arcgis.py` got the same settings as `arc_bench_fixed`, to check
  them against the exact test on the three cases.
- Corrected benchmark, test area ("stukje", 1,039 trees, ~1.5 min from
  the ArcGIS Python Command Prompt; the same run crashed Pro when started
  inside Pro): mean 3.22 trees visible, 49.3% of cells >= 3 trees; the
  original benchmark 5.59 / 72.3%; the pipeline (production DEM) 4.74 /
  67.6%. With the intended observer Visibility is close to GDAL on the same
  area and observer (3.42 / 52.6%, ARCHITECTURE.md section 14). Pixel r:
  corrected vs original 0.78, corrected vs pipeline 0.67.
- The full-Delft corrected run (one Visibility call, 86,488 trees) was
  still busy after 2.5 h on one core and was stopped. benchmark_corrected.py
  now runs in 500 m tiles (+30 m margin for the DEM and the trees, inner part
  kept, mosaicked), 5 tiles at a time in separate processes; resumable.
- Corrected benchmark for all of Delft: 180 tiles of 500 m, 5 in parallel,
  ~55 min. The first mosaic held the tiles' own NoData markers (65535 and
  -128, 7.9 M cells, all on DEM NoData) as counts; cleaned, and tiles are
  now written as 32-bit with one NoData value. No seams at tile edges
  (neighbour differences 0.40 across edges vs 0.40 elsewhere).
- Result inside Delft, >= 30 m from the boundary (86.6 M cells; outside it
  the pipeline has the province's trees and the benchmarks only Delft's):
  corrected benchmark mean 3.28 trees, 26.3% of cells 0 trees, 47.3% >= 3;
  original 6.16 / 16.8% / 69.5%; pipeline (production DEM) 4.57 / 20.0% /
  60.4%. Pixel r: corrected vs pipeline 0.61 (original vs pipeline 0.55),
  corrected vs original 0.77. The pipeline still sees more than the
  corrected benchmark: canopy-top observer (median ~2 m higher than point
  + 1 m) and its own DEM.
- QGIS validation project: visibility_Delft_corrected and the original
  visibility_Delft (read from the geodatabase) side by side under
  Experimenten.

**10. The 3-30-300 web map**
- The 30 (FME canopy cover per CBS buurt 2023) and the 300 (FME walking
  isochrones) added to the map behind a 3 / 30 / 300 switch, on the same
  gemeenten, wijken and buurten as the 3 (`web/build_tiles_30_300.py`);
  explanation pages in Dutch (`web/uitleg/`).
- The 30's missing values (all of Voorne aan Zee, 28 buurten in Schiedam,
  some in Westland, Leiderdorp, Pijnacker-Nootdorp, Delft) turned out to be
  an FME bug: 2023 codes looked up in the CBS map of 2022. The crown data
  is complete.

**11. One repository**
- `3-regel` (the 3) and the earlier `3-30-300-regel` (the map) combined into
  this repository with full history, one folder per indicator; the FME
  workbenches of all three added. The old repositories were deleted, their
  issues transferred here; the site is published from this repository's
  `gh-pages` branch.

**12. The benchmark reproduced exactly on a study area (AHN5)**
- Molenbuurt, Delft (BU05031407), the colleague's own AHN5 DSM and tree
  layer, Visibility with the benchmark's settings
  (`arcgis_tests/studiegebied.py` + `studiegebied_arcgis.py`): the same
  count as `visibility_Delft` on 100.0% of the 515,056 land cells. GDAL with
  the same observers: same >= 3 verdict on 98.3%, same count on 79.2%.
- Trees without RASTERVALU (349 of Delft's 87,837) do count in the
  benchmark. Left out, one tree (41652) was missing (99.2% equal); written
  as 0 in a shapefile, ArcGIS treats them differently (98.9%). Only the
  benchmark's own layer, with the value still NULL, gives 100%; the ArcGIS
  script now copies the trees from there. `benchmark_corrected.py` still
  leaves these trees out (its figures are for 99.6% of the trees).

**13. Consistency check, QGIS, comments, wiki**
- The QGIS validation project now also holds the 3 per gemeente / wijk /
  buurt and the FME results of the 30 and the 300.
- Every Python script got docstrings and comments (no code changes;
  checked by comparing the syntax trees).
- A GitHub wiki for new readers (overview, getting started, the three
  indicators, data, validation, known issues).
- Documentation brought in line with the current state: 5.2 million trees
  (13.5 million is the number of per-tree viewsheds, halos included);
  issue #2 open for the remaining difference; `30_2024.fmw` is the
  workbench behind the map; stale notes in `IMPROVEMENTS.md` and
  `ARCHITECTURE.md` updated.

## 2026-10-01 — the green of the 300 on the map

**14. Parks and woods on the 300 map**
- The 300 tab now shows the green the calculation walks to: dark green
  outlines from zoom 11, filled from zoom 13; a click gives type, source
  (OSM / TOP10NL) and area. New `web/build_tiles_groen.py` ->
  `web/data/groen.pmtiles` (20,973 polygons, zoom 11-14, 7 MB).
- The FME results hold only the entrances, so the green is read from the
  workbench's input (`groenkaart.gdb`, new `config.FME_INPUT_DIR`) with the
  workbench's own selection: its reader leaves out `bron = 'top 10 water'`
  (8,681 lakes, watercourses and the sea, which are in the same layer) and
  its Tester keeps >= 300 m2 with perimeter / area <= 0.35.
- A file of its own: inside `30-300.pmtiles` (zoom 11-16) it grew from
  94 to 117 MB, over GitHub's 100 MB limit.

**15. Issue #2 closed**
- The ArcGIS comparison is explained: the benchmark's observer was at
  2 x RASTERVALU (reproduced on 100% of cells for single trees and for the
  Molenbuurt study area), and with the same DEM and observers GDAL and
  ArcGIS agree (same >= 3 verdict on 98.3%). The gap that remains with the
  pipeline is its own inputs (canopy-top observer, AHN4 DEM). Summary
  posted on the issue, issue retitled and closed; README, ARCHITECTURE,
  `uitleg/3.html` and the wiki updated.

## 2026-10-01 (later) — bilingual site, clean-up

**16. Site and README in Dutch and English**
- The map has a NL / EN switch (`?lang=en`): every text in `index.html`
  as `L("Nederlands", "English")`, static HTML via `data-en`; links go to
  the explanation pages in the chosen language. English versions of all
  five explanation pages in `web/uitleg/en/`, each page linking to its
  counterpart. `README.nl.md`: the README in Dutch.
- Checked: no console errors in either language (headless Edge), all 11
  pages' relative links and anchors resolve.

**17. Clean-up and consistency check**
- Removed `sde_reexport/municipality_extents.json` (an exact copy of the
  `.csv` that `export_from_sde.py` reads). The other rarely mentioned files
  are in use or record how the DEMs were fixed; now documented in the 3's
  README (helpers in `etl/`, the three re-export steps).
- ARCHITECTURE §12 checked against `config.py`: all values match. Wiki
  pages updated for the English version.
- Local (gitignored): old run logs packed into
  `logs/archief_2026-09.zip` (`benchmark.csv` kept, `BENCHMARKS.md` is
  generated from it); the obsolete `province_dem.vrt.bak_2026-09-27`
  removed.

**18. Walking zones and entrances in a tile file of their own**
- Ticking "Looptijdzones en ingangen" was slow: MapLibre re-processes every
  loaded tile of a source when one of its layers is switched on or off, and
  the zones were in `30-300.pmtiles` with all the buildings (~20,000
  buildings / 210,000 points in a Delft-sized view at zoom 13).
- `web/build_tiles_30_300.py` now writes two files from one run:
  `30-300.pmtiles` (areas, homes; 94 -> 87 MB) and `looptijd.pmtiles`
  (iso5, iso15, ingangen; 7.5 MB), with the same zoom ranges as before; the
  map reads the zones from the new source. Same features as before (checked
  in a Delft window at zoom 13).
- Not done (possible next steps): simplify the isochrones (the 15-minute
  zone is one polygon of 100,242 vertices), show them from zoom 11, toggle
  by opacity instead of visibility.

## 2026-10-04 — the map on a phone

**19. Opening view above the panel on a phone**
- On a phone the panel sits at the bottom (up to 45% of the screen) and
  covered the south of the province. Fitting with bottom padding alone did
  nothing: the pan limits (`maxBounds`) pulled the view back. Now, on screens
  <= 600 px wide, the map first fits the province into the part above the
  panel and then sets the southern pan limit to the screen's bottom edge.
  The address search puts the found place in the middle of that part too.
- Measured in headless Edge at phone size: province from y 20 to 378 with
  the panel from 390 (before: 177 to 579). Desktop unchanged.

**20. The 30 from BKB 2024: test on Delft**
- BKB 2024 (boomkroonbedekking, Friedenau Society, from lidar): the
  Netherlands at 0.25 m, 1 = crown, 1.2 M x 1.6 M pixels (1.9 TB
  uncompressed, 9.2 GB as a COG). A 1 km window reads in 0.05 s.
- New `indicator_30_kroonbedekking/etl/bkb_per_gebied.py`: 1 km tiles in
  parallel; per tile the areas are burnt into the same 0.25 m grid (pixel
  centre decides, so no double counting on boundaries) and crown pixels and
  all pixels counted per area. ~100 MB per worker. Delft (91 buurten):
  3 s. `config.BKB_TIF` = `data/raw/bkb_2024.tif`.
- Delft against the FME 30 on the same CBS 2023 buurten and land area:
  crown area FME/NEO 553.6 ha vs BKB 469.3 ha (FME 18% higher); canopy
  cover 24.7% vs 21.0%; per buurt r = 0.95, median FME - BKB +3.5 points;
  buurten >= 30%: 23 vs 17. Largest gap: Bedrijventerrein Delftech
  (59.8% vs 23.7%). Delft's 91 buurten are the same in CBS 2023 and 2025;
  on CBS 2025: 20.9%.
- CBS land area (2023 and 2025) is in whole hectares; per buurt that
  rounding moves a percentage by up to a few points.

**21. Comparison with Cobra Groeninzicht; sources in `docs/`**
- Cobra Groeninzicht sells a national 3+30+300 with a mark per building. Their
  method comes from the FAQ on their site and their public ArcGIS layers; the
  mark formulas follow exactly from the 11 sample buildings in their legend
  layer: 3 = 1/3/5/.../10 for 0/1/2/.../7+ trees, 30 = % / 5, 300 = 10 -
  0.02 x (m - 100), total = 0.25 / 0.5 / 0.25. Written up in
  `docs/COMPARISON_COBRA.md`, with the Yggdrasil handbook (Dutch translation
  by Cobra) and what each side probably misses.
- Their gemeente values for the 50 municipalities of Zuid-Holland against
  ours: the 30 ranks alike (r = 0.83) but is 7.6 points higher on average
  (500 m around buildings vs all land of the municipality); the 3 hardly
  relates (r = 0.31): they count only crowns > 28 m², we count every tree
  point. The tree-size threshold is the main open point for the 3.
- `docs/bronnen/`: the Konijnendijk (2023) paper (CC BY 4.0, committed) and
  the handbook (local only, handed out by Cobra after a form).
- Planned work added to `IMPROVEMENTS.md`: the 3 per floor for multi-storey
  buildings (3D BAG floors, one target height per floor) and a fixed set of
  values per home, incl. the number of rules met (0-3) and an optional mark
  on Cobra's scale.
