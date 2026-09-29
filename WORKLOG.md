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

**9. Isolated tree and tree group vs the benchmark** (2026-09-29)
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
