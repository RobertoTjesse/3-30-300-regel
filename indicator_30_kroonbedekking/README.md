# The 30: 30% tree canopy where people live

For every residential building of the province, the share of land under tree crowns within 500 m of its facade. Buurten, wijken and gemeenten are built up from their homes:

```
canopy cover of a home = crown area on land / land area, within 500 m of the facade x 100%
an area                = share of its homes with canopy cover >= 30%
```

Crowns, land and water come from one raster: the technische totaalkaart 2024 by Friedenau Society, made from lidar at 0.25 m. Per pixel it gives the land cover and whether a tree crown hangs over it; its crowns are those of Boomkroonbedekking 2024 (BKB 2024). The homes are the residential buildings of the 3 (BAG), and the areas are the CBS Wijk- en Buurtkaart 2025, as on the rest of the map.

## Run it

From the repository root, with the OSGeo4W Python, after the 3's stage 6:

```
python indicator_30_kroonbedekking/etl/kroon_raster_5m.py
python indicator_30_kroonbedekking/etl/kroon_500m_woningen.py
```

| File | What | Zuid-Holland |
|---|---|---|
| `etl/kroon_raster_5m.py` | counts per 5 m cell the crown pixels on land and the land pixels (UInt16, 0-400 of the 20 x 20 source pixels) from the totaalkaart, in 1 km blocks in parallel; writes `config.KROON_GRID_TIF` (`data/interim/kroon_land_5m.tif`) | 5 min, 6,300 blocks |
| `etl/kroon_500m_woningen.py` | per building: buffers the footprint by 500 m and adds up both counts inside; then the share of homes with >= 30% and the mean per buurt, wijk and gemeente. `--gemeente Delft` runs one gemeente as a test | 3 min, 978,428 buildings |

Outputs in `data/processed/`: `<Province>_kroon500_woningen.csv` (pand_id, woningen, kroon in %, land_ha) and `<Province>_kroon500_gebieden.csv` (niveau, code, naam, gemeente, gebouwen, woningen, woningen_30, pct_30, gem_kroon). `web/build_tiles_30_300.py` reads both.

Settings in `indicator_3_bomen/etl/config.py`: `KROON_BUFFER_M` (500), `KROON_GRID_RES` (5 m), `VSK_TIF`, `LAND_CLASSES`, `CROWN_ON_LAND_CLASSES`.

## How a home is measured

1. **500 m from the facade.** The buffer goes around the footprint, so a large building gets a larger zone than a terraced house. The script buffers the footprint's convex hull. At 500 m the buffer covers any recesses in a footprint anyway, and the hull keeps the zone convex.
2. **Counting fast.** On the 5 m grid, the cells whose centre lies inside a convex zone form one unbroken run per grid row. With running sums along every row, each run costs one subtraction, so a building takes about 200 lookups however large its zone. Against brute force (burning the exact 500 m buffer of each footprint into the grid) the difference is at most 0.03 percentage points, on 45 Delft buildings including the 5 largest footprints.
3. **Land only.** Water, bridges over water, abroad and the sea do not count, in the denominator or under a crown. A home on a lake is not marked down for the lake.
4. **Up to the areas.** A building belongs to the buurt that holds its footprint's centroid, to that buurt's wijk, and to the gemeente stage 5 gave it, as in the 3. Every building counts with its number of homes.

The zone crosses buurt and gemeente boundaries, and the provincial border: the grid extends 600 m beyond the outermost home.

## Results for Zuid-Holland

32,405 of the 1,797,328 homes (1.8%) have at least 30% canopy within 500 m. The mean over all homes is 12.7%; half of the buildings are below 10.9%, and 1 in 100 is above 32%.

| | with any home >= 30% | where half the homes or more reach 30% |
|---|---|---|
| gemeenten (50) | 24 | 0 |
| wijken (511 with homes) | 88 | 6 |
| buurten (2,393 with homes) | 172 | 41 |

Wassenaar stands out: 27.3% of its homes reach 30%, with a mean of 26.0%. Next come Rijswijk (8.1%, mean 19.1%), Den Haag (6.1%) and Delft (5.6%, mean 17.2%). The lowest means are in Nieuwkoop (5.9%), Kaag en Braassem (6.1%) and Molenlanden (6.3%).

## The 30 per area (until 19 October 2026)

Before, the map showed the canopy cover of each buurt itself: crown area in the buurt divided by its CBS land area. That calculation is still here (`etl/bkb_per_gebied.py`, `etl/kroonbedekking_gebieden.py`, output `<Province>_kroonbedekking.csv`): 8.1% of the province's land lies under a crown, no gemeente reaches 30% (Wassenaar 27.9%), and 102 of the 2,509 buurten do. A pixel belongs to the buurt that holds its centre, so a crown on a boundary counts once.

The earlier calculation before that (NEO crown polygons on the CBS buurten of 2023) gave higher values: higher in all 39 gemeenten with complete data, by a median of 23%, with almost the same ranking (r = 0.99). It counted every NEO crown that touched a buurt in full and added up overlapping crowns.

## The technische totaalkaart 2024

`data/raw/vsk_2024_v3_zh.tif` is the technische totaalkaart 2024 (`vsk_2024_v3.tif`, Friedenau Society), clipped from the national file to Zuid-Holland's bounding box plus at least 1 km (x 42-132 km, y 405-485 km). It is a box on purpose: homes near the provincial border have zones that reach into the neighbouring provinces. It is a COG with DEFLATE level 9 (as the source) and nearest-neighbour overviews, because the values are classes. The national file and its legend are on `R:\ESRI\BEHEER\Projecten\_TEAM-GEO\2025-opdrachten-Q4\M25 10 00604 Klimaatmonitor\Data_bron\KEA\Totaalkaarten\` (`vsk_2024_v3_mapClassification.xlsx`, and the class names in `BasiskaartGroenVerharding.qgz`).

| Class | Meaning | Land | Crown on land |
|---|---|---|---|
| 0 | no data (the sea) | no | no |
| 1 | water | no | no |
| 2 | gras | yes | no |
| 3 | agrarisch | yes | no |
| 4 | half verhard | yes | no |
| 5 | verhard | yes | no |
| 6 | onverhard | yes | no |
| 7 | struiken | yes | no |
| 8 | gebouw | yes | no |
| 9 | brug boven water | no | no |
| 10 | boom zonder struik, verder onduidelijk | yes | yes |
| 11 | boom boven water | no | no |
| 12 | boom boven onverhard | yes | yes |
| 13 | boom boven agrarisch | yes | yes |
| 14 | boom boven half verhard | yes | yes |
| 15 | boom boven verhard | yes | yes |
| 16 | boom boven struiken | yes | yes |
| 17 | boom boven gebouw | yes | yes |
| 18 | boom boven brug | no | no |
| 19 | buitenland | no | no |

`config.LAND_CLASSES` and `config.CROWN_ON_LAND_CLASSES` hold these two columns. The separate `landbedekking_2024.tif` (8 classes) is no longer used: its local copy did not match its legend from class 6 on.

## Caveats

See the wiki page [Known issues](https://github.com/RobertoTjesse/3-30-300-regel/wiki/Known-issues).

More in `web/uitleg/30.html` (Dutch) and `web/uitleg/en/30.html` (English).
