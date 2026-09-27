"""
build_project.py — Create / update the QGIS validation project
(qgis_validation/3-regel_validation.qgz) with every layer the pipeline has
produced.

First run creates the project with:
  - Viewshed        one layer per data/processed/<name>_viewshed.tif,
                    classed on "number of trees visible":
                      0 red | 1-2 orange | 3-5 green | 6-7 darker green |
                      8+ dark green
  - Bomen           all trees (data/interim/province_trees.gpkg, only drawn
                    when zoomed in — 13M points) + every
                    data/processed/<name>_tree_heights.gpkg
  - 3D BAG          LoD2.2 buildings: WMS (2D map) + 3D Tiles (3D map view)
  - Achtergrond     PDOK BRT grijs (WMTS) and PDOK luchtfoto (WMS)
  - Experimenten    anything in data/processed/experiments/: rasters get the
                    viewshed styling, vectors (e.g. BAG footprints) a plain
                    outline; all switched off by default

Later runs open the existing project and only ADD layers for new output
files, so manual changes made in QGIS (styling, visibility, extra layers)
are kept. Delete the .qgz to rebuild it from scratch.

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
    QgsCoordinateReferenceSystem,
    QgsFillSymbol,
    QgsLayerTreeGroup,
    QgsMarkerSymbol,
    QgsProject,
    QgsRasterLayer,
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
EXPERIMENTS_DIR = PROCESSED_DIR / "experiments"

GROUP_VIEWSHED = "Viewshed (aantal zichtbare bomen)"
GROUP_TREES = "Bomen"
GROUP_3DBAG = "3D BAG"
GROUP_BACKGROUND = "Achtergrond"
GROUP_EXPERIMENTS = "Experimenten"

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

        PROJECT_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not project.write(str(PROJECT_PATH)):
            sys.exit(f"ERROR: could not write {PROJECT_PATH}")
        print(f"{'Created' if is_new else 'Updated'} {PROJECT_PATH} — {added} new layer(s), "
              f"{len(project.mapLayers())} in total")
    finally:
        qgs.exitQgis()


if __name__ == "__main__":
    main()
