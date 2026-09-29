"""
build_project.py — Create / update the QGIS validation project
(qgis_validation/330300regel_validatie.qgz) with every layer the pipeline has
produced.

First run creates the project with:
  - Woningen        one layer per data/processed/<name>_woningen.gpkg
                    (04_score_buildings.py): residential buildings coloured
                    by trees visible from just outside the facade, same
                    classes as below; grey = no facade ring (enclosed)
  - Viewshed        one layer per data/processed/<name>_viewshed.tif,
                    classed on "number of trees visible":
                      0 red | 1-2 orange | 3-5 green | 6-7 darker green |
                      8+ dark green
  - Hoogtemodel     the province DEM mosaic (data/interim/province_dem.vrt,
                    the pixel source of stage 1), hillshaded, switched off
                    by default — for checking the input surface model
  - Bomen           all trees (data/interim/province_trees.gpkg, only drawn
                    when zoomed in — 5.2M points) + every
                    data/processed/<name>_tree_heights.gpkg
  - 3D BAG          LoD2.2 buildings: WMS (2D map) + 3D Tiles (3D map view)
  - Achtergrond     PDOK BRT grijs (WMTS) and PDOK luchtfoto (WMS)
  - De 3 per gemeente / wijk / buurt
                    <Province>_gebieden.gpkg (06_area_summaries.py): share of
                    homes with >= 3 visible trees, web map colours
  - De 30           the FME result (data/fme_output/30_regel_v2.gdb): canopy
                    cover per CBS wijk and buurt 2023, grey = no crown data
  - De 300          the FME result (data/fme_output/300.gdb): home buildings
                    within / beyond 5 and 15 minutes' walk of green, the
                    walking isochrones and the park entrances
  - Experimenten    anything in data/processed/experiments/: rasters get the
                    viewshed styling, vectors (e.g. BAG footprints) a plain
                    outline; all switched off by default

Later runs open the existing project, ADD layers for new output files and
REMOVE file-based layers whose file no longer exists (e.g. deleted old
outputs); everything else — manual changes made in QGIS (styling,
visibility, extra layers) — is kept. Delete the .qgz to rebuild it from
scratch.

Layer paths are stored relative to the project, so the folder can be moved
together with the repo. indicator_3_bomen/etl/run_all_municipalities.sh calls this after
every municipality.

Usage (needs QGIS's Python, not plain OSGeo4W Python):
    C:\\...\\OSGeo4W\\bin\\python-qgis-ltr.bat qgis_validation\\build_project.py
"""

import sys
from pathlib import Path

from qgis.core import (
    QgsApplication,
    QgsColorRampShader,
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem,
    QgsRendererCategory,
    QgsRuleBasedRenderer,
    QgsFillSymbol,
    QgsHillshadeRenderer,
    QgsLayerTreeGroup,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsPalettedRasterRenderer,
    QgsProject,
    QgsRasterLayer,
    QgsRasterRange,
    QgsRasterShader,
    QgsSingleBandPseudoColorRenderer,
    QgsSingleSymbolRenderer,
    QgsVectorLayer,
)
from qgis.PyQt.QtGui import QColor

REPO = Path(__file__).resolve().parent.parent
PROJECT_PATH = REPO / "qgis_validation" / "330300regel_validatie.qgz"
PROCESSED_DIR = REPO / "data" / "processed"
PROVINCE_TREES = REPO / "data" / "interim" / "province_trees.gpkg"
PROVINCE_DEM = REPO / "data" / "interim" / "province_dem.vrt"
EXPERIMENTS_DIR = PROCESSED_DIR / "experiments"
ONE_TREE_DIR = REPO / "indicator_3_bomen" / "arcgis_tests" / "one_tree"
STUDY_DIR = REPO / "data" / "studiegebied"
# The original ArcGIS benchmark (observer 2 x RASTERVALU, see ARCHITECTURE.md §14)
BENCHMARK = ('OpenFileGDB:"R:/ESRI/DATA/RUIMTELIJKE ONTWIKKELING/PERSOONLIJK/Chris/test/data.gdb"'
             ':visibility_Delft')
BENCHMARK_NAME = "visibility_Delft (originele benchmark, observer 2x RASTERVALU)"
BENCH_NODATA = -2147483647            # its undeclared NoData

GROUP_HOMES = "Woningen (aantal zichtbare bomen)"
GROUP_VIEWSHED = "Viewshed (aantal zichtbare bomen)"
GROUP_TREES = "Bomen"
GROUP_DEM = "Hoogtemodel"
GROUP_3DBAG = "3D BAG"
GROUP_BACKGROUND = "Achtergrond"
GROUP_EXPERIMENTS = "Experimenten"
GROUP_ONE_TREE = "Eén boom / boomgroep: GDAL vs ArcGIS vs benchmark (arcgis_tests/one_tree)"
GROUP_ONE_TREE_OLD = "Eén boom: GDAL vs ArcGIS vs exact (arcgis_tests/one_tree)"
GROUP_STUDY = "Studiegebied: benchmark vs ArcGIS vs GDAL (arcgis_tests/studiegebied.py)"
GROUP_AREAS_3 = "De 3 per gemeente / wijk / buurt (% woningen met >= 3 bomen)"
GROUP_30 = "De 30: kroonbedekking per wijk / buurt 2023 (FME)"
GROUP_300 = "De 300: groen op loopafstand (FME)"

# The FME results of the 30 and the 300 (config.FME_OUTPUT_DIR's default)
FME_OUTPUT_DIR = REPO / "data" / "fme_output"
FME_30 = FME_OUTPUT_DIR / "30_regel_v2.gdb"
FME_300 = FME_OUTPUT_DIR / "300.gdb"
# BAG panden that no longer / never existed, left out as in web/build_tiles_30_300.py
PAND_GONE = ("Pand gesloopt", "Niet gerealiseerd pand", "Pand buiten gebruik")

# Area colours as on the web map (web/index.html STEPS_3 / STEPS_30):
# (lower bound, colour, label)
STEPS_3 = [(0, "#d7301f", "< 80%"), (80, "#fdae6b", "80-90%"), (90, "#c7e9c0", "90-95%"),
           (95, "#74c476", "95-98%"), (98, "#238b45", ">= 98%")]
STEPS_30 = [(0, "#d7301f", "< 10%"), (10, "#fdae6b", "10-20%"), (20, "#c7e9c0", "20-30%"),
            (30, "#238b45", ">= 30% (voldoet)")]
MIN_HOMES = 10          # fewer homes: no percentage (grey), as on the web map
NO_DATA = "#d9d9d9"

# (upper bound inclusive, colour, label) — discrete classes on integer counts
VIEWSHED_CLASSES = [
    (0, "#d7191c", "0 bomen"),
    (2, "#fd8d3c", "1-2 bomen"),
    (5, "#74c476", "3-5 bomen"),
    (7, "#31a354", "6-7 bomen"),
    (float("inf"), "#006d2c", "8+ bomen"),
]

BRT_WMTS = ("contextualWMSLegend=0&crs=EPSG:28992&dpiMode=7&format=image/png"
            "&layers=grijs&styles=default&tileMatrixSet=EPSG:28992"
            "&url=https://service.pdok.nl/brt/achtergrondkaart/wmts/v2_0"
            "?request%3DGetCapabilities%26service%3DWMTS")
LUCHTFOTO_WMS = ("crs=EPSG:28992&format=image/jpeg&layers=Actueel_orthoHR&styles="
                 "&url=https://service.pdok.nl/hwh/luchtfotorgb/wms/v1_0")
BAG3D_WMS = ("crs=EPSG:28992&format=image/png&layers=lod22&styles="
             "&url=https://data.3dbag.nl/api/BAG3D/wms")
BAG3D_TILES = "url=https://data.3dbag.nl/v20250903/cesium3dtiles/lod22/tileset.json"


def _group(root, name, index=None):
    group = root.findGroup(name)
    if group is None:
        group = QgsLayerTreeGroup(name)
        root.insertChildNode(-1 if index is None else index, group)
    return group


def _prune_missing(project):
    """Remove local-file layers whose file is gone; return their names."""
    gone = [l for l in project.mapLayers().values()
            if l.providerType() in ("gdal", "ogr") and not l.source().startswith("OpenFileGDB:")
            and not Path(l.source().split("|")[0]).exists()]
    names = [l.name() for l in gone]
    project.removeMapLayers([l.id() for l in gone])
    return names


def _existing_sources(project):
    return {Path(l.source().split("|")[0]).resolve()
            for l in project.mapLayers().values() if l.providerType() in ("gdal", "ogr")}


def _style_viewshed(layer):
    ramp = QgsColorRampShader()
    ramp.setColorRampType(QgsColorRampShader.Discrete)
    ramp.setColorRampItemList([QgsColorRampShader.ColorRampItem(v, QColor(c), lbl)
                               for v, c, lbl in VIEWSHED_CLASSES])
    shader = QgsRasterShader()
    shader.setRasterShaderFunction(ramp)
    renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
    renderer.setClassificationMin(0)
    renderer.setClassificationMax(8)
    layer.setRenderer(renderer)
    layer.setOpacity(0.8)


def _style_homes(layer):
    labels = ["0", "1-2", "3-5", "6-7", "8+"]
    cats = [QgsRendererCategory(lbl, QgsFillSymbol.createSimple(
                {"color": colour, "outline_color": "#404040", "outline_width": "0.1"}),
                f"{lbl} bomen")
            for lbl, (_, colour, _) in zip(labels, VIEWSHED_CLASSES)]
    cats.append(QgsRendererCategory(None, QgsFillSymbol.createSimple(
        {"color": "#bdbdbd", "outline_color": "#404040", "outline_width": "0.1"}), "geen gevelring"))
    layer.setRenderer(QgsCategorizedSymbolRenderer("klasse", cats))


def _style_paletted(layer, classes):
    """classes: [(value, colour or None, label)]; None = transparent."""
    items = [QgsPalettedRasterRenderer.Class(v, QColor(c) if c else QColor(0, 0, 0, 0), lbl)
             for v, c, lbl in classes]
    layer.setRenderer(QgsPalettedRasterRenderer(layer.dataProvider(), 1, items))


COUNT_GREENS = ["#c7e9c0", "#a1d99b", "#74c476", "#41ab5d", "#238b45", "#006d2c", "#00441b"]


def _style_counts(layer, top=40):
    """Number of trees that see a cell: 0 transparent, 1..7+ light to dark green."""
    _style_paletted(layer, [(0, None, "0")] + [
        (v, COUNT_GREENS[min(v, len(COUNT_GREENS)) - 1], str(v) if v < len(COUNT_GREENS) else f"{v}")
        for v in range(1, top + 1)])


# One-tree results shown in QGIS (and their diff_ maps): the exact test, GDAL,
# the pipeline's own logic on AHN5, classic ArcGIS Visibility, Viewshed2, the
# benchmark as it was run (arc_bench_both), the corrected settings
# (arc_bench_fixed) and the benchmark cut-out
ONE_TREE_KEEP = {"exact_bilinear", "gdal", "pipeline_ahn5", "arc_vis_2d", "arc_v2_2d",
                 "arc_bench_both", "arc_bench_fixed", "bench_visibility_Delft"}


def _one_tree_kept(path):
    stem = path.stem.removeprefix("diff_")
    return path.suffix != ".tif" or path.stem in ("dem", "others") or stem in ONE_TREE_KEEP


def _add_one_tree(project, root, have):
    """The one-tree / tree-group viewshed comparison (arcgis_tests/one_tree.py),
    one subgroup per case folder: every result (number of the case's trees
    that see a cell, 0 transparent), the difference maps vs the exact
    line-of-sight test, the benchmark and pipeline cut-outs, the cells
    within 30 m of other trees, the trees with their 30 m circles and the
    DEM. All off by default except the trees and circles."""
    cases = sorted(p for p in ONE_TREE_DIR.glob("*") if (p / "dem.tif").exists()) \
        if ONE_TREE_DIR.exists() else []
    old = root.findGroup(GROUP_ONE_TREE_OLD)     # the single-tree group of the first version
    if old is not None and not old.findLayers():
        old.parent().removeChildNode(old)
    if not cases:
        return 0
    # Only the results that tell the story; the rest (duplicates such as the
    # "3D" variants, dead ends, the production-DEM cut-out) is left out and
    # removed if an earlier run added it
    one_tree = str(ONE_TREE_DIR.resolve()).lower()
    drop = [l for l in project.mapLayers().values()
            if l.providerType() in ("gdal", "ogr")
            and str(Path(l.source().split("|")[0]).resolve()).lower().startswith(one_tree)
            and not _one_tree_kept(Path(l.source().split("|")[0]))]
    project.removeMapLayers([l.id() for l in drop])
    top = _group(root, GROUP_ONE_TREE, 0)
    added = 0
    for case_dir in cases:
        group = _group(top, case_dir.name)
        gpkg = case_dir / "tree_ring.gpkg"
        if gpkg.exists() and gpkg.resolve() not in have:
            for name, label in (("tree", "bomen"), ("ring30", "30 m rond de bomen")):
                layer = QgsVectorLayer(f"{gpkg}|layername={name}", label, "ogr")
                if _add(project, group, layer):
                    if name == "tree":
                        layer.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple(
                            {"name": "circle", "color": "#e31a1c", "size": "3"})))
                    else:
                        layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple(
                            {"line_color": "#e31a1c", "line_width": "0.6"})))
                    added += 1
        diffs = _group(group, "Verschil met exact (oranje = telt meer bomen, blauw = minder)")
        results = _group(group, "Resultaten (aantal bomen dat de cel ziet)")
        for tif in sorted(case_dir.glob("*.tif")):
            if tif.resolve() in have or tif.stem == "dem" or not _one_tree_kept(tif):
                continue
            if tif.stem.startswith("diff_"):
                tool = tif.stem.removeprefix("diff_")
                layer = QgsRasterLayer(str(tif), f"verschil {tool} vs exact", "gdal")
                if _add(project, diffs, layer, visible=False):
                    _style_paletted(layer, [(0, None, "zelfde"), (1, "#ff7f00", f"{tool} telt meer"),
                                            (2, "#1f78b4", f"{tool} telt minder")])
                    added += 1
            elif tif.stem == "others":
                layer = QgsRasterLayer(str(tif), "binnen 30 m van andere bomen (niet vergeleken)", "gdal")
                if _add(project, group, layer, visible=False):
                    _style_paletted(layer, [(0, None, "alleen deze bomen"), (1, "#969696", "ook andere bomen")])
                    layer.setOpacity(0.6)
                    added += 1
            elif tif.stem == "dem_raw":
                layer = QgsRasterLayer(str(tif), "DEM ongevuld (wat de benchmark zag)", "gdal")
                if _add(project, group, layer, visible=False):
                    layer.setRenderer(QgsHillshadeRenderer(layer.dataProvider(), 1, 315, 45))
                    added += 1
            else:
                layer = QgsRasterLayer(str(tif), tif.stem, "gdal")
                if _add(project, results, layer, visible=False):
                    _style_counts(layer)
                    layer.setOpacity(0.75)
                    added += 1
        dem = case_dir / "dem.tif"
        if dem.resolve() not in have:
            layer = QgsRasterLayer(str(dem), "DEM (AHN5 DSM, gevuld)", "gdal")
            if _add(project, group, layer, visible=False):
                layer.setRenderer(QgsHillshadeRenderer(layer.dataProvider(), 1, 315, 45))
                added += 1
    return added


def _add_study_areas(project, root, have):
    """The study areas (arcgis_tests/studiegebied.py), one subgroup per buurt:
    the buurt and its 30 m buffer, the trees, the benchmark cut-out and the
    ArcGIS and GDAL results (trees seeing a cell, same colours as the
    viewshed layers), the difference maps vs the benchmark and the DSM."""
    areas = sorted(p for p in STUDY_DIR.glob("BU*") if (p / "studiegebied.gpkg").exists()) \
        if STUDY_DIR.exists() else []
    if not areas:
        return 0
    top = _group(root, GROUP_STUDY, 0)
    added = 0
    labels = {"benchmark": "benchmark (visibility_Delft, uitgesneden)",
              "arcgis": "ArcGIS Visibility, instellingen benchmark",
              "gdal": "GDAL, instellingen benchmark"}
    for area in areas:
        group = _group(top, area.name)
        gpkg = area / "studiegebied.gpkg"
        if gpkg.resolve() not in have:
            styles = {
                "buurt": QgsFillSymbol.createSimple({"color": "0,0,0,0", "outline_color": "#e31a1c",
                                                     "outline_width": "0.8"}),
                "buffer30": QgsFillSymbol.createSimple({"color": "0,0,0,0", "outline_color": "#e31a1c",
                                                        "outline_width": "0.4", "outline_style": "dash"}),
                "bomen": QgsMarkerSymbol.createSimple({"name": "circle", "color": "#1a9850",
                                                       "outline_color": "#ffffff", "size": "1.8"}),
            }
            for name, label in (("buurt", "buurt (vergeleken cellen)"), ("buffer30", "buffer 30 m"),
                                ("bomen", "bomen (benchmark)")):
                layer = QgsVectorLayer(f"{gpkg}|layername={name}", label, "ogr")
                if _add(project, group, layer):
                    layer.setRenderer(QgsSingleSymbolRenderer(styles[name]))
                    added += 1
        diffs = _group(group, "Verschil met benchmark (oranje = telt meer bomen, blauw = minder)")
        results = _group(group, "Resultaten (aantal bomen dat de cel ziet)")
        for stem in ("benchmark", "arcgis", "gdal"):
            tif = area / f"{stem}.tif"
            if tif.exists() and tif.resolve() not in have:
                layer = QgsRasterLayer(str(tif), labels[stem], "gdal")
                if _add(project, results, layer, visible=stem == "benchmark"):
                    _style_viewshed(layer)
                    added += 1
        for tif in sorted(area.glob("verschil_*.tif")):
            if tif.resolve() in have:
                continue
            tool = tif.stem.removeprefix("verschil_")
            layer = QgsRasterLayer(str(tif), f"verschil {tool} vs benchmark", "gdal")
            if _add(project, diffs, layer, visible=False):
                _style_paletted(layer, [(0, None, "zelfde"), (1, "#ff7f00", f"{tool} telt meer"),
                                        (2, "#1f78b4", f"{tool} telt minder")])
                added += 1
        dem = area / "dem_raw.tif"
        if dem.exists() and dem.resolve() not in have:
            layer = QgsRasterLayer(str(dem), "DSM (AHN5 ruw, zoals de benchmark)", "gdal")
            if _add(project, group, layer, visible=False):
                layer.setRenderer(QgsHillshadeRenderer(layer.dataProvider(), 1, 315, 45))
                added += 1
    return added


def _style_rules(layer, rules, outline="#404040"):
    """rules: [(expression, colour, label)] — the first matching rule wins."""
    root_rule = QgsRuleBasedRenderer.Rule(None)
    for expr, colour, label in rules:
        symbol = QgsFillSymbol.createSimple({"color": colour, "outline_color": outline,
                                             "outline_width": "0.1"})
        root_rule.appendChild(QgsRuleBasedRenderer.Rule(symbol, filterExp=expr, label=label))
    layer.setRenderer(QgsRuleBasedRenderer(root_rule))


def _step_rules(field, steps, no_data_expr, no_data_label):
    """Rules for steps [(lower bound, colour, label)] on a percentage field."""
    rules = [(no_data_expr, NO_DATA, no_data_label)]
    for i, (lo, colour, label) in enumerate(steps):
        hi = steps[i + 1][0] if i + 1 < len(steps) else None
        expr = f'"{field}" >= {lo}' + (f' AND "{field}" < {hi}' if hi is not None else "")
        rules.append((f"NOT ({no_data_expr}) AND {expr}", colour, label))
    return rules


def _add_areas_3(project, root, have):
    """The 3 summarised per gemeente, wijk and buurt (06_area_summaries.py):
    share of homes with >= 3 visible trees, in the web map's colours."""
    added = 0
    for gpkg in sorted(PROCESSED_DIR.glob("*_gebieden.gpkg")):
        if gpkg.resolve() in have:
            continue
        group = _group(root, GROUP_AREAS_3, 0)
        rules = _step_rules("pct_woningen_3_of_meer", STEPS_3,
                            f'"woningen" < {MIN_HOMES} OR "pct_woningen_3_of_meer" IS NULL',
                            f"< {MIN_HOMES} woningen")
        for level in ("gemeenten", "wijken", "buurten"):
            layer = QgsVectorLayer(f"{gpkg}|layername={level}", level, "ogr")
            if _add(project, group, layer, visible=False):
                _style_rules(layer, rules)
                added += 1
    return added


def _add_30(project, root, have):
    """The 30 as FME computed it (indicator_30_kroonbedekking): canopy cover
    per CBS wijk and buurt 2023 (Percentage_groen = crown m2 / land area).
    Grey = no crown data (the FME bug: codes new in 2023 not found)."""
    if not FME_30.exists() or FME_30.resolve() in have:
        return 0
    group = _group(root, GROUP_30, 0)
    rules = _step_rules("Percentage_groen", STEPS_30,
                        "\"totaal_kroonoppervlak_m2\" IS NULL OR \"totaal_kroonoppervlak_m2\" = ''",
                        "geen kroondata")
    added = 0
    for name, label in (("FeatureClass1", "wijken 2023"), ("FeatureClass_buurt", "buurten 2023")):
        layer = QgsVectorLayer(f"{FME_30}|layername={name}", label, "ogr")
        if _add(project, group, layer, visible=False):
            _style_rules(layer, rules)
            added += 1
    return added


def _add_300(project, root, have):
    """The 300 as FME computed it (indicator_300_park): BAG panden with a
    woonfunctie inside / outside the 5- and 15-minute walking isochrones from
    the entrances of parks and woods, the isochrones and the entrances."""
    if not FME_300.exists() or FME_300.resolve() in have:
        return 0
    group = _group(root, GROUP_300, 0)
    added = 0

    entrances = QgsVectorLayer(f"{FME_300}|layername=ingang_parken", "ingangen parken en bossen", "ogr")
    if _add(project, group, entrances, visible=False):
        entrances.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple(
            {"name": "circle", "color": "#006d2c", "outline_color": "#ffffff", "size": "2"})))
        entrances.setScaleBasedVisibility(True)
        entrances.setMinimumScale(50000)
        entrances.setMaximumScale(0)
        added += 1

    for name, label, colour, style in (("isochrones_dissolved", "5 min lopen", "#238b45", "solid"),
                                       ("isochrones_dissolved_15", "15 min lopen", "#238b45", "dash")):
        layer = QgsVectorLayer(f"{FME_300}|layername={name}", f"looptijdzone {label}", "ogr")
        if _add(project, group, layer, visible=False):
            layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple(
                {"color": "0,0,0,0", "outline_color": colour, "outline_width": "0.5",
                 "outline_style": style})))
            added += 1

    gone = ", ".join(f"'{s}'" for s in PAND_GONE)
    homes_filter = f'"gebr_woonfunctie" > 0 AND ("status" IS NULL OR "status" NOT IN ({gone}))'
    for name, label in (("_300regel", "5 min"), ("_300regel_15", "15 min")):
        layer = QgsVectorLayer(f"{FME_300}|layername={name}", f"woonpanden binnen {label} lopen", "ogr")
        if _add(project, group, layer, visible=False):
            layer.setSubsetString(homes_filter)
            _style_rules(layer, [('"_related_suppliers" >= 1', "#238b45", f"binnen {label} lopen"),
                                 ("ELSE", "#d7301f", f"verder dan {label} lopen")])
            layer.setScaleBasedVisibility(True)
            layer.setMinimumScale(25000)    # 1.9 M panden: only draw when zoomed in
            layer.setMaximumScale(0)
            added += 1
    return added


def _add(project, group, layer, visible=True):
    if not layer.isValid():
        print(f"  WARNING: could not load '{layer.name()}' — skipped")
        return None
    project.addMapLayer(layer, False)
    node = group.addLayer(layer)
    node.setItemVisibilityChecked(visible)
    node.setExpanded(False)
    return layer


def _create_static_layers(project, root):
    """Background, 3D BAG and the all-trees layer — only on first creation."""
    bg = _group(root, GROUP_BACKGROUND)
    _add(project, bg, QgsRasterLayer(BRT_WMTS, "PDOK BRT achtergrondkaart (grijs)", "wms"))
    _add(project, bg, QgsRasterLayer(LUCHTFOTO_WMS, "PDOK luchtfoto (actueel)", "wms"), visible=False)

    bag = _group(root, GROUP_3DBAG, 0)
    _add(project, bag, QgsRasterLayer(BAG3D_WMS, "3D BAG LoD2.2 (kaart)", "wms"), visible=False)
    try:
        from qgis.core import QgsTiledSceneLayer
        _add(project, bag, QgsTiledSceneLayer(BAG3D_TILES, "3D BAG LoD2.2 (3D-weergave)", "cesiumtiles"),
             visible=False)
    except ImportError:
        print("  NOTE: this QGIS has no 3D Tiles support — 3D BAG added as WMS only")

    trees = _group(root, GROUP_TREES, 0)
    if PROVINCE_TREES.exists():
        layer = QgsVectorLayer(f"{PROVINCE_TREES}|layername=province_trees", "Alle bomen (Zuid-Holland)", "ogr")
        if _add(project, trees, layer):
            layer.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple(
                {"name": "circle", "color": "#1a9850", "outline_color": "#ffffff",
                 "outline_width": "0.1", "size": "1.6"})))
            layer.setScaleBasedVisibility(True)
            layer.setMinimumScale(10000)   # only draw when zoomed in past 1:10,000
            layer.setMaximumScale(0)


def main():
    qgs = QgsApplication([], False)
    qgs.initQgis()
    try:
        project = QgsProject.instance()
        is_new = not PROJECT_PATH.exists()
        if is_new:
            project.setCrs(QgsCoordinateReferenceSystem("EPSG:28992"))
            project.setTitle("330300regel — validatie 3-30-300")
        elif not project.read(str(PROJECT_PATH)):
            sys.exit(f"ERROR: could not read {PROJECT_PATH}")
        project.writeEntryBool("Paths", "/Absolute", False)   # store relative paths

        root = project.layerTreeRoot()
        if is_new:
            _create_static_layers(project, root)
        viewshed_group = _group(root, GROUP_VIEWSHED, 0)
        trees_group = _group(root, GROUP_TREES, 1)
        removed = [] if is_new else _prune_missing(project)
        have = _existing_sources(project)

        added = 0
        for tif in sorted(PROCESSED_DIR.glob("*_viewshed.tif")):
            if tif.resolve() in have:
                continue
            layer = QgsRasterLayer(str(tif), tif.stem.removesuffix("_viewshed"), "gdal")
            if _add(project, viewshed_group, layer):
                _style_viewshed(layer)
                added += 1

        for gpkg in sorted(PROCESSED_DIR.glob("*_tree_heights.gpkg")):
            if gpkg.resolve() in have:
                continue
            name = gpkg.stem.removesuffix("_tree_heights")
            layer = QgsVectorLayer(str(gpkg), f"Boomhoogtes {name}", "ogr")
            if _add(project, trees_group, layer, visible=False):
                added += 1

        if PROVINCE_DEM.exists() and PROVINCE_DEM.resolve() not in have:
            layer = QgsRasterLayer(str(PROVINCE_DEM), "DEM Zuid-Holland (province_dem.vrt)", "gdal")
            if _add(project, _group(root, GROUP_DEM, 2), layer, visible=False):
                # Hillshade makes DEM faults visible at a glance: integer
                # terraces, NoData holes, seams between municipality exports
                layer.setRenderer(QgsHillshadeRenderer(layer.dataProvider(), 1, 315, 45))
                added += 1

        homes = sorted(PROCESSED_DIR.glob("*_woningen.gpkg"))
        if homes:
            homes_group = _group(root, GROUP_HOMES, 0)
        for gpkg in homes:
            if gpkg.resolve() in have:
                continue
            layer = QgsVectorLayer(str(gpkg), gpkg.stem.removesuffix("_woningen"), "ogr")
            if _add(project, homes_group, layer):
                _style_homes(layer)
                added += 1

        experiments = sorted(EXPERIMENTS_DIR.glob("*")) if EXPERIMENTS_DIR.exists() else []
        if experiments:
            exp_group = _group(root, GROUP_EXPERIMENTS, 1)
        for f in experiments:
            if f.resolve() in have:
                continue
            if f.suffix.lower() == ".tif":
                layer = QgsRasterLayer(str(f), f.stem, "gdal")
                if _add(project, exp_group, layer, visible=False):
                    _style_viewshed(layer)
                    added += 1
            elif f.suffix.lower() in (".fgb", ".gpkg", ".shp"):
                layer = QgsVectorLayer(str(f), f.stem, "ogr")
                if _add(project, exp_group, layer, visible=False):
                    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple(
                        {"color": "0,0,0,0", "outline_color": "#3f007d", "outline_width": "0.4"})))
                    added += 1

        # The original ArcGIS benchmark, read straight from its geodatabase,
        # next to the corrected one (arcgis_tests/benchmark_corrected.py)
        if not any(l.name() == BENCHMARK_NAME for l in project.mapLayers().values()):
            layer = QgsRasterLayer(BENCHMARK, BENCHMARK_NAME, "gdal")
            if layer.isValid():
                layer.dataProvider().setUserNoDataValue(1, [QgsRasterRange(BENCH_NODATA, BENCH_NODATA)])
                if _add(project, _group(root, GROUP_EXPERIMENTS, 1), layer, visible=False):
                    _style_viewshed(layer)
                    added += 1
            else:
                print(f"  NOTE: {BENCHMARK} not readable (R: drive?) — benchmark layer skipped")

        added += _add_areas_3(project, root, have)
        added += _add_30(project, root, have)
        added += _add_300(project, root, have)
        added += _add_one_tree(project, root, have)
        added += _add_study_areas(project, root, have)

        PROJECT_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not project.write(str(PROJECT_PATH)):
            sys.exit(f"ERROR: could not write {PROJECT_PATH}")
        print(f"{'Created' if is_new else 'Updated'} {PROJECT_PATH} — {added} new layer(s), "
              f"{len(removed)} removed (file gone), {len(project.mapLayers())} in total")
    finally:
        qgs.exitQgis()


if __name__ == "__main__":
    main()
