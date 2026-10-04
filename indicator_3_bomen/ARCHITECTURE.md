# Architecture of the 3: the South Holland viewshed pipeline

A technical reference for how the pipeline of the 3 works: the raster mechanics, the tiling scheme, the TIFF layout, and what each script does and why. For setup and usage, see `README.md`; for run times and current parameters, `BENCHMARKS.md`.

## 1. What is computed

For every 0.5 m pixel of a municipality, the pipeline counts from how many trees within 30 m the pixel is visible, with line of sight that takes terrain and obstacles into account. This is the "3" of the 3-30-300 rule for urban green (3 trees visible from home, 30% canopy cover, 300 m to green space).

Computing each tree's 30 m viewshed and writing it to its own raster does not scale: a mid-size municipality has more than 100,000 trees, so that would be more than 100,000 raster files. This pipeline accumulates instead. For each tree it computes the viewshed and adds 1 to every pixel visible from it. The result is one raster per municipality in which each pixel holds the number of trees visible from there.

## 2. Source data

One pair of files per municipality, with the same base name (`Delft.tif` / `Delft.gpkg`):

- DEM: `.tif`, Float32, one band, 0.5 m pixels, RD New / EPSG:28992. The source is `Geo_raster.TOPOGRAFIE.AHN4_05M_RUW`, AHN4 at 0.5 m in its raw ("RUW", unfiltered) form: a surface model (DSM) that includes buildings and tree canopy, not just the ground. That is what makes it possible to sample a tree's canopy height (§6) and what makes a roofline or hedge block the line of sight, which would not happen on a bare-earth DTM.
- Trees: `.gpkg`, points, EPSG:28992, without a height attribute. They were originally shipped as `.shp` and converted once to GeoPackage (§7): a bare shapefile has no spatial index, so a bounding-box query per tile scans close to the whole file. On the larger municipalities that dominated the runtime until it was fixed (about 280x slower per query without an index).

Both are read in place from wherever `config.VIEWANALYSE_DIR` points (in practice a network share); nothing is copied locally. Some DEMs are tens of gigabytes (Rotterdam about 13 GB, Goeree-Overflakkee about 17 GB).

## 3. Why tiling is needed

`gdal.ViewshedGenerate()` needs the DEM band open for random reads while it traces lines of sight. Opening a 17 GB DEM once is fine, but keeping the accumulator array for a whole municipality in memory as well is not: for Goeree-Overflakkee's raster of about 35,000 x 19,000 pixels, one UInt32 accumulator is about 2.6 GB, and GDAL's read-ahead and caching on top of that get expensive quickly, all the more with `NUM_WORKERS` processes running at once.

So the DEM is split into tiles of 1000 x 1000 pixels (500 x 500 m), each processed on its own. Tiles are also the unit of work for `ProcessPoolExecutor`, which is what allows `NUM_WORKERS` processes to run in parallel.

## 4. The buffer, or halo

A tile is more than its own 1000 x 1000 pixel window. Every tile is cut with a buffer of 70 pixels (35 m) on all four sides, a "halo", so a full interior tile is 1140 x 1140 pixels on disk.

The reason is that a tree's viewshed radius (`MAX_DISTANCE`) is 30 m. A tree 2 m inside a tile's edge can light up pixels up to 30 m away, some of which lie in the neighbouring tile. Processing each tile strictly within its own 1000 x 1000 footprint would go wrong in two ways at every tile edge:

1. A `ViewshedGenerate()` call near the edge would run out of DEM (blocking cannot be tested past the raster's edge) and cut that tree's contribution short without warning.
2. Trees just across the edge, which do affect pixels on this side, would never be queried if the tree query were limited to the tile's own footprint.

The buffer must be at least `MAX_DISTANCE`. It is 35 m (70 px at 0.5 m), 5 m more than needed.

The pattern, which this pipeline uses everywhere (§8 extends it to the whole province):

1. Read the DEM pixels and query the trees over the buffered extent (1140 x 1140). A tree just across the edge of a neighbouring tile is included on purpose, and that neighbouring tile queries it too. Every tree has to be evaluated by every tile whose halo it falls in, or that tile's edge pixels get an incomplete count.
2. Accumulate into a 1140 x 1140 array (one per tile, in memory, per worker process).
3. Crop the accumulator to the inner 1000 x 1000 window before writing anything. The halo's values are thrown away; they were only needed as context for computing the inner window correctly.

Because every tile written to disk is exactly its own inner window, the tiles fit edge to edge without any overlap. When `03_merge_tiles.py` mosaics them (a plain VRT union, with no blending), there is no seam to get wrong: every output pixel comes from exactly one tile's complete, correctly buffered computation.

This exact bug (querying and writing only the inner extent, with no halo) was present early in the project. It produced a visible seam at every internal tile edge, an undercount on a regular grid covering about 10-12% of every municipality's area. The halo-and-crop split above fixed it; the git history has the full account.

## 5. Inside one tile (`02_compute_viewsheds.py`)

For a single tile:

```
for each tree within the tile's BUFFERED extent (from PROVINCE_TREES_GPKG):
    h = sample the tree's height from the DEM (§6)
    result = gdal.ViewshedGenerate(
        srcBand       = this tile's DEM band,
        observerX/Y   = tree coordinates,
        observerHeight= h,
        targetHeight  = 1.8,     # eye/window height — see §12
        maxDistance   = 30.0,
        dfCurvCoeff   = 0.0,      # flat-earth — irrelevant at 30m radius
        mode          = GVM_Edge,
        visibleVal=1.0, invisibleVal=0.0, outOfRangeVal=0.0,
    )
    accumulator[result's footprint] += result   # see note below
accumulator = accumulator[inner window]          # crop
write accumulator to <tile_id>.tif
```

`gdal.ViewshedGenerate()` does not return a raster the size of the input tile. It returns a small window centred on the observer, sized by `maxDistance` (about 121 x 121 pixels for a 30 m radius at 0.5 m): just the disc the tree could affect. The result has to be pasted into the tile's full-size accumulator at the pixel offset that follows from comparing the two geotransforms. Treating it as tile-sized, a bug early in the project, raises a numpy broadcast error on every tree.

The output tiles have no NoData value. A pixel value of `0` means "no tree within 30 m", a real and common answer (most of any municipality is water, non-residential land or far from vegetation), not missing data. Flagging it as NoData would make GIS tools draw large legitimate areas as blank and leave them out of any statistics on the raster.

## 6. The observer height per tree

Each tree's height is sampled from the DEM itself instead of using one constant for all trees. This happens in `_prepare_tree()`, which also cuts the DEM within `MAX_DISTANCE` of the tree into a small in-memory raster for that tree's `ViewshedGenerate()` call:

1. Canopy top: the maximum DEM value within `TREE_HEIGHT_BUFFER_RADIUS` (1.5 m) of the tree point, measured to pixel centres, ignoring building pixels when building footprints are available (a roof edge next to a tree is not its crown).
2. The observer is placed at the canopy top: `observerHeight` is the canopy top minus the DEM value at the observer's own pixel, because GDAL adds it to exactly that pixel (see "observerHeight is additive" below).
3. Plausibility check: the local ground is estimated as the minimum DEM value within `TREE_GROUND_SEARCH_RADIUS` (5 m). If the tree's height above that ground (canopy top minus ground) is not above 0 or exceeds `TREE_HEIGHT_MAX_PLAUSIBLE` (35 m), the tree gets the constant `OBSERVER_HEIGHT` (1.7 m above its pixel) instead. The ground estimate is only used for this check, never for the observer height.

   Until 2026-09-27 the check was applied to the absolute canopy value in NAP (`<= 0` or `> 35 m` NAP). South Holland runs from about -6 m NAP in the polders to +40 m NAP in the dunes, so that rejected ordinary trees at both ends: in Delft, 3.0% of all trees (about 2,600, with crowns below NAP) got the 1.7 m fallback instead of their real height. With the check on height above ground, only 1 of 87,837 is rejected (median tree height 8.4 m, 90th percentile 18.2 m). Deep polders (such as Zuidplas) and dune municipalities were affected more.

The check is needed because the source raster has no point classification. It is a plain elevation grid, and its values alone cannot tell a power line, pylon or building corner from a tree canopy. AHN's underlying lidar point cloud does carry classes for exactly this (ASPRS classes 13-16: wire guard and conductor, transmission tower, wire-structure connector), but that information is lost in the derived raster this pipeline reads. The plausibility check is a practical stopgap; a better fix would derive tree heights from the classified point cloud, leaving out the non-vegetation classes before rasterizing.

`observerHeight` is additive, not absolute (fixed 2026-09-27). The `observerHeight` and `targetHeight` parameters of `gdal.ViewshedGenerate()` are offsets added to the DEM's own value at that pixel, not an absolute Z. Earlier versions passed the sampled canopy top straight in as that offset. The tree's own pixel comes from the same surface model and usually already lies under its own canopy, so the observer ended up at `DEM_value_at_tree_pixel + canopy_top`, counting part of the tree twice. On 300 real Delft trees, the pixel directly under a tree was at a median of about 52% of its sampled canopy top, so observers sat roughly 1.4-2x too high. `_observer_offset()` now passes `canopy_top - DEM_value_at_tree_pixel` (with the pixel found by `floor()`, as GDAL's viewshed code does), which puts the observer exactly at the canopy top. This was verified on 2026-09-27 against the GDAL 3.12 documentation and a synthetic DEM: both `observerHeight` and `targetHeight` switch from blocked to visible exactly where the additive reading predicts. Re-checked on about 1,000 Delft trees after the fix, the median `observerHeight` fell from 4.45 m to 1.69 m (a median overshoot of 2.3 m removed). All 52 municipalities were rerun with this fix in the full run of 2026-09-27/28.

Why the canopy top and not the exact tree point (2026-09-27). For a short while the observer was placed on the DSM at the exact tree point, as the reference ArcGIS method does with `RASTERVALU`. That point usually lies under the crown, below its top (in Delft a median of 4.0 m above ground at the point against 8.4 m for the canopy top), so the tree's own higher crown pixels block its lines of sight: self-occlusion. On 488 random Delft trees, 35.5% then saw less than 1% of the cells beyond 3 m (with the canopy top: 0%). The share of Delft pixels with >= 3 trees was 20.6%, against 50.9% with the canopy top.

Own-crown removal (optional, `OWN_CROWN_RADIUS`, default 0 = off). Before a tree's viewshed, every non-building pixel within the radius is lowered to the local ground estimate, which removes the tree's own crown from its own line-of-sight surface. Building pixels (from `PROVINCE_BUILDINGS_GPKG`, required when the radius is above 0) are never lowered; without that mask, 11.7% of Delft trees have a building within 3 m that would be cut open. At pixel level (Delft, r = 3 m with the mask), 53.2% of pixels see >= 3 trees, against 50.9% with the plain canopy top. At building level, which is what counts (a residential building sees N trees if any of its pixels does), it changes the >= 3 verdict for only 0.1% of Delft's 30,509 residential buildings (97-99% stay in the same colour class), at about 14% extra runtime. So it is off by default: the canopy top already avoids nearly all self-occlusion. The exact tree point, by comparison, changed the class of 53-69% of the residential buildings.

## 7. Why GeoPackage instead of the original shapefiles

A bare `.shp` has no spatial index, so `layer.SetSpatialFilter(bbox)` on it falls back to a near-linear scan of the whole file on every call. This pipeline makes that call once per tile, so the total cost of tree queries grows with `tile_count x total_features_in_file` instead of with the number of trees each query returns. On a small municipality (few tiles) that goes unnoticed; on a large one (many tiles and features) it dominates the runtime. Measured: about 280x slower per query on an unindexed file than on the same data in a GeoPackage, which has a built-in R-tree. That matched the drop in throughput seen across municipalities of increasing size before the fix.

Each municipality's `.shp` was converted once with `ogr2ogr -f GPKG`, except `'s-Gravenhage` (Den Haag): the `ogr2ogr` command line mis-parses a base name that starts with an apostrophe and produced an empty GeoPackage (0 layers) without an error. It was regenerated with `gdal.VectorTranslate()`, the Python API, which does not go through the same argument parsing.

## 8. Municipality boundaries: the same halo, one level up

The halo of §4 handles seams between tiles within one municipality. A municipality boundary is the same kind of edge: a tree in the neighbouring municipality, close to the border, can be within 30 m of a pixel on this side.

Rather than teach every script which municipalities are neighbours, the pipeline builds two province-wide sources once and points the per-tile logic at them instead of at one municipality's own files:

- `PROVINCE_DEM_VRT`: a `gdalbuildvrt` mosaic of every municipality's DEM `.tif`. `01_tile_dem.py` reads the pixels for every tile's buffered window from this mosaic, not from the municipality's own raster. The buffered window is computed as before (§4) but is no longer clipped to the municipality's own raster bounds: where it reaches past the municipal edge, the VRT supplies real pixels from whichever neighbouring municipality's file covers that area (a VRT is a set of references to source files, and GDAL picks the right file per pixel).
- `PROVINCE_TREES_GPKG`: every municipality's tree GeoPackage merged into one layer (about 5.2 million points for the province). `02_compute_viewsheds.py` queries it for every tile's buffered extent, so a tree in the neighbouring municipality is included just like a tree from a neighbouring tile in the same municipality (§4): queried from both sides, with output written only for the inner window.

`TILE_BUFFER_PX` (35 m) already exceeds the 30 m requirement, so there is no separate buffer for municipality boundaries; the same halo now reaches across an administrative boundary instead of stopping at it.

Each municipality's own DEM `.tif` still decides where the tiles go (its extent and pixel grid), so the tile coverage per municipality did not change. Only the source of the pixel values did.

## 9. TIFF layout: internal tiles or strips

A GeoTIFF can store its pixels in two ways:
- In strips, where each block spans the full width of the raster. Reading a small window still means reading complete rows.
- In internal tiles, fixed-size blocks (for example 512 x 512), so reading a small window only touches the blocks it overlaps.

Several of the original source DEMs are stored in strips (on Rotterdam: `Block=91065x1`, one block per row of 91,065 pixels). This pipeline makes thousands of small windowed reads per municipality, one per tile. On a strip-organised source each of those reads pulls full-width rows off disk however narrow the tile is, which multiplies the amount of data read on the bigger municipalities.

That is why every raster this pipeline writes (DEM tiles, viewshed tiles and the merged output) uses `TILED=YES`. The 2026 SDE re-export of the 12 corrupted DEMs (§11) also uses `BLOCKXSIZE=512 BLOCKYSIZE=512`, set in a separate `gdal_translate` pass because ArcGIS Pro's export tools do not reliably offer that control.

## 10. The pipeline stages

Stages 1-3 compute the viewshed raster per municipality; stages 4-6 turn it into scores per home and per area.

| Stage | Script | Input | Output |
|---|---|---|---|
| Extract | `01_tile_dem.py` | `PROVINCE_DEM_VRT` + one municipality's own `.tif` (for its extent and grid) | `data/interim/dem_tiles/<name>/tile_RRRR_CCCC.tif` (1140x1140, buffered) + `tile_index.json` (every tile's buffered and inner extents, in map coordinates and pixel offsets) |
| Transform | `02_compute_viewsheds.py` | `tile_index.json` + `PROVINCE_TREES_GPKG` | `data/interim/viewshed_tiles/<name>/tile_RRRR_CCCC.tif` (1000x1000, cropped to the inner window, UInt32 counts) |
| Load | `03_merge_tiles.py` | all of one municipality's viewshed tiles | `data/processed/<name>_viewshed.tif`, one Cloud-Optimized GeoTIFF (COG) |
| Score | `04_score_buildings.py` | `<name>_viewshed.tif` + BAG buildings and addresses | `data/processed/<name>_woningen.gpkg`: every residential building with the maximum count in a 1.5 m ring outside its facade |
| Province | `05_merge_province.py` | all `<name>_woningen.gpkg` + gemeente/provincie boundaries | `<Province>_woningen.gpkg` (each building once, in its current municipality) + `<Province>_samenvatting.csv` |
| Areas | `06_area_summaries.py` | `<Province>_woningen.gpkg` + CBS wijken/buurten 2025 | `<Province>_gebieden.gpkg` (gemeenten, wijken, buurten) + CSVs |

`tile_index.json` is the hand-off between stages 1 and 2. It holds everything stage 2 needs about a tile's geometry, so nothing has to be derived again: the buffered extent (for the tree query), the inner extent (for cropping the output), the buffered tile's geotransform and the DEM's projection WKT.

Stage 3 builds a VRT over all of a municipality's viewshed tiles (a mosaic, which copies no data) and then runs a single `gdal.Translate(..., format="COG")`. GDAL's dedicated COG driver builds the overview pyramids in the same write and in the right byte order. The original approach, a plain GTiff translate followed by a separate `BuildOverviews()`, produced a file that GDAL itself flags as a broken COG, because the overviews end up after the full-resolution data instead of before it. Such a file still works, but loses what a COG is for: fast partial reads over HTTP. The overviews are resampled with `AVERAGE`, which represents a count field well when zoomed out, rather than `NEAREST`, which would pick one pixel per block. This only affects the overview levels, never the full-resolution values.

## 11. Resolved data problem: 12 corrupted source DEMs

Fixed on 2026-09-07. 12 of the 52 municipality source DEMs (`Barendrecht`, `Dordrecht`, `Goeree-Overflakkee`, `Gorinchem`, `Hardinxveld-Giessendam`, `Hellevoetsluis`, `Hendrik-Ido-Ambacht`, `Hoeksche Waard`, `Nissewaard`, `Papendrecht`, `Sliedrecht`, `Zwijndrecht`) turned out to hold only 0-3.4% real elevation data. The rest was exactly `0.0`, and no NoData flag was set, so the file metadata showed nothing wrong; only looking at the pixels revealed it.

An all-zero DEM is worse than an empty one. To the viewshed algorithm it is perfectly flat terrain on which nothing ever blocks a line of sight. The pipeline ran to completion, reported no errors and produced output that looked plausible and varied across space, since "trees within 30 m" alone still follows tree density. Nothing in the output raster could tell this case apart from a real result automatically.

The confirmed cause: whatever process produced these 12 `fme_input` files exported empty areas as `0.0` instead of a flagged NoData value. Given the near-total zero coverage, the whole raster was probably empty for these 12. The authoritative source (`Geo_raster.TOPOGRAFIE.AHN4_05M_RUW`, an enterprise SDE raster) covers these areas correctly.

The fix: `sde_reexport/export_from_sde.py`, an arcpy script run in ArcGIS Pro because this pipeline's GDAL tools have no SDE access, clipped the 12 extents again directly from the SDE source. It explicitly detected the NoData value instead of assuming one; the source had none set either, so the pipeline's own fallback value (`-9999`) was used. A `gdal_translate` pass then applied the pipeline's standard TIFF layout (§9: DEFLATE with predictor 3, 512x512 internal tiles, BigTIFF where needed) before the corrected files replaced the empty ones on R:\. After the fix, all 12 hold real elevation data with a correct NoData flag, 54-94% valid per municipality. The low end (for example Dordrecht and Goeree-Overflakkee) are river and island municipalities whose large water areas are correctly marked as NoData within their bounding boxes. `config.CORRUPTED_DEM_MUNICIPALITIES` is now empty, and these 12 are processed like all the others.

## 12. Current parameters

| Parameter | Value | Meaning |
|---|---|---|
| `MAX_DISTANCE` | 30.0 m | Viewshed radius per tree |
| `OBSERVER_HEIGHT` | 1.7 m | Fallback height when the DEM sample is outside the raster or implausible |
| `TREE_HEIGHT_BUFFER_RADIUS` | 1.5 m | Radius around each tree searched for its canopy top (observer height) |
| `TREE_GROUND_SEARCH_RADIUS` | 5.0 m | Radius for the local ground estimate (plausibility check only) |
| `OWN_CROWN_RADIUS` | 0.0 m (off) | Optional own-crown removal radius; needs building footprints when > 0 |
| `TREE_HEIGHT_MAX_PLAUSIBLE` | 35.0 m | Upper limit of a plausible tree (guards against power lines, pylons, buildings) |
| `TARGET_HEIGHT` | 1.8 m | Eye/window height for target pixels (the reference ArcGIS Pro method's `surface_offset`) |
| `CURVATURE_COEFF` | 0.0 | Flat earth (curvature is below a millimetre at 30 m) |
| `TILE_PIXELS` | 1000 px (500 m) | Inner tile size |
| `TILE_BUFFER_PX` | 70 px (35 m) | Halo width; must be `>= MAX_DISTANCE / pixel_size` |
| `NUM_WORKERS` | 4 | Parallel tile processing |
| Output raster dtype | UInt32 | Visible-tree count per pixel (fixed in stages 2 and 3) |
| DEM resolution / CRS | 0.5 m / EPSG:28992 | RD New |
| DEM source | AHN4, `_05M_RUW` | Raw, unfiltered surface model (DSM, not bare earth) |

`etl/config.py` holds the current values; `BENCHMARKS.md` has the measured time per municipality.

## 13. Resolved data problem: NoData in the input DEM ignored by `ViewshedGenerate`

Fixed on 2026-09-08. `gdal.ViewshedGenerate()` does "no special processing of input cells at a nodata value" (GDAL's documentation), so a NoData value in the input DEM (for example `-9999`) is treated as a real elevation. Wherever a tree's 30 m radius touched a gap in the source raster (usually water, bridges, or a municipality's own padding around its real coverage, see §11), this created a phantom cliff, or for a large positive value such as `9999` a phantom mountain, and gave wrong visibility without any error or warning.

The fix: `_fill_nodata()` in `01_tile_dem.py` runs on every tile right after it is cut from the province VRT, before any viewshed sees it:

1. Small gaps (under bridges, narrow water, holes in the data) are interpolated from the surrounding surface with `gdal.FillNodata()`, reaching at most `FILL_SEARCH_PX` (50 px = 25 m).
2. What remains are large gaps such as open water and wide rivers. They are set to the lowest valid value in the tile, a flat, low "water level" that can never block a line of sight.
3. A tile without any valid pixel (open sea, outside the province) keeps its NoData flag: there is nothing to fill from, and the raw NoData value must never pass for an elevation.

History (2026-09-11 to 09-27): the first version interpolated everything with `maxSearchDist=2000`, after 300 px had left a void of about 265 x 140 px unfilled on 2 of Delft's 208 tiles. That was correct, but a tile that was mostly water then spent about 30 s interpolating across it, adding up to hours for coastal municipalities (Brielle's tiling had done only 40 of 285 tiles after 13 minutes). The two-step fill above does Brielle in 290 s.

It was applied to the whole province in the full run of 2026-09-27/28, which re-tiled all 52 municipalities.

An AHN5 comparison run for Delft (`delft_benchmark/`) was removed on 2026-09-28: its source export had been converted to Int32, which truncated the heights to whole metres and made the comparison invalid. All production DEMs are Float32, in steps of about 0.1 mm.

## 14. Comparison with ArcGIS Pro's viewshed tools (2026-09-28)

> Read the last part of this section first. The first analysis below (2026-09-28) put the difference down to the viewshed engine. The single-tree tests of 2026-09-29 showed that the reference run's observer height was wrong instead. Its conclusions about the engine, the 3D radius and the Visibility crashes no longer hold and are kept as a record of what was tried.

A reference result for Delft (`visibility_Delft`) was made in ArcGIS Pro with the Spatial Analyst Visibility tool: AHN5 raw DSM, the observer at the DSM value at the tree point (bilinear, `ExtractValuesToPoints INTERPOLATE`) plus the tool's default observer offset of 1 m, a surface offset of 1.8 m and an outer radius of 30 m. Over all of Delft it correlates poorly with this pipeline (pixel r = 0.26; >= 3 trees: 53% in ArcGIS against 72% here), and it visibly shows less self-occlusion.

A test on identical inputs: on a 528 x 466 m area in Delft, with the same AHN5 DEM, the same 1,038 trees and the same observer heights, only the viewshed engine differs (`arcgis_tests/visibility_variants.py`):

| Engine | Mean trees visible | Pixels >= 3 trees |
|---|---:|---:|
| ArcGIS Visibility (reference run) | 5.52 | 72.8% |
| ArcGIS Viewshed2 / Geodesic Viewshed | 4.03 | 60.4% |
| GDAL `ViewshedGenerate` (this pipeline) | 3.42 | 52.6% |

- The engine explained most of the difference, and the tree-height rule little of it: canopy top against point + 1 m changes the mean by only 0.15-0.26 in both engines. On 3,000 Delft trees the canopy top is a median of 2 m higher than the reference observer, which by itself would make this pipeline see more trees, not fewer.
- GDAL follows Viewshed2's pattern closely (r = 0.96) but is somewhat stricter (0.6 trees fewer on average). Esri describes Viewshed2 as more accurate than its wavefront-based Viewshed tools. GDAL's algorithm (Wang et al. 2000) is also a wavefront method, yet sees fewer trees than Visibility, so the two wavefront implementations differ.
- Viewshed2's options hardly matter here: a 3D outer radius changes the mean by -0.19, perimeter sightlines by about 0.
- Visibility reads a positive radius as a 3D line-of-sight distance (negative means 2D), so the reference run's 30 m was 3D while this pipeline's is 2D. From a raised observer, 3D reaches slightly less far, so this does not explain why Visibility sees more.
- The observer offset matters a lot: with 0 instead of 1 m on the reference observer, GDAL's mean drops from 3.42 to 1.64.

So §6's statement that the canopy top avoids nearly all self-occlusion holds against the exact tree point within GDAL; against ArcGIS's Visibility tool, GDAL still occludes more. If an ArcGIS reference is needed, Viewshed2 is the closer one and, according to Esri, the more accurate.

The cause found (2026-09-29; GitHub issue #2, closed 2026-10-01): the reference run's observer height was wrong. An isolated tree and an isolated group of 5 trees (`arcgis_tests/one_tree.py`, `one_tree_arcgis.py`; WORKLOG section 9) show that Visibility reproduces `visibility_Delft` exactly (the same count on 100% of cells) with `RASTERVALU` as both `observer_elevation` and `observer_offset`. Every observer sat at 2 x RASTERVALU m NAP, not at RASTERVALU + 1 m as described above. Tall trees thus got their height twice, and the 7.4% of Delft trees below NAP had their observer below the surface. With the intended observer, Visibility, GDAL and an exact line-of-sight test agree closely (GDAL equals the exact test on 95.5-99.7% of cells on single trees). The benchmark's difference from this pipeline comes from its observer height, and the statements above that blame the engine no longer hold.

A rerun with the intended observer (`arcgis_tests/benchmark_corrected.py`, RASTERVALU plus an explicit 1 m offset, in 500 m tiles), inside Delft at least 30 m from its boundary, gives a mean of 3.28 trees visible and 47.3% of cells with >= 3 trees (original 6.16 / 69.5%; this pipeline 4.57 / 60.4%, pixel r 0.61 with the corrected benchmark). The gap that remains with this pipeline comes from its canopy-top observer and its own DEM: on a whole study area (Molenbuurt, Delft; WORKLOG section 12), Visibility with the benchmark's settings reproduces `visibility_Delft` on 100.0% of cells, and GDAL with the same DEM and observers gives the same >= 3 verdict on 98.3%. The Visibility crashes were caused by the repository path (a folder name starting with a digit and containing a hyphen), not by the tool, and the tool also ignores the sign of the outer radius (always 2D).
