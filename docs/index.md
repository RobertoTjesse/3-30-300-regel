# 3-30-300-regel

The 3-30-300 rule (Konijnendijk, 2023) asks for green close to where people live:

| | The rule | How it is measured here |
|---|---|---|
| 3 | 3 trees visible from every home | trees visible from just outside the facade, within 30 m, on the AHN surface model |
| 30 | 30% tree canopy in every neighbourhood | area under crowns (BKB 2024, from lidar) / land area per CBS buurt, wijk and gemeente |
| 300 | a park within 300 m of every home | home within a 5-minute walk (Valhalla isochrone) of an entrance of a park or wood >= 300 m² |

All three are computed for the province of Zuid-Holland and shown on one web map, per gemeente, wijk, buurt and home:
**https://robertotjesse.github.io/3-30-300-regel/**. The site is in Dutch, with an English version: the [map](https://robertotjesse.github.io/3-30-300-regel/?lang=en) and the explanation pages under `/uitleg/en/` (Dutch under `/uitleg/`).

## Results for Zuid-Holland

| | Result |
|---|---|
| 3 | 94.7% of homes see at least 3 trees and 2.0% see none (1.8 million homes in 978,428 buildings; 5.2 million trees) |
| 30 | 8.1% of the land lies under a crown. No municipality reaches 30% (Wassenaar comes closest, 27.9%; the lowest is Kaag en Braassem, 2.5%); 102 of the 2,509 buurten do |
| 300 | 78.9% of homes are within a 5-minute walk of green, 99.2% within 15 minutes |

## Pages

| Page | What |
|---|---|
| [Getting started](getting-started.md) | install, configure and run everything |
| [The 3 - visible trees](the-3.md) | method, choices, pipeline stages |
| [The 30 - canopy cover](the-30.md) | method (BKB 2024 per area), results, comparison with the earlier FME calculation |
| [The 300 - green within walking distance](the-300.md) | method, isochrones, entrances |
| [Data sources](data-sources.md) | every input, its source and date |
| [Validation against ArcGIS](validation-arcgis.md) | how the 3 was checked, and what was found |
| [Web map and publishing](web-map.md) | the map, the tile files, publishing to GitHub Pages |
| [QGIS validation project](qgis-validation.md) | the QGIS project with every layer |
| [Another province](another-province.md) | repeat the analysis elsewhere |
| [Known issues](known-issues.md) | open points and caveats |
| [Benchmarks and hardware](benchmarks.md) | run times per step, the machine used, memory and disk needs |

For more detail, see the repository's [`README.md`](https://github.com/RobertoTjesse/3-30-300-regel/blob/master/README.md), [`WORKLOG.md`](https://github.com/RobertoTjesse/3-30-300-regel/blob/master/WORKLOG.md) and, for the 3, [`ARCHITECTURE.md`](https://github.com/RobertoTjesse/3-30-300-regel/blob/master/indicator_3_bomen/ARCHITECTURE.md).
