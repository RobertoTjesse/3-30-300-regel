"""
build_project.py — Build the QGIS validation project
(qgis_validation/330300regel_validatie.qgz): only what is needed to check the
3 and the 300, in a few numbered groups.

  1. De 3 – AHN5        the current run (data/processed, files written since
                        AHN5_SINCE): buurten/wijken/gemeenten (% homes with
                        >= 3 visible trees), buildings (trees visible from
                        the facade; per municipality until 05 has merged
                        them) and all viewsheds as one mosaic
                        (qgis_validation/viewshed_ahn5.vrt)
  2. De 3 – AHN4        the same from the AHN4 run, kept on R: (AHN4_DIR)
  3. Benchmark         one Delft buurt: the colleague's ArcGIS Pro run, our GDAL
                        replica of it, ArcGIS Pro with the correct observer
                        and our own GDAL method, each against the benchmark
  4. De 300             the walking distance per home as on the map (GRASS,
                        Valhalla as second look), the green and its entrances
  5. Invoer             all trees, the tree heights, the AHN5 and AHN4
                        surface models (hillshade)
  6. Achtergrond        PDOK BRT grijs, PDOK luchtfoto

The project is rebuilt from scratch on every run (the previous one is kept as
330300regel_validatie.backup.qgz), so changes made in QGIS are not kept.
Layer paths are stored relative to the project.

Usage (needs QGIS's Python, not plain OSGeo4W Python):
    C:\\...\\OSGeo4W\\bin\\python-qgis-ltr.bat qgis_validation\\build_project.py
"""

import shutil
import sys
from datetime import datetime
from pathlib import Path

from qgis.core import (
    QgsApplication,
    QgsCategorizedSymbolRenderer,
    QgsColorRampShader,
    QgsCoordinateReferenceSystem,
    QgsFillSymbol,
    QgsHillshadeRenderer,
    QgsLayerTreeGroup,
    QgsMarkerSymbol,
    QgsPalettedRasterRenderer,
    QgsProject,
    QgsRasterLayer,
    QgsRasterShader,
    QgsRendererCategory,
    QgsRuleBasedRenderer,
    QgsSingleBandPseudoColorRenderer,
    QgsSingleSymbolRenderer,
    QgsVectorLayer,
)
from qgis.PyQt.QtGui import QColor
import numpy as np
from osgeo import gdal

gdal.UseExceptions()

REPO = Path(__file__).resolve().parent.parent
PROJECT_PATH = REPO / "qgis_validation" / "330300regel_validatie.qgz"
BACKUP_PATH = PROJECT_PATH.with_suffix(".backup.qgz")
PROCESSED_DIR = REPO / "data" / "processed"
INTERIM_DIR = REPO / "data" / "interim"
PROVINCE = "ZuidHolland"

# The AHN5 run started on 20 October 2026: in data/processed, only files
# written since then are AHN5 (the rest is still AHN4 until the run reaches it)
AHN5_SINCE = datetime(2026, 10, 20, 17, 0).timestamp()
# The AHN4 results, copied to R: before the AHN5 run overwrote them
AHN4_DIR = Path(r"R:\ESRI\BEHEER\Projecten\Tijdelijk_Roberto\3-30-300\ahn4_resultaten")

TREES = INTERIM_DIR / "province_trees.gpkg"
DEM_AHN5 = INTERIM_DIR / "province_dem.vrt"
DEM_AHN4 = INTERIM_DIR / "province_dem_ahn4.vrt"
TREE_HEIGHTS = {"AHN5": PROCESSED_DIR / "Delft_tree_heights.gpkg",
                "AHN4": PROCESSED_DIR / "ahn4" / "Delft_tree_heights.gpkg"}
# The benchmark analysis on one Delft buurt (indicator_3_bomen/arcgis_tests:
# studiegebied.py, studiegebied_pipeline.py)
STUDY = REPO / "data" / "studiegebied" / "BU05031407"
STUDY_NAME = "Molenbuurt"
# (file stem, group title, explanation) in the order shown
BENCHMARK_RUNS = [
    ("benchmark", "ArcGIS Pro Visibility: de benchmark (waarnemer te hoog)",
     "De oorspronkelijke analyse van een collega (visibility_Delft): ArcGIS Pro Visibility, "
     "FREQUENCY, straal 30 m, doel 1,8 m boven het maaiveld, AHN5 ruw. RASTERVALU (de AHN-hoogte "
     "op het boompunt) is zowel als observer_elevation als als observer_offset opgegeven, dus "
     "elke boom kijkt vanaf 2 x RASTERVALU m NAP: veel te hoog, en daardoor ziet hij te veel."),
    ("gdal", "GDAL-replica van de benchmark (zelfde instellingen)",
     "Onze nabouw van de benchmark met GDAL ViewshedGenerate: dezelfde bomen, hetzelfde "
     "hoogtemodel, waarnemer op 2 x RASTERVALU, doel 1,8 m, straal 30 m. Laat zien dat GDAL "
     "dezelfde uitkomst geeft als ArcGIS Pro wanneer de instellingen gelijk zijn."),
    ("arcgis_correct", "ArcGIS Pro Visibility zonder de verdubbeling (RASTERVALU + 1 m)",
     "De benchmark opnieuw gedraaid in ArcGIS Pro (benchmark_corrected.py) met maar één "
     "wijziging: observer_offset 1 m in plaats van RASTERVALU. Die 1 m is de standaardwaarde van "
     "de tool, geen onderbouwde keuze: RASTERVALU is al de hoogte van het oppervlak op het "
     "boompunt (meestal de kruin), dus de boom kijkt vanaf 1 m boven zijn eigen kruin."),
    ("pipeline_ahn5", "GDAL-alternatief: onze methode op AHN5 (waarnemer op de kruintop)",
     "Onze eigen methode (indicator_3_bomen/etl): GDAL ViewshedGenerate per boom, waarnemer op "
     "de kruintop (hoogste punt binnen 1,5 m van de boom, gebouwen niet meegeteld), doel 1,8 m, "
     "straal 30 m, AHN5 DSM (hoogste punt per cel) met de gaten gevuld. Uitgesneden uit de "
     "Delft-run van 20 oktober 2026."),
    ("pipeline_ahn4", "GDAL-alternatief: onze methode op AHN4",
     "Dezelfde methode op AHN4 (gewogen gemiddelde per cel), de versie die op de kaart stond "
     "vóór de AHN5-run."),
]

OPEN_300 = INTERIM_DIR / "300" / PROVINCE / "prepared.gpkg"      # green areas, entrances
FINAL_300 = INTERIM_DIR / "300" / PROVINCE / "300.gpkg"          # what the map shows

# Area colours as on the web map (web/index.html STEPS_3): (lower bound, colour, label)
STEPS_3 = [(0, "#d7301f", "< 80%"), (80, "#fdae6b", "80-90%"), (90, "#c7e9c0", "90-95%"),
           (95, "#74c476", "95-98%"), (98, "#238b45", ">= 98%")]
MIN_HOMES = 10          # fewer homes: no percentage (grey), as on the web map
NO_DATA = "#d9d9d9"

# (upper bound inclusive, colour, label) — discrete classes on integer counts
TREE_CLASSES = [
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


def _group(parent, name, expanded=True):
    group = QgsLayerTreeGroup(name)
    group.setExpanded(expanded)
    parent.addChildNode(group)
    return group


def _add(project, group, layer, visible=True):
    """Add a layer to a group (collapsed, on or off); returns the layer, or
    None with a warning when the source can't be loaded."""
    if not layer.isValid():
        print(f"  WARNING: could not load '{layer.name()}' — skipped")
        return None
    project.addMapLayer(layer, False)
    node = group.addLayer(layer)
    node.setItemVisibilityChecked(visible)
    node.setExpanded(False)
    return layer


def _zoomed_in(layer, scale=25000):
    """Only draw when zoomed in past 1:scale (point layers with millions of features)."""
    layer.setScaleBasedVisibility(True)
    layer.setMinimumScale(scale)
    layer.setMaximumScale(0)


def _style_viewshed(layer):
    """Tree-count raster in the TREE_CLASSES colours (0 red ... 8+ dark green)."""
    ramp = QgsColorRampShader()
    ramp.setColorRampType(QgsColorRampShader.Discrete)
    ramp.setColorRampItemList([QgsColorRampShader.ColorRampItem(v, QColor(c), lbl)
                               for v, c, lbl in TREE_CLASSES])
    shader = QgsRasterShader()
    shader.setRasterShaderFunction(ramp)
    renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
    renderer.setClassificationMin(0)
    renderer.setClassificationMax(8)
    layer.setRenderer(renderer)
    layer.setOpacity(0.8)


def _style_buildings(layer):
    """Buildings coloured by their "klasse" field, grey when it is empty."""
    labels = ["0", "1-2", "3-5", "6-7", "8+"]
    cats = [QgsRendererCategory(lbl, QgsFillSymbol.createSimple(
                {"color": colour, "outline_color": "#404040", "outline_width": "0.1"}), f"{lbl} bomen")
            for lbl, (_, colour, _) in zip(labels, TREE_CLASSES)]
    cats.append(QgsRendererCategory(None, QgsFillSymbol.createSimple(
        {"color": "#bdbdbd", "outline_color": "#404040", "outline_width": "0.1"}), "geen gevelring"))
    layer.setRenderer(QgsCategorizedSymbolRenderer("klasse", cats))


def _style_areas(layer):
    """% homes with >= 3 visible trees in the web map's steps; grey below MIN_HOMES."""
    field = "pct_woningen_3_of_meer"
    no_data = f'"woningen" < {MIN_HOMES} OR "{field}" IS NULL'
    rules = [(no_data, NO_DATA, f"< {MIN_HOMES} woningen")]
    for i, (lo, colour, label) in enumerate(STEPS_3):
        hi = STEPS_3[i + 1][0] if i + 1 < len(STEPS_3) else None
        expr = f'"{field}" >= {lo}' + (f' AND "{field}" < {hi}' if hi is not None else "")
        rules.append((f"NOT ({no_data}) AND {expr}", colour, label))
    root_rule = QgsRuleBasedRenderer.Rule(None)
    for expr, colour, label in rules:
        symbol = QgsFillSymbol.createSimple({"color": colour, "outline_color": "#404040",
                                             "outline_width": "0.1"})
        root_rule.appendChild(QgsRuleBasedRenderer.Rule(symbol, filterExp=expr, label=label))
    layer.setRenderer(QgsRuleBasedRenderer(root_rule))


def _municipality(path, suffix):
    return path.name.removesuffix(suffix).replace("ZuidHolland", "Zuid-Holland")


def _add_the_3(project, parent, folder, tag, keep, on):
    """One version of the 3 (tag "AHN5" or "AHN4") from folder: areas,
    buildings and viewsheds, each file only if keep(path)."""
    top = _group(parent, f"De 3 – {tag}", expanded=on)
    areas = folder / f"{PROVINCE}_gebieden.gpkg"
    if areas.exists() and keep(areas):
        group = _group(top, "% woningen met ≥ 3 zichtbare bomen")
        for level, label in (("buurten", "Buurten"), ("wijken", "Wijken"), ("gemeenten", "Gemeenten")):
            layer = QgsVectorLayer(f"{areas}|layername={level}", f"{label} ({tag})", "ogr")
            if _add(project, group, layer, visible=on and level == "buurten"):
                _style_areas(layer)

    province = folder / f"{PROVINCE}_woningen.gpkg"
    if province.exists() and keep(province):
        buildings = [(province, "Zuid-Holland")]
    else:   # not merged yet: one layer per municipality
        buildings = [(p, _municipality(p, "_woningen.gpkg")) for p in sorted(folder.glob("*_woningen.gpkg"))
                     if p != province and keep(p)]
    if buildings:
        group = _group(top, "Panden: zichtbare bomen vanaf de gevel")
        for gpkg, name in buildings:
            layer = QgsVectorLayer(str(gpkg), f"Panden {name} ({tag})", "ogr")
            if _add(project, group, layer, visible=on):
                _style_buildings(layer)

    # All municipalities' viewsheds as one mosaic (a VRT next to the project)
    viewsheds = [str(p) for p in sorted(folder.glob("*_viewshed.tif")) if keep(p)]
    if viewsheds:
        vrt = PROJECT_PATH.parent / f"viewshed_{tag.lower()}.vrt"
        gdal.BuildVRT(str(vrt), viewsheds).FlushCache()
        layer = QgsRasterLayer(str(vrt), f"Zichtbare bomen per cel van 0,5 m ({tag}, {len(viewsheds)} gemeente{"n" if len(viewsheds) > 1 else ""})",
                               "gdal")
        if _add(project, top, layer, visible=False):
            _style_viewshed(layer)


def _explain(layer, text):
    """Put an explanation in the layer's metadata (Layer Properties → Metadata)."""
    meta = layer.metadata()
    meta.setAbstract(text)
    layer.setMetadata(meta)


def _read(path):
    """A raster's first band as (array, NoData value)."""
    ds = gdal.Open(str(path))
    band = ds.GetRasterBand(1)
    return band.ReadAsArray(), band.GetNoDataValue()


def _cells(path, area):
    """Trees per compared cell of a study-area raster (NoData = 0)."""
    v, nodata = _read(path)
    v = v.astype(np.int64)
    if nodata is not None:
        v[v == nodata] = 0
    return v[area]


def _add_benchmark(project, parent):
    """The benchmark analysis on one Delft buurt (arcgis_tests/studiegebied.py,
    studiegebied_pipeline.py): the colleague's ArcGIS Pro run, our GDAL replica
    of it, ArcGIS Pro with the correct observer and our own GDAL method, each
    with its difference from the benchmark."""
    if not STUDY.exists():
        return
    top = _group(parent, f"Benchmark – ArcGIS Pro tegen GDAL ({STUDY_NAME}, Delft)", expanded=False)
    gpkg = STUDY / "studiegebied.gpkg"
    for name, label, symbol in (
            ("buurt", f"{STUDY_NAME}: de vergeleken cellen",
             QgsFillSymbol.createSimple({"color": "0,0,0,0", "outline_color": "#e31a1c", "outline_width": "0.8"})),
            ("bomen", "Bomen",
             QgsMarkerSymbol.createSimple({"name": "circle", "color": "#1a9850", "outline_color": "#ffffff",
                                           "size": "1.8"}))):
        layer = QgsVectorLayer(f"{gpkg}|layername={name}", label, "ogr")
        if _add(project, top, layer):
            layer.setRenderer(QgsSingleSymbolRenderer(symbol))

    # The compared cells: inside the buurt, not NoData (water) in the benchmark
    bench, nodata = _read(STUDY / "benchmark.tif")
    area = (_read(STUDY / "buurt.tif")[0] == 1) & (bench != (nodata if nodata is not None else -2147483647))
    for i, (stem, title, text) in enumerate(BENCHMARK_RUNS, 1):
        tif = STUDY / f"{stem}.tif"
        if not tif.exists():
            continue
        v = _cells(tif, area)
        group = _group(top, f"{i}. {title}", expanded=i == 1)
        layer = QgsRasterLayer(str(tif), f"Zichtbare bomen per cel: gemiddeld {v.mean():.1f}, "
                                         f"{100 * (v >= 3).mean():.0f}% van de cellen ≥ 3".replace(".", ","),
                               "gdal")
        if _add(project, group, layer, visible=i == 1):
            _style_viewshed(layer)
            _explain(layer, text)
        diff = STUDY / f"verschil_{stem}.tif"
        if diff.exists():
            b = _cells(STUDY / "benchmark.tif", area)
            layer = QgsRasterLayer(str(diff), f"Verschil met de benchmark: {100 * (v == b).mean():.0f}% gelijk, "
                                              f"{100 * ((v >= 3) == (b >= 3)).mean():.0f}% zelfde oordeel ≥ 3",
                                   "gdal")
            if _add(project, group, layer, visible=False):
                classes = [(0, QColor(0, 0, 0, 0), "gelijk"), (1, QColor("#ff7f00"), "telt meer bomen"),
                           (2, QColor("#1f78b4"), "telt minder bomen")]
                layer.setRenderer(QgsPalettedRasterRenderer(
                    layer.dataProvider(), 1, [QgsPalettedRasterRenderer.Class(v_, c, l) for v_, c, l in classes]))
    dsm = STUDY / "dem_raw.tif"
    if dsm.exists():
        layer = QgsRasterLayer(str(dsm), "Hoogtemodel AHN5 ruw (wat de benchmark zag)", "gdal")
        if _add(project, top, layer, visible=False):
            layer.setRenderer(QgsHillshadeRenderer(layer.dataProvider(), 1, 315, 45))


def _add_the_300(project, parent):
    """The walking distance per home as on the map, the green and its entrances."""
    top = _group(parent, "De 300 – loopafstand tot groen", expanded=False)
    if FINAL_300.exists():
        layer = QgsVectorLayer(f"{FINAL_300}|layername=homes", "Woningen: loopafstand tot groen", "ogr")
        if _add(project, top, layer, visible=False):
            root_rule = QgsRuleBasedRenderer.Rule(None)
            for expr, colour, label in (('"afstand_m" <= 300', "#1a9850", "binnen 300 m"),
                                        ('"afstand_m" <= 425', "#91cf60", "300 m - 5 min"),
                                        ('"afstand_m" <= 1275', "#fdae61", "5 - 15 min"),
                                        ("ELSE", "#d7301f", "verder dan 15 min")):
                symbol = QgsMarkerSymbol.createSimple({"name": "circle", "color": colour,
                                                       "outline_style": "no", "size": "1.8"})
                root_rule.appendChild(QgsRuleBasedRenderer.Rule(symbol, filterExp=expr, label=label))
            layer.setRenderer(QgsRuleBasedRenderer(root_rule))
            _zoomed_in(layer)
    if OPEN_300.exists():
        entrances = QgsVectorLayer(f"{OPEN_300}|layername=entrances", "Ingangen van het groen", "ogr")
        if _add(project, top, entrances, visible=False):
            cats = [QgsRendererCategory(s, QgsMarkerSymbol.createSimple(
                {"name": "circle", "color": c, "outline_color": "#ffffff", "size": "2"}), label)
                for s, c, label in (("pad", "#c4521f", "pad kruist de rand"), ("poort", "#7b3294", "OSM-poort"),
                                    ("rand", "#e6a100", "open rand langs straat"))]
            entrances.setRenderer(QgsCategorizedSymbolRenderer("soort", cats))
            _zoomed_in(entrances, 50000)
        parks = QgsVectorLayer(f"{OPEN_300}|layername=parks", "Groengebieden", "ogr")
        if _add(project, top, parks, visible=False):
            parks.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple(
                {"color": "47,125,50,120", "outline_color": "#1b5e20", "outline_width": "0.2"})))


def _add_inputs(project, parent):
    """All trees, the tree heights and both surface models."""
    top = _group(parent, "Invoer – bomen en hoogtemodel", expanded=False)
    if TREES.exists():
        layer = QgsVectorLayer(f"{TREES}|layername=province_trees", "Alle bomen (vanaf 1:10.000)", "ogr")
        if _add(project, top, layer):
            layer.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple(
                {"name": "circle", "color": "#1a9850", "outline_color": "#ffffff",
                 "outline_width": "0.1", "size": "1.6"})))
            _zoomed_in(layer, 10000)
    for tag, gpkg in TREE_HEIGHTS.items():
        if gpkg.exists():
            _add(project, top, QgsVectorLayer(str(gpkg), f"Boomhoogtes Delft ({tag})", "ogr"), visible=False)
    for tag, vrt in (("AHN5, hoogste punt per cel", DEM_AHN5), ("AHN4", DEM_AHN4)):
        if vrt.exists():
            layer = QgsRasterLayer(str(vrt), f"Hoogtemodel {tag}", "gdal")
            if _add(project, top, layer, visible=False):
                layer.setRenderer(QgsHillshadeRenderer(layer.dataProvider(), 1, 315, 45))


def _add_background(project, parent):
    top = _group(parent, "Achtergrond", expanded=False)
    _add(project, top, QgsRasterLayer(BRT_WMTS, "BRT achtergrondkaart (grijs)", "wms"))
    _add(project, top, QgsRasterLayer(LUCHTFOTO_WMS, "Luchtfoto", "wms"), visible=False)


def main():
    qgs = QgsApplication([], False)       # QGIS without a window
    qgs.initQgis()
    try:
        if PROJECT_PATH.exists():
            shutil.copy2(PROJECT_PATH, BACKUP_PATH)
        project = QgsProject.instance()
        project.setCrs(QgsCoordinateReferenceSystem("EPSG:28992"))
        project.setTitle("330300regel — validatie 3-30-300")
        project.writeEntryBool("Paths", "/Absolute", False)   # store relative paths
        root = project.layerTreeRoot()

        _add_the_3(project, root, PROCESSED_DIR, "AHN5",
                   keep=lambda p: p.stat().st_mtime >= AHN5_SINCE, on=True)
        if AHN4_DIR.exists():
            _add_the_3(project, root, AHN4_DIR, "AHN4", keep=lambda p: True, on=False)
        else:
            print(f"  NOTE: {AHN4_DIR} not readable (R: drive?) — AHN4 group skipped")
        _add_benchmark(project, root)
        _add_the_300(project, root)
        _add_inputs(project, root)
        _add_background(project, root)

        if not project.write(str(PROJECT_PATH)):
            sys.exit(f"ERROR: could not write {PROJECT_PATH}")
        print(f"Wrote {PROJECT_PATH} — {len(project.mapLayers())} layers")
    finally:
        qgs.exitQgis()


if __name__ == "__main__":
    main()
