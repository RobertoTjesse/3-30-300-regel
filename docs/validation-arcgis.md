# Validation against ArcGIS

An earlier Delft result made with ArcGIS Pro's Visibility tool (`visibility_Delft`, the "benchmark") saw far more trees than this pipeline. [Issue #2](https://github.com/RobertoTjesse/3-30-300-regel/issues/2) tracked the comparison and was closed on 2026-10-01: the cause was the benchmark's observer height.

## What was found

1. The viewshed engines agree. On single trees and a small group, with the same DEM and the same observer, GDAL, ArcGIS Visibility and an exact line-of-sight test (`arcgis_tests/one_tree.py`) give nearly the same result: GDAL has the same count as the exact test on 95.5-99.7% of cells.
2. The benchmark's observer height was wrong. Its tree height field `RASTERVALU` was passed as both the observer elevation and the observer offset, so every observer sat at 2 x RASTERVALU m NAP instead of RASTERVALU + 1 m. With those settings Visibility reproduces the benchmark on 100% of cells for an isolated tree and an isolated group. Tall trees got their height twice, and the 7.4% of trees below NAP had their observer below the surface.
3. A corrected benchmark (`arcgis_tests/benchmark_corrected.py`, with the intended observer, run in 500 m tiles) was compared with the original and with this pipeline, inside Delft at least 30 m from its boundary:

| | Mean trees visible | Cells >= 3 trees |
|---|---:|---:|
| Corrected benchmark (ArcGIS, point + 1 m) | 3.28 | 47.3% |
| Original benchmark (2 x RASTERVALU) | 6.16 | 69.5% |
| This pipeline (canopy-top observer) | 4.57 | 60.4% |

4. A whole study area was reproduced: Molenbuurt, Delft (BU05031407), with the colleague's own AHN5 DSM and tree layer. Visibility with the benchmark's settings gives the same count as `visibility_Delft` on 100.0% of the 515,056 land cells. GDAL with the same observers gives the same >= 3-trees verdict on 98.3% of cells, and the same count on 79.2%.

## The remaining difference

The pipeline still sees more than the corrected benchmark (4.57 vs 3.28 trees) because of its inputs. It puts the observer at the canopy top, a median of about 2 m higher than point + 1 m, and it runs on its own AHN4 DEM. With the same DEM and observers the two engines agree (point 4).

## Practical notes

- The ArcGIS scripts are run by hand from the ArcGIS Pro Python Command Prompt, with Pro closed. Started inside Pro, they crashed it.
- The old GRID engine behind Viewshed and Visibility fails on paths such as `D:\Repositories\3-regel` (a folder name that starts with a digit and contains a hyphen), so the scripts first copy their inputs to a plain folder under `D:\Temp`.
- All comparison layers are in the [QGIS validation project](qgis-validation.md).
