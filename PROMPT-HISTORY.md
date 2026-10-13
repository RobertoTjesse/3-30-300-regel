# Prompt history

A summary of the prompts given to Claude Code in this project, taken from the session logs on this workstation. It covers 29 September to 13 October 2026. Sessions from before the repo moved to `D:\Repositories\330300regel` were not kept, so they are missing here.

## 29 September: consistency check and the ArcGIS viewshed

- Check the whole project for inconsistencies and fix them, including the QGIS project, whose file paths were broken. The first wiki page was created.
- For the 300 rule, read the FME workspace to find out which dataset was used.
- Recreate a colleague's ArcGIS Pro viewshed analysis for the study area with AHN5. The user ran the script from the ArcGIS Python prompt and reported the "Incorrect number of format variables" messages.
- Compare the ArcGIS Pro settings with our GDAL settings, mainly on self-occlusion. Which height did the colleague use, why does ArcGIS show so little self-occlusion, when does NAP come into play, and does the tree dataset have a height value? A script for ArcGIS to test this.
- Do not push the experiments to GitHub.

## 1 October: explanation, green areas and a Dutch version

- Explain the arcpy analysis again and why its result is higher, and show the exact code where the raster value is counted twice.
- Add the polygons of the green areas to the 300 map.
- Update the GitHub issue now that the reason for the better ArcGIS result is known.
- Explain again how the 3 rule is calculated.
- Add a Dutch README and make the website available in Dutch first and English second.

## 4 October: mobile map, the 30 rule, COBRA and Europe

- Move the map centre on mobile so the legend no longer hides the south.
- The 30 rule: compute crown cover per buurt, wijk and gemeente from the large BKB2024 raster (crown cover 2024, made by Friedenau from lidar), keeping memory use in mind. Compare with CBS figures from 2024 or 2025, starting with a small area.
- Install a humanizer skill and use it to make the repo and wiki texts easier to read.
- Compare our method with COBRA Groeninzicht, which also computes the 3-30-300 rule for the Netherlands. Look at their Utrecht example map and file the PDF they publish.
- Look for a second study area in Europe with lidar and tree data, Spain if possible.

## 5 to 6 October: metadata, assumptions, benchmarks and Read the Docs

- Move the land cover file and fill in the metadata form.
- List the open issues. Commit the `docs/bronnen` folder.
- Make the README and the 3-30-300 explanation clearer about the assumptions that matter most, for example that using the tree centre avoids houses that only see branches, and that one green pixel turns the whole house green.
- A speed table for arcpy against GDAL. Then a larger study area, a whole buurt near the Malieveld in The Hague, with optimised versions of both, the same results from each, and graphs of the speed. Several long arcpy runs followed.
- Set up a Read the Docs site for the repo. Close the FME task.

## 8 October: tree status, documentation hosting and the 300 rule

- A summary of all prompts, covering methods, software, requirements, data sources and goals.
- How could missing trees be collected from the public through public GIS, using FME Flow and its new data virtualisation feature?
- Filter trees out of the 3 rule when they are recorded as disappeared or small. Fewer positives are better than false positives.
- Read the Docs: dropped because the login failed, then GitHub Pages, then back to Read the Docs once the login worked, and finally the documentation went to the wiki only.
- Find a way to compute the 300 rule on this workstation alone. Valhalla used to run on a Mac mini, and Docker cannot be installed here. A new dataset with green polygons is coming.
- Start the province rerun, and make GIFs that show step by step how the 3 rule works from the trees in a small neighbourhood, for the documentation.

## 12 October: the 300 with open software

- This summary.
- How to build a front end on MapStore and Cesium.
- A trial tab with Cobra's 30 within 500 m of every building, removed again.
- Install the Anatomy skill to explain the 3 with an interactive figure; set aside because Node is not installed.
- A new park dataset for the 300: find the entrances with OpenStreetMap and compute walking distances. Valhalla or something better, Valhalla through its public API, scipy, or a tool already installed with QGIS. Install pyvalhalla and download the OSM extract, and get both a GRASS and a Valhalla version ready to compare.
- Explain Valhalla's expansion on the explanation pages and in the wiki, with a picture centred on the Provinciehuis, and show clearly how the two methods differ.

## 13 October

- Count cycle paths as walking routes.
- Export the new green from the provincial SDE with an arcpy script, kept out of the repository; then use the copied voorzieningen geodatabase (`parken_vlakken`), TOP10NL functioneel gebied, and from the full TOP10NL list cemeteries and allotments, but no duck decoys.
- Run the parks for the whole province first and add the homes later; show the isochrones as maps.
- Run the homes with GRASS and Valhalla, benchmark and compare them and the FME result, put the comparison in the QGIS project, update the website and documentation (the analysis runs on OSGeo software), and clean up the repository.
