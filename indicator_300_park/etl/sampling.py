"""
sampling.py — Points at a fixed spacing along a line, for the nearest-point lookup of the homes.

Used by 02_valhalla.py and 03_grass.py. numpy works per line instead of per
point (OGR's Geometry.Value), which matters for a province: about 20 million
points along 40,000 km of paths.
"""
import numpy as np


def along(geom, step):
    """(x, y, s, length) for points every `step` metres along an OGR line, ends included.
    s is the distance of each point from the line's first vertex."""
    pts = np.asarray(geom.GetPoints(), dtype=float)[:, :2]
    seg = np.hypot(np.diff(pts[:, 0]), np.diff(pts[:, 1]))
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    length = float(cum[-1])
    n = max(int(length // step), 1)
    s = np.linspace(0.0, length, n + 1)
    return np.interp(s, cum, pts[:, 0]), np.interp(s, cum, pts[:, 1]), s, length
