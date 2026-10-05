# Known issues

## Open

| Issue | Status |
|---|---|
| [#1 Tree status](https://github.com/RobertoTjesse/3-30-300-regel/issues/1) | 21% of the counted trees are marked "disappeared, small tree" in the source register (`current_st`), 3% "not seen once". All statuses count for now; whether to exclude them is open. |
| The 30: land area in whole hectares | CBS rounds the land area to whole hectares, which can shift the percentage of a small buurt by a few points. |
| The 300: 300 m² threshold | Far below the WHO's 1 ha, so results are more favourable than under the strict norm. |

## Caveats of the 3

- Eye height is at street level only. The view from higher floors is not computed, and residents of flats often see more (or other) trees.
- Only trees within 30 m count. A row of trees across a wide canal is visible, but falls outside the rule as measured here.
- Everything in the surface model blocks the view, including hedges, fences, bus shelters and other trees' crowns. A dense 2 m hedge counts as a wall.
- The surface model and trees are from 2020-2022 and the homes from 2026, so new-build homes (3-5%) appear in their old surroundings.
- The surface model has no point classification. A power line can be mistaken for a crown; the height check catches most of these.
- A value of `0` in a viewshed raster means "no tree within 30 m", not "no data". No NoData value is set on purpose.

## Resolved

- 2026-10-04: the 30 is now computed from BKB 2024 (Friedenau Society) on the map's own CBS 2025 areas. That ends the earlier FME calculation's missing values (2023 codes looked up in the 2022 CBS map) and its double counting of crowns on buurt boundaries. See [The 30 - canopy cover](the-30.md).
- 2026-10-01: [#2 ArcGIS comparison](https://github.com/RobertoTjesse/3-30-300-regel/issues/2) closed. The benchmark saw more trees because its observer sat at 2 x RASTERVALU. With the same DEM and observers, ArcGIS and GDAL agree. See [Validation against ArcGIS](validation-arcgis.md).
- 2026-09-07: 12 of 52 municipality DEMs were mostly exactly 0 without a NoData flag, a flat surface on which the viewshed saw everything. They were re-exported from the SDE source (`indicator_3_bomen/sde_reexport/`).
- 2026-09-08 / 09-27: `ViewshedGenerate` read NoData in the DEM as real terrain (phantom cliffs). Stage 1 now fills every tile.
- 2026-09-27: `observerHeight` is added to the DEM value at the observer's pixel. It had been passed as an absolute height, which put observers 1.4-2x too high. Fixed, and all municipalities rerun.
- 2026-09-27: the tree height check used absolute NAP heights and rejected ordinary trees in polders and dunes. It now uses the height above local ground.
