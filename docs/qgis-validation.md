# QGIS validation project

`qgis_validation/build_project.py` builds `qgis_validation/330300regel_validatie.qgz` (gitignored), a QGIS project with every result, for checking them visually.

```
C:\...\OSGeo4W\bin\python-qgis-ltr.bat qgis_validation\build_project.py
```

It needs QGIS's own Python (PyQGIS); the plain OSGeo4W Python will not do. `run_all_municipalities.sh` calls it after every municipality.

## Groups

| Group | What |
|---|---|
| De 300: groen op loopafstand (FME) | home buildings inside and outside the 5- and 15-minute zones, the walking zones, the park entrances |
| De 30: kroonbedekking per wijk / buurt 2023 (FME) | canopy cover per CBS wijk and buurt 2023; grey = no crown data |
| De 3 per gemeente / wijk / buurt | share of homes with >= 3 visible trees, in the web map's colours |
| Woningen | every municipality's (and the province's) scored buildings, by class |
| Viewshed | every municipality's tree-count raster |
| Bomen | all 5.2 million trees (drawn only when zoomed in) and tree heights |
| Hoogtemodel | the province DEM mosaic as a hillshade, for spotting integer terraces, holes and seams |
| Experimenten | the corrected and the original ArcGIS benchmark |
| Eén boom / boomgroep | the single-tree and tree-group comparison, with difference maps against the exact test |
| Studiegebied | the Delft study area: benchmark vs ArcGIS vs GDAL |
| 3D BAG, Achtergrond | 3D buildings (WMS and 3D Tiles), the BRT grey map and the aerial photo |

Layer paths are stored relative to the project, so the folder can move with the repository.

## Re-running

A re-run adds layers for new files and removes layers whose file is gone. It keeps everything else you changed in QGIS: styling, visibility, extra layers. To rebuild from scratch, delete the `.qgz`. Close the project in QGIS before a re-run, or QGIS may overwrite the update when you save.
