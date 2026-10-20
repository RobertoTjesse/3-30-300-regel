"""
in_green.py — Which homes lie inside a green area.

Used by 02_valhalla.py and 03_grass.py: a home inside a green area of
01_prepare.py (layer parks) is already in the green, so its walking distance is
0 m, however far it is from an entrance.
"""


def homes_in_green(prepared):
    """FIDs of the homes of 01_prepare.py that lie inside one of its green areas."""
    homes = prepared.GetLayerByName("homes")
    inside = set()
    for park in prepared.GetLayerByName("parks"):
        g = park.GetGeometryRef()
        homes.SetSpatialFilter(g)   # bounding box only; the exact test follows
        inside.update(f.GetFID() for f in homes if f.GetGeometryRef().Within(g))
    homes.SetSpatialFilter(None)
    return inside
