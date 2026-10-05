# 3-30-300-regel: de 3-30-300-regel per woning, buurt, wijk en gemeente

*English: [README.md](README.md)*

De 3-30-300-regel (Konijnendijk, 2023) vraagt op drie manieren om groen:

| | Regel | Hier gemeten als | Hoe gemaakt | Map |
|---|---|---|---|---|
| 3 | 3 bomen zichtbaar vanuit elke woning | bomen zichtbaar vanaf direct voor de gevel (straal 30 m, AHN-oppervlaktemodel) | Python + GDAL, deze repository | [`indicator_3_bomen/`](indicator_3_bomen/) |
| 30 | 30% boomkronen in elke buurt | oppervlak onder kronen (BKB 2024, Friedenau Society, uit lidar) / landoppervlak per CBS-buurt, -wijk en gemeente (2025) | Python + GDAL, deze repository | [`indicator_30_kroonbedekking/`](indicator_30_kroonbedekking/) |
| 300 | een park binnen 300 m van elke woning | woning binnen 5 minuten lopen (Valhalla-isochroon) van een park of bos van ≥ 300 m² | FME + PostGIS + Valhalla | [`indicator_300_park/`](indicator_300_park/) |

Alle drie zijn berekend voor de provincie Zuid-Holland en samen gepubliceerd als één webkaart met een 3 / 30 / 300-schakelaar, per gemeente, wijk, buurt en woning: **https://robertotjesse.github.io/3-30-300-regel/**. De site en de uitlegpagina's zijn Nederlands, met een Engelse versie ([kaart](https://robertotjesse.github.io/3-30-300-regel/?lang=en), `/uitleg/en/`). De 3 en de 30 zijn zonder codewijzigingen voor een andere provincie te draaien ([Andere provincie](#andere-provincie)). De 300 hangt nog van FME af; die naar open-source Python overzetten staat gepland (`indicator_3_bomen/IMPROVEMENTS.md`).

## Wat de uitkomsten zeggen

Zuid-Holland, laatste runs:

| | Uitkomst |
|---|---|
| 3 | 94,7% van de woningen ziet minstens 3 bomen; 2,0% ziet er geen |
| 30 | 8,1% van het land ligt onder een boomkroon. 102 van de 2.509 buurten halen 30%, geen enkele gemeente (Wassenaar komt het dichtst bij met 27,9%) |
| 300 | 78,9% van de woningen ligt binnen 5 minuten lopen van een park of bos; 99,2% binnen 15 minuten |

De cijfers per gemeente, wijk en buurt zijn betrouwbaar. Voor één woning geeft de kaart een indicatie, en de aannames hieronder verklaren de meeste gevallen waarin die afwijkt van wat een bewoner ziet.

## Wat de uitkomsten aannemen

Het artikel laat veel open: vanaf waar je kijkt, hoe ver, wat telt als boom of park. Elk gat is met een keuze gevuld, en elke keuze verschuift de cijfers. Dit zijn de keuzes die het meest uitmaken, met de kant waarop ze de uitkomst duwen. De uitlegpagina's (`web/uitleg/`, Nederlands en Engels) geven de redenen en de alternatieven die zijn afgewogen.

### De 3

- **Eén goede plek is genoeg.** Een pand krijgt de hoogste telling van alle vakjes van 0,5 x 0,5 m in een ring van 1,5 m eromheen. Ziet één vakje in de achtertuin drie bomen, dan haalt het hele pand de norm, ook als de woonkamer op een blinde muur uitkijkt. De regel vraagt of je vanuit je woning bomen ziet, niet vanuit elk raam. *Duwt de uitkomst omhoog.*
- **Elke woning in een pand krijgt de score van het pand.** Alle 80 flats in een blok delen één getal, en de cijfers per gebied tellen woningen, dus dat blok weegt 80 keer zo zwaar als een vrijstaand huis.
- **Het zicht wordt op straatniveau berekend.** De plek ligt 1,8 m boven de grond direct voor de gevel. Hogere verdiepingen en balkons, die over heggen en auto's heen kijken, worden niet berekend. *Duwt de uitkomst omlaag voor flats.*
- **Een boom is zichtbaar als de top van de kroon zichtbaar is.** De zichtlijn loopt van de top van de kroon (het hoogste punt van het oppervlak binnen 1,5 m van het boompunt) naar de plek voor de woning. Een woning die alleen de zijkant van een kroon of een overhangende tak ziet, terwijl de top achter een dak verdwijnt, telt die boom niet. Een woning die net de top boven een dak uit ziet, telt hem wel. Vanaf het boompunt zelf zou de waarnemer in zijn eigen kroon staan; in Delft zag 35,5% van de bomen dan vrijwel niets.
- **De 30 m wordt vanaf de stam gemeten.** De afstand loopt van het boompunt naar de plek voor de woning, niet vanaf de rand van de kroon. Een grote boom met de stam op 32 m telt niet mee, ook als de kroon dichterbij hangt.
- **Elke boom telt even zwaar.** Een jonge straatboom telt net zo hard als een oude eik; het "well-established" uit het artikel wordt niet gecontroleerd. Bomen die in de bron als "verdwenen, kleine boom" staan (21% van de getelde) tellen mee ([issue #1](https://github.com/RobertoTjesse/3-30-300-regel/issues/1)). *Duwt de uitkomst omhoog.*
- **Alles in het hoogtemodel blokkeert het zicht.** Gebouwen, heggen, schuttingen, bushokjes en de kronen van andere bomen zijn massief. Een dichte heg telt als muur, ook in de winter. *Duwt de uitkomst omlaag.*
- **De omgeving is van 2020-2022, de woningen van 2026.** Een woning die sindsdien is gebouwd, krijgt een score op de oude situatie (3 tot 5% van de woningen).

### De 30

- **De buurt is de eenheid, zoals het artikel vraagt.** Het cijfer van een wijk of gemeente is het kroonoppervlak van al haar buurten gedeeld door hun totale landoppervlak. Grote groene buurten kunnen dat optillen terwijl andere buurten ver onder 30% blijven, dus lees de kaart op buurtniveau.
- **Elk vakje van 25 cm telt één keer, in één buurt.** Een vakje hoort bij de buurt waarin het midden ligt. Overlappende kronen tellen één keer, omdat de grond eronder ook maar één keer bestaat.
- **Kroonoppervlak wordt gedeeld door landoppervlak.** Water telt niet mee in het oppervlak, dus een buurt met een plas wordt daar niet op afgerekend. Een kroon boven een gracht telt wel als kroon, wat buurten met veel bomen langs het water iets optilt. *Duwt de uitkomst een beetje omhoog.*
- **Wat een kroon is, bepaalt BKB 2024.** De kaart is gemaakt uit lidar en bevat straat- en tuinbomen; een eventuele minimale boomhoogte volgt uit hoe die kaart is gemaakt.
- **CBS geeft het landoppervlak in hele hectares.** Bij een kleine buurt kan dat het percentage een paar punten verschuiven.

### De 300

- **Een klein park telt mee.** Groen telt vanaf 300 m², terwijl de WHO-norm achter de regel 1 ha is. Stroken smaller dan ongeveer 6 m (omtrek / oppervlak boven 0,35), zoals bermen, vallen af. *Duwt de uitkomst omhoog; een berekening met 1 ha is de eerste geplande verbetering.*
- **De zone raken is genoeg.** Een pand telt als een deel ervan in de 5-minutenzone ligt. Bij een lange flat haalt de hele flat de norm als één uiteinde erin ligt. *Duwt de uitkomst omhoog.*
- **Het lopen begint bij een ingang.** Een ingang is een plek waar een pad uit OpenStreetMap de rand van het groen kruist. Groen zonder pad erin heeft geen ingang en telt niet mee, en een kortere route die niet in OSM staat, ontbreekt ook in de berekening. *Duwt de uitkomst omlaag.*
- **5 minuten is ongeveer 425 m langs de straat.** Valhalla loopt 5,1 km/u. Met de gebruikelijke omweg van straten ten opzichte van de rechte lijn is dat ongeveer 300 m hemelsbreed, en water, spoor en snelwegen worden meegenomen.
- **Openbare toegang is niet gecontroleerd.** Een pad het groen in geldt als toegang. *Duwt de uitkomst omhoog.*
- **De woningen komen uit een andere BAG-kopie** (9 december 2025) dan die van de 3 (27 september 2026), dus het aantal woningen per gebied verschilt een beetje tussen de twee.

### Alle drie

- Alleen woningen zijn gemeten. Het artikel vraagt ook om zichtbare bomen vanuit scholen en werkplekken.
- De bronnen zijn van verschillende jaren: bomen en hoogtemodel 2020-2022 (de 3), kronen 2024 (de 30), groen 2024 en paden 2025 (de 300).
- De 3 en de 30 gebruiken andere boomgegevens (NEO-boompunten tegenover de kroonkaart van BKB), dus een boom kan bij de een meetellen en bij de ander niet.
- Een pand hoort bij de buurt en gemeente waarin zijn middelpunt ligt. Voor de 3 en de 300 krijgen gebieden met minder dan 10 woningen geen percentage op de kaart.

De technische documenten in de mappen (`ARCHITECTURE.md`, `WORKLOG.md`, de README's per indicator) en de code zijn in het Engels.

## Indeling van de repository

```
330300regel/
├── README.md                     het project als geheel (Engels)
├── README.nl.md                  dit bestand
├── WORKLOG.md                    wat er is gedaan en gevonden, stap voor stap
├── indicator_3_bomen/            de 3: de volledige pipeline, zie de README daar
│   ├── etl/                      stappen 01-06, config.py, config_local.py (gitignored)
│   ├── arcgis_tests/             experimenten: vergelijkingen met ArcGIS, exacte test (alleen lokaal, niet op GitHub)
│   ├── sde_reexport/             eenmalige herexport van beschadigde hoogtemodellen
│   ├── fme/                      de oorspronkelijke FME-workbenches van de 3
│   └── README.md, ARCHITECTURE.md, BENCHMARKS.md, IMPROVEMENTS.md
├── indicator_30_kroonbedekking/  de 30: etl/ (BKB 2024 per gebied) + de eerdere FME-workbenches
├── indicator_300_park/           de 300: FME-workbenches + README
├── web/                          de webkaart voor alle drie (gepubliceerde site)
├── qgis_validation/              bouwt het QGIS-validatieproject voor alle drie
├── data/                         alle gegevens (gitignored), zie "Gegevens"
└── logs/                         logbestanden van runs (gitignored)
```

Mapnamen beginnen met een letter en hebben geen streepjes, omdat de oude GRID-rekenkern achter de ArcGIS-tools Viewshed en Visibility vastloopt op paden als `D:\Repositories\3-regel`. Daarom heet de lokale kopie ook `330300regel`, terwijl de GitHub-repository `3-30-300-regel` heet. Die mapnaam begint nog wel met een cijfer, dus de ArcGIS-scripts kopiëren hun invoer eerst naar een gewone werkmap onder `D:\Temp`.

## Aan de slag

Je hebt een QGIS / OSGeo4W-installatie nodig (de Python daarvan heeft GDAL/OGR en, voor het validatieproject, PyQGIS; niets komt van pip) en toegang tot de brongegevens (zie [Gegevens](#gegevens)). Voor de vergelijkingen met ArcGIS is ook ArcGIS Pro met Spatial Analyst nodig.

1. Clone naar een map zonder streepjes of spaties:
   `git clone https://github.com/RobertoTjesse/3-30-300-regel.git 330300regel`
2. Kopieer `indicator_3_bomen/etl/config_local.example.py` naar `indicator_3_bomen/etl/config_local.py` en vul `OSGEO4W_ROOT` en `VIEWANALYSE_DIR` in. Dit bestand is gitignored. De gedeelde instellingen staan in `indicator_3_bomen/etl/config.py`, dat elk script in de repository importeert.
3. Draai alles vanuit de hoofdmap van de repository met die Python, bijvoorbeeld `C:\...\OSGeo4W\apps\Python312\python.exe indicator_3_bomen\etl\01_tile_dem.py`.

Wat te draaien:

| Doel | Commando (vanuit de hoofdmap) |
|---|---|
| De 3, alle stappen | `indicator_3_bomen/etl/01_tile_dem.py` t/m `06_area_summaries.py`; zie [`indicator_3_bomen/README.md`](indicator_3_bomen/README.md#pipeline) |
| De 30 | `python indicator_30_kroonbedekking/etl/kroonbedekking_gebieden.py` (na stap 6 van de 3, die de gebieden maakt); zie [`indicator_30_kroonbedekking/README.md`](indicator_30_kroonbedekking/README.md) |
| De 300 | de workbench in FME draaien; resultaten in `data/fme_output/` (zie de README daar) |
| Kaarttegels | `python web/build_tiles.py` (de 3), `python web/build_tiles_30_300.py` (de 30 en 300) en `python web/build_tiles_groen.py` (het groen van de 300) |
| Kaart lokaal bekijken | `python web/serve.py`, dan http://localhost:8000 |
| QGIS-validatieproject | `C:\...\OSGeo4W\bin\python-qgis-ltr.bat qgis_validation\build_project.py` |

## Gegevens

Alles onder `data/` is gitignored:

| Pad | Wat | Bron |
|---|---|---|
| `VIEWANALYSE_DIR` (niet gekopieerd) | per gemeente: AHN-DSM `.tif` + NEO-bomen `.gpkg`, 119 GB | `R:\ESRI\BEHEER\Projecten\Tijdelijk_Roberto\3-30-300\fme_input\viewanalyse` |
| `data/raw/` | `bkb_2024.tif`: Boomkroonbedekking 2024 (Friedenau Society, uit lidar), heel Nederland in vakjes van 25 cm, 9,2 GB (`config.BKB_TIF`) | Friedenau Society |
| `data/interim/` | `province_dem.vrt`, `province_trees.gpkg`, BAG-panden/adressen, gemeenten, wijken, buurten, landoppervlak CBS 2025 (`cbs2025_oppervlak.gpkg`); tegels tijdens een run | lokaal gebouwd (PDOK, zie de README van de 3) |
| `data/processed/` | de 3: `<naam>_viewshed.tif`, `<naam>_woningen.gpkg`, `ZuidHolland_*.gpkg/csv`; de 30: `ZuidHolland_kroonbedekking.csv`; `experiments/` (bijv. de gecorrigeerde ArcGIS-benchmark) | uitvoer van de pipeline |
| `data/fme_output/` | de FME-resultaten: `300.gdb` (300); `30_regel_v2.gdb`, de eerdere 30, ter vergelijking; `3_lijst.gdb` | kopie van `R:\…\3-30-300\fme output` |
| `data/fme_input/` | FME-invoer voor de 300: `gemeentes`, `groenvoorzieningen`, `localeversie_osm` | kopie van `R:\…\3-30-300\fme_input` (`panden`, 8,8 GB, blijft op R:) |
| `data/studiegebied/` | vergelijking op een studiegebied (één Delftse buurt) | `indicator_3_bomen/arcgis_tests/studiegebied.py` |
| `web/data/` | `3.pmtiles`, `30-300.pmtiles`, `looptijd.pmtiles`, `groen.pmtiles` (elk onder de limiet van 100 MB van GitHub) | `web/build_tiles*.py` |

De ArcGIS-benchmark `visibility_Delft` en het AHN5-DSM uit de vergelijkingen worden gelezen uit `R:\ESRI\DATA\RUIMTELIJKE ONTWIKKELING\PERSOONLIJK\Chris\test\data.gdb`.

## De webkaart

Met een 3 / 30 / 300-schakelaar zie je de drie onderdelen op dezelfde gebieden: gemeenten (zoom < 10), CBS-wijken 2025 (< 11,5), CBS-buurten 2025 (< 13), en daarna elk woonpand (de 3 en de 300; de 30 blijft op buurtniveau). Achter de knop (i) staan de bronnen en hun actualiteit. Als achtergrond kies je de grijze PDOK-kaart of een luchtfoto, en zoeken op adres gaat via de PDOK Locatieserver, binnen de provincie. Rechtsboven in het paneel wissel je tussen Nederlands en Engels.

| Bestand | Wat |
|---|---|
| `web/index.html` | de kaart (MapLibre + PMTiles); instellingen (`PROVINCE`, `REPO`, kleuren, teksten) bovenin het `<script>`; Nederlands, Engels met `?lang=en` (elke tekst als `L("Nederlands", "English")`) |
| `web/uitleg/*.html`, `web/uitleg/en/*.html` | uitlegpagina's in het Nederlands en Engels: methode, keuzes, het artikel van Konijnendijk, hoe je het herhaalt |
| `web/build_tiles.py` | `data/3.pmtiles` uit `<Provincie>_gebieden.gpkg` en `_woningen.gpkg` |
| `web/build_tiles_30_300.py` | `data/30-300.pmtiles` (gebieden, woningen) en `data/looptijd.pmtiles` (looptijdzones en ingangen, in een eigen bestand zodat aan- en uitzetten de tegels met panden niet opnieuw laat verwerken), uit de 30 per gebied (`<Provincie>_kroonbedekking.csv`), de FME-resultaten van de 300 (`config.FME_OUTPUT_DIR`, standaard `data/fme_output`) en de gebieden van de 3 |
| `web/build_tiles_groen.py` | `data/groen.pmtiles`: de parken en bossen van de 300, uit de groeninvoer van de workbench (`config.FME_INPUT_DIR`) |
| `web/serve.py` | lokale server om te bekijken (ondersteunt de Range-requests die PMTiles nodig heeft) |

De 30 komt rechtstreeks uit BKB 2024, op de buurten van de kaart; wijken en gemeenten zijn de som van hun buurten. De 300 is per gebied opnieuw geteld uit de looptijdklasse van de panden. Vanaf zoom 11 toont de 300 ook de parken en bossen zelf, geselecteerd uit de invoer van de workbench `data/fme_input/groenvoorzieningen/groenkaart.gdb` (`config.FME_INPUT_DIR`) zoals de workbench dat doet: geen TOP10NL-water, minstens 300 m², omtrek/oppervlak hoogstens 0,35.

Publiceren: kopieer `web/` zonder de `.py`-bestanden naar de branch `gh-pages`, zet er een lege `.nojekyll` bij en push. GitHub Pages serveert die branch op https://robertotjesse.github.io/3-30-300-regel/.

## Validatie

- Het QGIS-validatieproject (`qgis_validation/build_project.py` maakt `qgis_validation/330300regel_validatie.qgz`, gitignored) bevat de 3 per gemeente (zichtbaarheid en woningen), alle bomen, het hoogtemodel, 3D BAG, achtergrondkaarten, de experimenten (de gecorrigeerde en de oorspronkelijke ArcGIS-benchmark), de vergelijking op één boom en een groepje bomen, en het studiegebied. Opnieuw draaien voegt alleen nieuwe lagen toe en haalt lagen weg waarvan het bestand weg is, dus wat je in QGIS aanpast blijft staan; verwijder de `.qgz` om hem opnieuw op te bouwen.
- De ArcGIS-benchmark van de 3 had zijn waarnemers op twee keer de bedoelde hoogte. Met de bedoelde waarnemer komen ArcGIS en de GDAL-rekenkern van deze pipeline goed overeen (issue #2, opgelost). Zie [`indicator_3_bomen/README.md`](indicator_3_bomen/README.md#validation-against-arcgis).
- De 30 uit BKB 2024 is vergeleken met de eerdere FME-berekening, voor Delft en per gemeente; zie [`indicator_30_kroonbedekking/README.md`](indicator_30_kroonbedekking/README.md).

## Andere provincie

Alles wat per provincie verschilt is een instelling of een invoerbestand; je hoeft de code niet aan te passen.

1. Zet de invoer per gemeente in één map (`VIEWANALYSE_DIR`): `<naam>.tif` (AHN-DSM 0,5 m *ruw*, Float32, RD New) en `<naam>.gpkg` (boompunten, RD New). Bouw `province_dem.vrt` en `province_trees.gpkg` (README van de 3, "Setup").
2. Zet in `indicator_3_bomen/etl/config_local.py` `PROVINCE` (precies zoals in de bestuurlijke gebieden van PDOK, bijv. `"Utrecht"`), `VIEWANALYSE_DIR`, `OSGEO4W_ROOT`, `MUNICIPALITIES = []`, en `FME_OUTPUT_DIR` / `FME_INPUT_DIR` als je resultaten van de 300 hebt. De uitvoer krijgt de naam van de provincie (`Utrecht_woningen.gpkg`).
3. Download de BAG met `python indicator_3_bomen/etl/download_bag_pdok.py`. Het commando voor de gemeenten en provincies staat in `05_merge_province.py`, en dat voor de CBS-wijken en -buurten in `06_area_summaries.py`, met de omgrenzing van je provincie als `spatFilter`.
4. Draai de stappen `01` t/m `06`, daarna de 30 (`kroonbedekking_gebieden.py`; BKB 2024 dekt heel Nederland) en `web/build_tiles.py`.
5. De 300 heeft nog FME-resultaten voor die provincie nodig. Zonder die resultaten laat je de 300 weg.
6. Zet op de kaart `PROVINCE` en `REPO` bovenin het script in `web/index.html`; de kaart opent op het gebied van `data/3.pmtiles`.
7. Publiceer `web/` zoals hierboven. De uitlegpagina's (Nederlands en Engels) beschrijven de run van Zuid-Holland, dus pas de cijfers daar aan.

## Documenten

| Document | Wat |
|---|---|
| [Wiki](https://github.com/RobertoTjesse/3-30-300-regel/wiki) | overzicht voor nieuwe lezers (Engels): aan de slag, de drie onderdelen, gegevens, validatie, bekende problemen, rekentijden |
| [`WORKLOG.md`](WORKLOG.md) | logboek van het werk en de bevindingen, in volgorde |
| [`indicator_3_bomen/README.md`](indicator_3_bomen/README.md) | de 3: installatie, pipeline, validatie, bekende gegevensproblemen |
| [`indicator_3_bomen/ARCHITECTURE.md`](indicator_3_bomen/ARCHITECTURE.md) | de 3: ontwerpkeuzes in detail |
| [`indicator_3_bomen/BENCHMARKS.md`](indicator_3_bomen/BENCHMARKS.md) | de 3: rekentijden (gegenereerd) |
| [`indicator_3_bomen/IMPROVEMENTS.md`](indicator_3_bomen/IMPROVEMENTS.md) | de 3: verbeteringen ten opzichte van het oorspronkelijke prototype; gepland werk, ook de 300 in Python |
| [`indicator_30_kroonbedekking/README.md`](indicator_30_kroonbedekking/README.md) | de 30 |
| [`indicator_300_park/README.md`](indicator_300_park/README.md) | de 300 |
| `web/uitleg/` | uitlegpagina's voor het publiek (Nederlands; Engels in `web/uitleg/en/`) |

## Geschiedenis

Het project begon als `RobertoTjesse/3-regel` (de pipeline van de 3). De webkaart is daarna gebouwd in een eerdere repository met dezelfde naam, `3-30-300-regel`. Op 29 september 2026 zijn beide samengevoegd in deze repository, met hun volledige geschiedenis, en ingedeeld in één map per onderdeel; de twee oudere repositories zijn daarna verwijderd. Hun issues zijn hierheen overgezet ([issues](https://github.com/RobertoTjesse/3-30-300-regel/issues)), en de eerste gepubliceerde site met alleen de 3 staat in de branch `archief/3-regel-gh-pages`.
