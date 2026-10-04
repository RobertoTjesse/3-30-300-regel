# Comparison with Cobra Groeninzicht's 3+30+300

Cobra Groeninzicht (Vianen) sells a 3+30+300 analysis to Dutch
municipalities, computed for the whole country, with a total mark per
building. This document compares their method with ours, using the handbook
they distribute and their public map layers, and lists what each side
probably misses. Checked on 2026-10-04.

## Sources

| Document | Where |
|---|---|
| Konijnendijk, C.C. (2023). Evidence-based guidelines for greener, healthier, more resilient neighbourhoods: Introducing the 3–30–300 rule. *Journal of Forestry Research* 34, 821–830. Open access, CC BY 4.0 | [`bronnen/konijnendijk_2023_3-30-300.pdf`](bronnen/konijnendijk_2023_3-30-300.pdf); summary in `web/uitleg/konijnendijk.html` |
| Handboek 3+30+300-regel (2025), Dutch translation by Cobra of the handbook from the Nordic Yggdrasil project (Konijnendijk is a co-author), 40 pages | `bronnen/handboek_3-30-300-regel_nl_cobra_groeninzicht_2025.pdf`, local only (gitignored: Cobra hands it out after a request form). English original: [The-330300-Handbook-oct-2025.pdf](https://cobra-groeninzicht.nl/wp-content/uploads/2025/10/The-330300-Handbook-oct-2025.pdf) |
| Cobra's method, FAQ and prices | [cobra-groeninzicht.nl/producten/330300-regel/nederland](https://cobra-groeninzicht.nl/producten/330300-regel/nederland/) |
| Cobra's viewer (gemeente, wijk, buurt free; pand locked) | [ArcGIS Experience "3:30:300-regel Nederland"](https://experience.arcgis.com/experience/95ab3ed3a402429a938d04c5e3bfea3e/page/3%2B30%2B300-regel/?views=Gemeente), feature services `steenbreek_basisatlas_{gemeenten,wijken,buurten}_2023` on `services-eu1.arcgis.com/TbKWQsJPcYHYfQVg` |

## The handbook

The handbook is a policy guide; it does not say how Cobra computes its scores. It has five steps (assessment,
strategic planning, planting and management, monitoring, community
involvement), FAQs and examples (Malmö, Belfast, Frederiksberg, Groningen,
Flanders). On measuring it is short, but it is more specific than the 2023
paper on several points that matter for us:

| Topic | Paper (2023) | Handbook (2025) |
|---|---|---|
| 3: from where | home, school, workplace | home, workplace, place of learning and care facility |
| 3: which trees | "well-established" | "large" trees; e.g. crown area >= 28 m² (used in Yggdrasil), crown diameter 6–8 m, or DBH >= 30 cm |
| 3: how to measure | not defined | Yggdrasil: buffer around each home, count crowns >= 28 m²; alternatives: surveys; "new methods" with real visibility from windows; overview in Browning et al. (2024) |
| 30: neighbourhood | neighbourhood | local definition, or a buffer of 500 m or 1 km around each home (Yggdrasil) |
| 30: denominator | not defined | usually the whole area including buildings and roads; larger water bodies excluded |
| 300: size | WHO: >= 1 ha | 0.5–1.0 ha |
| 300: distance | 300 m, ~5 min walk | real walking (or cycling) distance, not as the crow flies |
| 300: quality | large public green space | public, free, safe, managed, varied, no major access barriers; each city may define it locally |
| Combined score | – | Yggdrasil "overall score" with weights, or simply 0–3 = number of rules met |
| Minimums | – | 3, 30 and 300 are minimums; cooling starts at ~40% canopy |

It also advises combining the scores with socio-economic data to rank
areas (step 1A), and monitoring the scores over time (step 4).

## Cobra's method

From the FAQ on their Dutch page and the public layers:

| | Cobra |
|---|---|
| Buildings | Google and Microsoft building footprints, OSM, "other open and closed sources" (not the BAG); every building, scored per *pand* |
| Trees | their BomenMonitor (AI on aerial imagery and lidar); a municipality's own tree database is merged in on request |
| 3 | number of "significant" trees (crown > 28 m², ~6 m diameter, after Kluck 2020) whose crown edge lies within a 25 m buffer around the building. No lines of sight: "there is no standard for which room you look out from" |
| 30 | canopy % in a 500 m buffer around each building (a "synthetic neighbourhood" of 1 km diameter), land only (water excluded) |
| 300 | walking distance over the OSM walkable network to green space from the CBS Bestand Bodemgebruik (BBG) 2017: begraafplaats, dagrecreatief terrein, droog natuurlijk terrein, park en plantsoen, sportterrein, verblijfsrecreatie, volkstuin, water met recreatieve functie. >= 1 ha, in dense urban areas >= 0.5 ha. A municipality can validate the parks ("net" score) |
| Area values | mean over the buildings in the area, not weighted by homes |

### The mark per building

Cobra gives each building a mark from 0 to 10 per rule and a total. The
formulas are not published; they follow from the eleven sample buildings in
their public legend layer (`TheNordics_legend_data`, fields `Rule_*` and
`Score_*`), which they fit exactly:

| Rule | Mark | Pass (6) at |
|---|---|---|
| 3 | 0 trees → 1, 1 → 3, 2 → 5, 6 → 9, 7 or more → 10 (3–5 not in the sample; their legend calls 3 trees "a narrow pass") | 3 trees |
| 30 | canopy % / 5, at most 10 | 30% (50% = 10) |
| 300 | 10 − 0.02 × (distance in m − 100), between 0 and 10 | 300 m (<= 100 m = 10, >= 600 m = 0) |
| Total | 0.25 × mark 3 + 0.5 × mark 30 + 0.25 × mark 300 | |

So the 30 weighs double, and a 6 means "just meets the rule", like a Dutch
school mark. Areas get the mean total, rounded, in seven classes: 0–2 zeer
slecht, 3 slecht, 4 matig, 5 neutraal, 6 goed, 7 zeer goed, 8–10
uitstekend.

### The Utrecht example

Utrecht (their free viewer, totals per gemeente and wijk): the gemeente
scores 5, *neutraal* (mean 5 significant trees, 21% canopy, 551 m to
green). Per wijk:

| Wijk | 3 (trees) | 30 (%) | 300 (m) | Total |
|---|---|---|---|---|
| Overvecht | 7 | 33 | 101 | 7 |
| Oost | 8 | 29 | 226 | 7 |
| Zuid | 8 | 29 | 132 | 7 |
| Noordoost | 7 | 25 | 373 | 6 |
| West | 5 | 18 | 1729 | 5 |
| Noordwest | 4 | 17 | 370 | 5 |
| Binnenstad | 6 | 19 | 1951 | 5 |
| Zuidwest | 5 | 17 | 1396 | 5 |
| Vleuten-De Meern | 5 | 19 | 344 | 5 |
| Leidsche Rijn | 3 | 13 | 267 | 4 |

Overvecht, the wijk with the lowest socio-economic
score in the same layer, scores best: a post-war layout with lots of green.
The Binnenstad's mean distance to green of almost 2 km cannot be right
for homes along the Singel parks. It probably comes from the mean over all
buildings, where a few far-off buildings pull the mean up, and from green that
BBG 2017 does not classify as park. West (1.7 km) includes the Lage Weide
industrial estate. A mean distance says little; a share of homes within
300 m says more.

## Side by side

| | This repository (Zuid-Holland) | Cobra |
|---|---|---|
| Unit | home (BAG address with a residential function), weighted by homes per building | building (any footprint) |
| 3: trees | all NEO tree points, incl. "verdwenen, kleine boom" (21%) | crowns > 28 m² |
| 3: seen | real line of sight (AHN DSM) from crown top to eye height, within 30 m, from just outside the facade | crown edge within 25 m of the building, no line of sight |
| 30: area | CBS buurt, land area | 500 m buffer per building, land only |
| 30: canopy | NEO crown polygons (FME; BKB 2024 lidar being tested) | BomenMonitor |
| 300: green | OSM + TOP10NL 2024, >= 300 m², not too narrow | BBG 2017 classes, >= 0.5–1 ha |
| 300: distance | 5 min walk (Valhalla) from park entrances | walking distance over OSM, metres |
| Result | share of homes meeting each rule; no combined score | mark 0–10 per rule and total per building; mean per area |
| Open | code public, trees purchased (NEO) | closed; pand level paid |

## The numbers for Zuid-Holland

Cobra's gemeente values for the 50 municipalities of Zuid-Holland, against
ours (`data/processed/ZuidHolland_samenvatting.csv` and
`ZuidHolland_kroonbedekking.csv`, CBS 2023 areas):

- **30**: the same ranking (r = 0.83), but Cobra is on average 7.6 points
  higher (17.9% vs 10.3%). Largest gaps in rural municipalities and in
  Rotterdam (23% vs 9.7%), Alphen aan den Rijn (17 vs 5.1) and Leiderdorp
  (21 vs 7.4). Our gemeente value divides by all land, polders and port
  included; Cobra's is the mean around buildings, so it measures where
  people live. For Delft the two agree better (25 vs 20.9; BKB 2024 gives
  21.0, NEO/FME 24.7).
- **3**: hardly related (r = 0.31). We have 84–99% of homes seeing 3 or
  more trees in every municipality; Cobra's mean is 3–5 significant trees
  per building in most of them, which means a large share of buildings below
  3. The size threshold makes the difference: we count every tree point.
- **300**: not comparable as numbers (share of homes within 5 minutes vs mean
  metres per building).

## Where we probably miss something

1. **Tree size (the 3).** Paper and handbook ask for large or
   well-established trees; we count every point, including the 21% "verdwenen,
   kleine boom". This is the main reason our 3 is so high (94.7% of homes).
   Fix: a crown-area threshold (28 m², as Yggdrasil and Cobra) from the NEO
   crowns (`CROWN_AREA`) or BKB, and drop "verdwenen". Report both versions
   until it is settled.
2. **Green size (the 300).** 300 m² against 0.5–1 ha in the handbook and 1 ha
   in the WHO brief. Already the first open point; the handbook's 0.5 ha is a
   defensible middle for dense areas.
3. **The 30 as a gemeente figure.** Area-weighted over all land, it mostly
   measures how rural a municipality is. Per buurt it follows the paper, but
   at gemeente level a homes-weighted figure (share of homes in a buurt with
   >= 30%, or a 500 m buffer per home as in the handbook) fits better with the
   3 and the 300, which are per home.
4. **No combined result per home.** The handbook suggests 0–3 rules met per
   building; Cobra gives a mark. We show three separate maps.
5. **Schools, workplaces and care facilities** are not computed (the
   handbook adds care facilities to the paper's list).
6. **Socio-economic overlay.** The handbook's first step ranks gaps by
   vulnerability; Cobra's layer carries a SES score. We have nothing yet (CBS
   kerncijfers / SCP statusscore per buurt would do).
7. **Upper floors.** We measure at eye height in front of the facade, so flats
   are judged from street level. Cobra ignores height altogether, so this is
   not something they do better, but it is a known gap (see
   `indicator_3_bomen/IMPROVEMENTS.md`).

## Where Cobra probably misses something

1. **No visibility in the 3.** A tree in the courtyard behind the block, or
   behind another building, counts. Their own FAQ says lines of sight "may
   give a more reliable picture". Our 3 tests those lines of sight.
2. **Buildings, not homes.** Footprints from Google/Microsoft/OSM include
   sheds, barns and industrial halls, and every building weighs the same,
   whether it has 1 home or 200. Area values are means over buildings, not
   over residents.
3. **Compensating marks.** In the total, a 10 on the 300 makes up for a 2 on
   the 30. Konijnendijk means each number as a minimum, so "meets all three"
   (which Cobra reports for the Nordic region, 66%) is the stricter and more
   faithful summary. The 25/50/25 weighting is their choice, not the paper's.
4. **Means hide inequality.** A mean mark or mean distance per area is what the
   paper warns against for the 30 ("a city average can hide inequality
   between neighbourhoods"). The Utrecht Binnenstad at 1.95 km shows how a
   mean distance gets dragged by a few buildings.
5. **Old and broad green data.** BBG 2017 is nine years old and counts
   allotments, sports grounds, cemeteries and water as green space,
   whether public or not. The FAQ mentions no entrances, so the distance is probably to the edge of the area.
6. **Closed.** Method details are only in an FAQ, the formulas are not
   published, and the building level is paid, so no one else can reproduce the results.
7. Small: the page offers "time travel" of tree growth "from now to the year
   2025", presumably 2050.

## What to take over

- A crown-size threshold for the 3 (28 m²), as an option first.
- 0.5 ha / 1 ha options for the 300 (already planned).
- Per home: the raw values, a pass/fail per rule, the number of rules met
  (0–3), and optionally a mark per rule on Cobra's scale so results can be
  compared. Per area: the share of homes that meet all three, weighted by
  homes, not a mean mark. Specified in `indicator_3_bomen/IMPROVEMENTS.md`.
- A cross-check: Cobra's buurt values are public, so a per-buurt comparison
  for Zuid-Holland can be repeated after each change.
