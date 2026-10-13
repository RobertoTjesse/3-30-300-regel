# 3-30-300-regel: the 3-30-300 rule per home, buurt, wijk and gemeente

*Nederlands: [README.nl.md](README.nl.md)* · Documentation: the [wiki](https://github.com/RobertoTjesse/3-30-300-regel/wiki)

The 3-30-300 rule (Konijnendijk, 2023) asks for green in three ways:

| | Rule | Measured here as | How it is made | Folder |
|---|---|---|---|---|
| 3 | 3 trees visible from every home | trees visible from just outside the facade (30 m radius, AHN surface model) | Python + GDAL, this repository | [`indicator_3_bomen/`](indicator_3_bomen/) |
| 30 | 30% tree canopy in every neighbourhood | area under crowns (BKB 2024, Friedenau Society, from lidar) / land area per CBS buurt, wijk and gemeente (2025) | Python + GDAL, this repository | [`indicator_30_kroonbedekking/`](indicator_30_kroonbedekking/) |
| 300 | a park within 300 m of every home | walking distance from the home to the nearest entrance of a park or other public green >= 300 m2 | QGIS only: GDAL + GRASS, checked with Valhalla | [`indicator_300_park/`](indicator_300_park/) |

All three are computed for the province of Zuid-Holland and published as one web map with a 3 / 30 / 300 switch, per gemeente, wijk, buurt and home: **https://robertotjesse.github.io/3-30-300-regel/**. The site and its explanation pages are in Dutch, with an English version ([map](https://robertotjesse.github.io/3-30-300-regel/?lang=en), `/uitleg/en/`). Any province can be run without code changes ([Another province](https://github.com/RobertoTjesse/3-30-300-regel/wiki/Another-province)). All three run on QGIS and the components that come with it (GDAL, GRASS, QGIS's Python); no other software or licence is needed.

## What the results say

Zuid-Holland, latest runs:

| | Result |
|---|---|
| 3 | 85.9% of homes see at least 3 trees; 5.0% see none |
| 30 | 8.1% of the land lies under a tree crown. 102 of the 2,509 buurten reach 30%, and no gemeente does (Wassenaar comes closest with 27.9%) |
| 300 | 36.7% of homes lie within a 300 m walk of a park or other public green, 56.0% within 5 minutes and 97.8% within 15 minutes. Per gemeente from 20.3% (Voorne aan Zee) to 86.7% (Oegstgeest) within 5 minutes |

The figures per gemeente, wijk and buurt are sound. For a single home the map gives an indication, and the assumptions explain most of the cases where it disagrees with what a resident sees.

## What the results assume

The paper leaves a lot open: from where you look, how far, what counts as a tree or a park. Every gap had to be filled with a choice, and each choice moves the numbers. The choices that move them most, with the direction they push the result, are on the explanation pages: [the 3](https://robertotjesse.github.io/3-30-300-regel/uitleg/en/3.html#aannames), [the 30](https://robertotjesse.github.io/3-30-300-regel/uitleg/en/30.html#aannames), [the 300](https://robertotjesse.github.io/3-30-300-regel/uitleg/en/300.html#aannames), and for all three together [the overview](https://robertotjesse.github.io/3-30-300-regel/uitleg/en/#alle-drie) (Dutch versions under `/uitleg/`). Open issues are listed in the wiki under [Known issues](https://github.com/RobertoTjesse/3-30-300-regel/wiki/Known-issues).

## Repository layout

```
330300regel/
├── README.md                     this file: the project as a whole
├── README.nl.md                  a short introduction in Dutch
├── WORKLOG.md                    what was done and found, step by step
├── indicator_3_bomen/            the 3: full pipeline, see its README
│   ├── etl/                      stages 01-06, config.py, config_local.py (gitignored)
│   ├── arcgis_tests/             experiments: ArcGIS comparisons, exact test (local only, not on GitHub)
│   ├── sde_reexport/             one-off re-export of corrupted DEMs
│   ├── fme/                      the original FME workbenches for the 3
│   └── README.md, ARCHITECTURE.md, BENCHMARKS.md, IMPROVEMENTS.md
├── indicator_30_kroonbedekking/  the 30: etl/ (BKB 2024 per area) + the earlier FME workbenches
├── indicator_300_park/           the 300: etl/ (GDAL + GRASS from QGIS; Valhalla as a check) + the earlier FME workbenches
├── web/                          the web map for all three (published site)
├── docs/bronnen/                 source documents (Konijnendijk 2023, the Cobra Groeninzicht handbook)
├── qgis_validation/              builds the QGIS validation project for all three
├── data/                         all data (gitignored), see the wiki page "Data sources"
└── logs/                         run logs (gitignored)
```

Folder names start with a letter and contain no hyphens because the old GRID engine behind ArcGIS's Viewshed and Visibility tools fails on paths like `D:\Repositories\3-regel`. For the same reason the local clone is called `330300regel`, although the GitHub repository is `3-30-300-regel`. That folder name still starts with a digit, so the ArcGIS scripts copy their inputs to a plain work folder under `D:\Temp` before running.

## Getting started

You need QGIS (the OSGeo4W installation; nothing comes from pip) and access to the source data. Clone into a folder without hyphens or spaces (`git clone https://github.com/RobertoTjesse/3-30-300-regel.git 330300regel`) and run everything from the repository root with QGIS's Python. The details are in the [wiki](https://github.com/RobertoTjesse/3-30-300-regel/wiki):

| Wiki page | What |
|---|---|
| [Getting started](https://github.com/RobertoTjesse/3-30-300-regel/wiki/Getting-started) | setup, settings, and what to run for the 3, the 30, the 300, the map tiles and the QGIS project |
| [Data sources](https://github.com/RobertoTjesse/3-30-300-regel/wiki/Data-sources) | every input with its date, and where the files are |
| [Web map and publishing](https://github.com/RobertoTjesse/3-30-300-regel/wiki/Web-map-and-publishing) | the map, its tile files, local preview and publishing to `gh-pages` |
| [QGIS validation project](https://github.com/RobertoTjesse/3-30-300-regel/wiki/QGIS-validation-project) and [Validation against ArcGIS](https://github.com/RobertoTjesse/3-30-300-regel/wiki/Validation-against-ArcGIS) | checking the results |
| [Another province](https://github.com/RobertoTjesse/3-30-300-regel/wiki/Another-province) | running it for another province |

## Documents

| Document | What |
|---|---|
| [Wiki](https://github.com/RobertoTjesse/3-30-300-regel/wiki) | overview for new readers: getting started, the three indicators, data, validation, known issues, run times |
| [`WORKLOG.md`](WORKLOG.md) | chronological log of the work and findings |
| [`indicator_3_bomen/README.md`](indicator_3_bomen/README.md) | the 3: setup and pipeline |
| [`indicator_3_bomen/ARCHITECTURE.md`](indicator_3_bomen/ARCHITECTURE.md) | the 3: design decisions in depth |
| [`indicator_3_bomen/BENCHMARKS.md`](indicator_3_bomen/BENCHMARKS.md) | the 3: run times (generated) |
| [`indicator_3_bomen/IMPROVEMENTS.md`](indicator_3_bomen/IMPROVEMENTS.md) | improvements over the original prototype, and the list of planned work for all three |
| [Comparison with Cobra Groeninzicht](https://github.com/RobertoTjesse/3-30-300-regel/wiki/Comparison-with-Cobra-Groeninzicht) (wiki) | comparison with Cobra Groeninzicht's 3+30+300 and the Yggdrasil handbook: method, mark per building, what each side misses |
| [`indicator_30_kroonbedekking/README.md`](indicator_30_kroonbedekking/README.md) | the 30 |
| [`indicator_300_park/README.md`](indicator_300_park/README.md) | the 300 |
| `web/uitleg/` | explanation pages for the public (Dutch; English in `web/uitleg/en/`) |
| [`README.nl.md`](README.nl.md) | a short introduction in Dutch |

## History

The project started as `RobertoTjesse/3-regel` (the pipeline of the 3). The web map was then built in an earlier repository with this same name, `3-30-300-regel`. On 2026-09-29 both were combined into this repository with their full history and restructured into one folder per indicator, and the two older repositories were deleted. Their issues were transferred here ([issues](https://github.com/RobertoTjesse/3-30-300-regel/issues)), and the first published site with only the 3 is kept in the branch `archief/3-regel-gh-pages`.
