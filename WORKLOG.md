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
