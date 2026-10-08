# Web map and publishing

Live at https://robertotjesse.github.io/3-30-300-regel/

A 3 / 30 / 300 switch shows the three indicators on the same areas:

| Zoom | Areas |
|---|---|
| < 10 | gemeenten (current boundaries) |
| 10 - 11.5 | CBS wijken 2025 |
| 11.5 - 13 | CBS buurten 2025 |
| >= 13 | every residential building (the 3 and the 300; the 30 stays on buurten) |

Areas with fewer than 10 homes get no percentage. From zoom 11 the 300 also draws the parks and woods themselves, with a dark green outline that is filled once the buildings show; click one for its type, source and area. It is the same green the calculation uses: no water, at least 300 m², perimeter/area at most 0.35.

The site is in Dutch. `?lang=en`, or the link at the top of the panel, shows it in English, and every explanation page links to its counterpart in the other language. The (i) button lists the sources and their dates. The background is the PDOK grey map or an aerial photo, and the address search uses the PDOK Locatieserver, limited to the province. On a phone the panel sits at the bottom, so the map opens with the whole province above it.

## Files

| File | What |
|---|---|
| `web/index.html` | the map (MapLibre + PMTiles); settings (`PROVINCE`, `REPO`, colours, texts) at the top of the `<script>` |
| `web/uitleg/*.html`, `web/uitleg/en/*.html` | explanation pages in Dutch and English |
| `web/build_tiles.py` | `web/data/3.pmtiles` from `<Province>_gebieden.gpkg` and `<Province>_woningen.gpkg` |
| `web/build_tiles_30_300.py` | `web/data/30-300.pmtiles` (areas, homes) and `web/data/looptijd.pmtiles` (walking zones, entrances), from the 30 per area (`<Province>_kroonbedekking.csv`), the FME results of the 300 and the 3's areas |
| `web/build_tiles_groen.py` | `web/data/groen.pmtiles`: the 300's parks and woods, from the workbench's green input (`data/fme_input/groenvoorzieningen/groenkaart.gdb`) |
| `web/serve.py` | local preview with the HTTP Range requests PMTiles needs |

The walking zones and entrances have a tile file of their own. MapLibre re-processes every loaded tile of a source when one of its layers is switched on or off, and inside `30-300.pmtiles` that meant all the building tiles. Each tile file must stay under GitHub's 100 MB file limit; the build scripts warn when one does not.

## Publishing

1. Build the tiles: `python web/build_tiles.py`, `python web/build_tiles_30_300.py` and `python web/build_tiles_groen.py`.
2. Check locally: `python web/serve.py`, then open http://localhost:8000.
3. Copy `web/` without the `.py` files to the `gh-pages` branch.
4. Build this documentation site into the branch's `docs/` folder: `pip install -r docs/requirements.txt`, then `mkdocs build -d <gh-pages>/docs`.
5. Add an empty `.nojekyll` and push. GitHub Pages serves that branch: the map at https://robertotjesse.github.io/3-30-300-regel/ and the documentation at https://robertotjesse.github.io/3-30-300-regel/docs/.

The tile files are never committed to `master`.
