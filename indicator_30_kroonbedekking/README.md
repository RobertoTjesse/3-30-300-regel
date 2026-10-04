# The 30: 30% tree canopy per neighbourhood

The share of land under tree crowns in every CBS buurt, wijk and gemeente of the province:

```
canopy cover = area under tree crowns / land area x 100%
```

The crowns come from Boomkroonbedekking 2024 (BKB 2024) by Friedenau Society, a raster made from lidar that covers the Netherlands at 0.25 m (1 = crown). The areas and their land area come from the CBS Wijk- en Buurtkaart 2025, the same areas the web map uses.

## Run it

From the repository root, with the OSGeo4W Python:

```
python indicator_30_kroonbedekking/etl/kroonbedekking_gebieden.py
```

It reads `config.BKB_TIF` (`data/raw/bkb_2024.tif`, 9.2 GB, gitignored) and `data/processed/<Province>_gebieden.gpkg` (from `indicator_3_bomen/etl/06_area_summaries.py`), fetches the CBS 2025 land area from PDOK once into `data/interim/cbs2025_oppervlak.gpkg`, and writes `data/processed/<Province>_kroonbedekking.csv`. `web/build_tiles_30_300.py` reads that file. Zuid-Holland takes about 4 minutes (6,225 tiles of 1 km, 5 processes).

| File | What |
|---|---|
| `etl/bkb_per_gebied.py` | counts crown pixels and all pixels per area, in 1 km tiles in parallel (about 100 MB per process). Also usable on its own for any polygon layer, e.g. `... bkb_per_gebied.py data/interim/buurten.gpkg buurten buurtcode out.csv --where "gemeentecode = 'GM0503'"` |
| `etl/kroonbedekking_gebieden.py` | the province run: buurten, then wijken and gemeenten as sums of their buurten, divided by the CBS land area |
| `fme/30_2024.fmw`, `fme/30_2025.fmw` | the earlier FME calculation (NEO crown polygons on the CBS buurten of 2023), kept for comparison; its result is in `data/fme_output/30_regel_v2.gdb` |

## How the pixels are assigned

A pixel belongs to the buurt that holds its centre, so a crown on a boundary counts for the part that lies in each buurt and nowhere twice. With 0.25 m pixels a boundary is at most 12.5 cm off. On Delft's 91 buurten the counted area differs from the polygon area by at most 4.1 m², and adding up the buurten gives the same wijk and gemeente totals as clipping those directly (to within 0.02 m²).

## Results for Zuid-Holland

8.1% of the land lies under a crown (21,854 ha of 269,840 ha). No gemeente reaches 30%; Wassenaar comes closest with 27.9%, Kaag en Braassem is lowest with 2.5%. 16 of the 518 wijken and 102 of the 2,509 buurten reach 30%.

The earlier FME calculation gave higher values: higher in all 39 gemeenten with complete FME data, by a median of 23%, with almost the same ranking (r = 0.99). It counted every NEO crown that touched a buurt in full and added up overlapping crowns; in Delft its surplus per buurt grows with the length of the buurt boundary and with the amount of crown. It also had no values for Voorne aan Zee and parts of Schiedam, Leiderdorp, Westland, Pijnacker-Nootdorp and Delft, because 2023 buurt codes were looked up in the CBS map of 2022.

## Caveats

- CBS gives land area in whole hectares, which can shift the percentage of a small buurt by a few points.
- A crown over water counts as crown, while the water is not part of the land area.
- What counts as a crown (e.g. a minimum height) is decided by how BKB 2024 was made.

More in `web/uitleg/30.html` (Dutch) and `web/uitleg/en/30.html` (English).
