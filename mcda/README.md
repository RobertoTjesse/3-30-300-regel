# Afwegingskaart: the 3, the 30 and the 300 weighed together

A multi-criteria analysis (MCDA) of the 3-30-300 rule for every home in Zuid-Holland, in two versions:

| | Static (`site/`) | Local (`local/`) |
|---|---|---|
| Runs on | GitHub Pages, any browser | `python mcda/local/server.py`, on this computer only |
| Weights, value functions, targets | yes | yes |
| Three ways to combine | yes | yes |
| Ranking of gemeenten, wijken, buurten | yes | yes |
| How sure is the ranking (100 random weight sets) | yes, in the browser | yes |
| Score of an area you draw or load (GeoJSON) | no | yes |
| What-if: new green, walking distances re-estimated | no | yes |
| Saved scenarios, difference map between two | link only | yes, `local/data/scenarios.json` |
| Export of the scores as GeoPackage or CSV | no | yes |
| The NEO tree points on the map | no (purchased data) | yes |

The weighing happens in the browser in both: it scores all 1,797,970 homes again on every change of a
slider, in about 50 ms. The local server only adds what needs a database or the purchased data.

## What it computes

Every home gets a score from 0 to 1 per criterion:

| Criterion | Measured | Yes/no | Gradual |
|---|---|---|---|
| Trees in view (the 3) | trees visible from the facade, within 30 m | 1 at or above the target | count / target, at most 1 |
| Canopy (the 30) | % canopy within 500 m of the building | same | % / target, at most 1 |
| Green nearby (the 300) | walking distance to the nearest entrance of public green | 1 within the target | 1 up to the target, then linear to 0 at a distance you set (at most 1,275 m, the last the 300 measures) |

These are min-max normalisations with fixed bounds: 0 is the worst measured value, 1 the target, nothing extra beyond
the target (OECD/JRC, *Handbook on Constructing Composite Indicators*, 2008). The three scores are combined with the
weights by one of three methods:

- **Weighted mean** (compensatory): `Σ w·s / Σ w`.
- **Weighted product** (partly compensatory): `Π s^(w/Σw)`. A zero on any weighted criterion gives zero.
- **Weakest link** (non-compensatory): the weighted minimum of Yager (1978), `min over k of max(1 − w_k/w_max, s_k)`.
  The heaviest criterion counts in full; one with half that weight can pull the score down to 0.5 at most.

An area's score is the mean of its homes. Yes/no on all three with the weakest link and equal weights gives the share of
homes meeting the whole rule. Every popup and the ranking also show that share ("haalt alle drie") without weights.

**How sure is the ranking.** The weights are a choice, not a measurement. The browser draws 100 weight sets around the
chosen ones (±25%, ±50%, or anything from Dirichlet(1,1,1)), ranks the areas each time, and shows per area how often it
falls in the worst 20% and its rank interval (5th–95th percentile). `site/worker.js` does this off the main thread.

**What-if new green** (local). For every home within 1,275 m of a polygon you draw, the walking distance is estimated as
the straight-line distance times a detour factor (default 1.3), and replaces the measured route where it is shorter. This
is an estimate: the new green's entrances and the paths to it are not known. The exact way is to add the polygon to the
300's green areas and run `indicator_300_park/etl` again.

## Choices

- **No build step, no framework.** Plain HTML, CSS and JavaScript, like `web/`. The page is small enough that a
  framework would add more than it saves, and anyone who can read the other map can read this one.
- **MapLibre GL JS 5.24.0 and PMTiles 4.5.0**, the same libraries as the main map. The areas get their colour through
  feature state (no new tiles per weighting); the buildings compute their own score from their tile properties with a
  MapLibre expression that mirrors `mcda.js`.
- **The homes as three typed arrays** (`woningen.bin.gz`, 3.7 MB) instead of DuckDB-WASM or Parquet: the analysis needs
  three small integers per home and a loop, which a lookup table per criterion makes fast. The browser unzips it with the
  built-in `DecompressionStream`.
- **The local server is the Python standard library plus DuckDB** with its spatial extension, both already in OSGeo4W.
  No FastAPI, no PostGIS, no install.
- **Design** after the ui-ux-pro-max skill's design system for a dense government dashboard: Swiss/minimal, Atkinson
  Hyperlegible (made for legibility), slate with one blue accent, sequential YlGn for scores, OrRd for "surely at the
  bottom", a diverging scale for differences, a sortable table as the accessible counterpart of the map, visible focus,
  reduced motion respected.

## Files

| File | What |
|---|---|
| `build_data.py` | builds `site/data/` (areas, homes, vector tiles) and `local/data/mcda.duckdb` from the results of the 3, the 30 and the 300 |
| `site/index.html`, `style.css`, `app.js` | the map: controls, layers, ranking, detail |
| `site/mcda.js` | the arithmetic: value functions, the three methods, sums per area (no DOM) |
| `site/worker.js` | the sensitivity runs |
| `local/server.py` | static files with HTTP Range, and the API: `gebied`, `watals`, `bomen`, `export`, `scenario` |
| `local/local.js`, `local.css` | the local panel |

## Run

```bash
# OSGeo4W Python, after the 3, the 30 and the 300 have run (about 10 minutes)
python mcda/build_data.py
# static: any web server that supports HTTP Range requests, e.g.
python mcda/local/server.py          # also serves the static map, with the local extras
# open http://127.0.0.1:8765
```

To publish the static version, copy `site/` (with `site/data/`) to the root of a GitHub Pages repository.
`kaart.pmtiles` stays under GitHub's 100 MB limit because the buildings stop at zoom 15 (the map over-zooms them).
