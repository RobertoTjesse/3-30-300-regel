"""
build_project.py — Create / update the QGIS validation project
(qgis_validation/3-regel_validation.qgz) with every layer the pipeline has
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
                    when zoomed in — 13M points) + every
                    data/processed/<name>_tree_heights.gpkg
  - 3D BAG          LoD2.2 buildings: WMS (2D map) + 3D Tiles (3D map view)
  - Achtergrond     PDOK BRT grijs (WMTS) and PDOK luchtfoto (WMS)
  - Experimenten    anything in data/processed/experiments/: rasters get the
                    viewshed styling, vectors (e.g. BAG footprints) a plain
                    outline; all switched off by default

Later runs open the existing project, ADD layers for new output files and
REMOVE file-based layers whose file no longer exists (e.g. deleted old
outputs); everything else — manual changes made in QGIS (styling,
visibility, extra layers) — is kept. Delete the .qgz to rebuild it from
scratch.

Layer paths are stored relative to the project, so the folder can be moved
together with the repo. etl/run_all_municipalities.sh calls this after
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
PROJECT_PATH = REPO / "qgis_validation" / "3-regel_validation.qgz"
PROCESSED_DIR = REPO / "data" / "processed"
PROVINCE_TREES = REPO / "data" / "interim" / "province_trees.gpkg"
PROVINCE_DEM = REPO / "data" / "interim" / "province_dem.vrt"
EXPERIMENTS_DIR = PROCESSED_DIR / "experiments"
ONE_TREE_DIR = REPO / "arcgis_tests" / "one_tree"
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


def _add_one_tree(project, root, have):
    """The one-tree / tree-group viewshed comparison (arcgis_tests/one_tree.py),
    one subgroup per case folder: every result (number of the case's trees
    that see a cell, 0 transparent), the difference maps vs the exact
    line-of-sight test, the benchmark and pipeline cut-outs, the cells
    within 30 m of other trees, the trees with their 30 m circles and the
    DEM. All off by default except the trees and circles."""
    cases = sorted(p for p in ONE_TREE_DIR.glob("*") if (p / "dem.tif").exists())         if ONE_TREE_DIR.exists() else []
    old = root.findGroup(GROUP_ONE_TREE_OLD)     # the single-tree group of the first version
    if old is not None and not old.findLayers():
        old.parent().removeChildNode(old)
    if not cases:
        return 0
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
            if tif.resolve() in have or tif.stem == "dem":
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
            project.setTitle("3-regel — validatie zichtbare bomen (3-30-300)")
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

        added += _add_one_tree(project, root, have)

        PROJECT_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not project.write(str(PROJECT_PATH)):
            sys.exit(f"ERROR: could not write {PROJECT_PATH}")
        print(f"{'Created' if is_new else 'Updated'} {PROJECT_PATH} — {added} new layer(s), "
              f"{len(removed)} removed (file gone), {len(project.mapLayers())} in total")
    finally:
        qgs.exitQgis()


if __name__ == "__main__":
    main()
