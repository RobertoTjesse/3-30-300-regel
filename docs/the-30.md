# The 30: canopy cover

Is at least 30% of every neighbourhood covered by tree crowns?

```
canopy cover = area under tree crowns ÷ land area × 100%
```

per CBS buurt, wijk and gemeente (2025), the same areas as the web map.

## How it is made

Open source, Python and GDAL, in [`indicator_30_kroonbedekking/etl/`](https://github.com/RobertoTjesse/3-30-300-regel/tree/master/indicator_30_kroonbedekking):

1. The crowns come from Boomkroonbedekking 2024 (BKB 2024) by Friedenau Society: a raster made from lidar that covers the Netherlands at 0.25 m, with 1 where a crown hangs over the cell. It is 1.9 trillion pixels, 9.2 GB compressed (`data/raw/bkb_2024.tif`).
2. `bkb_per_gebied.py` reads it in tiles of 1 km, burns the buurten into the same 0.25 m grid and counts the crown pixels per buurt. A pixel belongs to the buurt that holds its centre, so every bit of crown counts in exactly one buurt. The tiles run in parallel, at about 100 MB per process.
3. `kroonbedekking_gebieden.py` adds up the buurten into wijken and gemeenten (CBS codes nest: buurt BU04820101 lies in wijk WK048201 and gemeente GM0482) and divides by the CBS 2025 land area (`oppervlakteLandInHa`, fetched once from PDOK).
4. The result, `data/processed/ZuidHolland_kroonbedekking.csv`, goes into the map's tiles through `web/build_tiles_30_300.py`.

Zuid-Holland takes about 4 minutes. For Delft, the counted area differs from the polygon area by at most 4.1 m² per buurt, and adding up the buurten gives the same wijk and gemeente totals as clipping those directly (to within 0.02 m²).

## Results for Zuid-Holland

8.1% of the land lies under a crown: 21,854 ha of 269,840 ha. No gemeente reaches 30%. Wassenaar comes closest with 27.9%, followed by Rijswijk (22.3%) and Delft (20.9%); the lowest are Kaag en Braassem (2.5%), Midden-Delfland (3.1%) and Molenlanden (3.2%). 16 of the 518 wijken and 102 of the 2,509 buurten reach 30%.

## Choices

| Choice | Why |
|---|---|
| Per CBS buurt | The paper asks for 30% per neighbourhood. The CBS buurt is the smallest official unit, and it is the same nationwide. |
| A crown raster from lidar | The rule is about cover, the ground under crowns. The raster measures it directly, and overlapping crowns count once. |
| Pixel centre decides the buurt | No crown is counted twice. A boundary is at most 12.5 cm off at 0.25 m pixels. |
| Divide by land area | No trees grow on open water, so a buurt with a lake would otherwise come out too low. |
| Crowns 2024, areas 2025 | Nearly the same year, and the same areas as the map, so nothing has to be carried over between editions. |

## Compared with the earlier FME calculation

The map used to show an FME calculation (`fme/30_2024.fmw`, result `data/fme_output/30_regel_v2.gdb`). It added up the `CROWN_AREA` of every NEO crown polygon (2020-2022) that touched a CBS buurt of 2023 and divided by the land area. That gave higher values everywhere:

| | FME / NEO | BKB 2024 |
|---|---|---|
| Delft, same 90 buurten and land area | 24.7% | 21.0% |
| 39 gemeenten with complete FME data, together | 7.9% | 6.5% |

FME was higher in all 39 gemeenten, by a median of 23%, while the ranking hardly differs (r = 0.99 per gemeente, 0.95 per buurt in Delft). FME counted a crown on a boundary in full in each buurt it touched and added up overlapping crowns. In Delft its surplus per buurt grows with the length of the buurt boundary (about 33 ha of the 84 ha surplus) and with the amount of crown (about 48 ha). The two sources also differ in year (2020-2022 against 2024) and in how a crown is drawn. Confirming the split exactly needs the NEO crown polygons, which have not been exported yet.

The FME calculation also had no values for all of Voorne aan Zee and parts of Schiedam, Leiderdorp, Westland, Pijnacker-Nootdorp and Delft: it looked up 2023 buurt codes in the CBS map of 2022. Those areas now have values.

## Caveats

- CBS gives land area in whole hectares, which can shift the percentage of a small buurt by a few points.
- A crown over water counts as crown, while the water is not part of the land area.
- What counts as a crown (a minimum height, for example) depends on how BKB 2024 was made.
