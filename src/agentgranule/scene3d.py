"""Pure world-space meshes for the native OpenGL module diagram.

The shared board is a 100% floor partition. Columns are separate, inset
closed meshes; heights describe display shares, never persisted effort values.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from dataclasses import dataclass

from .core import GranuleError
from .heights import box_vertices, maximum_height, proportional_heights
from .proportions import normalized_shares, treemap_rectangles

COLORS = ((55, 147, 117), (83, 127, 194), (188, 107, 144), (208, 154, 65),
          (137, 116, 191), (56, 153, 173), (183, 115, 76), (110, 156, 78))
COLUMN_INSET = 0.60
BOARD_WIDTH = 10.0
BOARD_HEIGHT = 7.0
BOARD_THICKNESS = 0.35


@dataclass(frozen=True)
class Mesh:
    """Unindexed triangle vertices: each contains XYZ and a flat face normal."""

    vertices: tuple[tuple[float, float, float, float, float, float], ...]
    edges: tuple[tuple[float, float, float], ...] = ()


def cuboid_mesh(vertices):
    """Triangulate a closed cuboid with fixed outward normals and winding."""
    points = tuple(vertices)
    if not points:
        return Mesh(())
    if len(points) != 8 or any(len(p) != 3 or not all(math.isfinite(v) for v in p) for p in points):
        raise GranuleError("A native cuboid requires eight finite XYZ vertices")
    faces = (((0, 3, 2, 1), (0, 0, -1)), ((4, 5, 6, 7), (0, 0, 1)),
             ((0, 1, 5, 4), (0, -1, 0)), ((1, 2, 6, 5), (1, 0, 0)),
             ((2, 3, 7, 6), (0, 1, 0)), ((3, 0, 4, 7), (-1, 0, 0)))
    triangles = tuple((*points[i], *normal) for face, normal in faces
                      for i in (face[0], face[1], face[2], face[0], face[2], face[3]))
    pairs = ((0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6),
             (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7))
    return Mesh(triangles, tuple(points[i] for pair in pairs for i in pair))


def floor_mesh(points):
    if len(points) != 4:
        raise GranuleError("A native floor requires four corners")
    return Mesh(tuple((*points[i], 0.0, 0.0, 1.0) for i in (0, 1, 2, 0, 2, 3)))


def build_scene(modules, efforts, direction, *, width=BOARD_WIDTH, height=BOARD_HEIGHT):
    """Build complete world geometry without GUI imports, approval or writes."""
    if not isinstance(modules, list) or not isinstance(efforts, Mapping):
        raise GranuleError("Native scene requires a module list and effort mapping")
    ids = [m.get("id") for m in modules if isinstance(m, Mapping)]
    if (len(ids) != len(modules) or any(not isinstance(key, str) or not key.strip() for key in ids)
            or len(set(ids)) != len(ids)):
        raise GranuleError("Native scene module IDs must be unique non-empty strings")
    if not isinstance(direction, str):
        raise GranuleError("Native scene direction must be a string")
    applicable = {m["id"] for m in modules if direction in m.get("directions", [])}
    allocation = normalized_shares({key: efforts.get(key) for key in ids}, applicable)
    rectangles = treemap_rectangles(allocation["shares"], width, height)
    span = max(width, height)
    heights = proportional_heights(allocation["shares"], span)
    by_id = {m["id"]: m for m in modules}
    tiles = {}
    for index, key in enumerate(sorted(ids)):
        x0, y0, x1, y1 = rectangles[key]
        rectangle = (x0 - width / 2, y0 - height / 2, x1 - width / 2, y1 - height / 2)
        left, bottom, right, top = rectangle
        corners = tuple((x, y, 0.0) for x, y in ((left, bottom), (right, bottom),
                                               (right, top), (left, top)))
        cx, cy = (left + right) / 2, (bottom + top) / 2
        dx, dy = (right - left) * COLUMN_INSET / 2, (top - bottom) * COLUMN_INSET / 2
        footprint = (cx - dx, cy - dy, cx + dx, cy + dy)
        vertices = box_vertices(footprint, heights[key])
        tiles[key] = {"id": key, "module": copy.deepcopy(by_id[key]), "rectangle": rectangle,
                      "floor_corners": corners, "floor": floor_mesh(corners),
                      "vertices": vertices, "mesh": cuboid_mesh(vertices),
                      "height": heights[key], "center": (cx, cy, 0.0),
                      "color": COLORS[index % len(COLORS)] if key in applicable else (150, 161, 157)}
    board = box_vertices((-width / 2, -height / 2, width / 2, height / 2), BOARD_THICKNESS,
                         base_z=-BOARD_THICKNESS)
    return {"tiles": tiles, "allocation": allocation, "rectangles": rectangles,
            "width": width, "height": height, "maximum_height": maximum_height(span),
            "board": cuboid_mesh(board), "raw_efforts": copy.deepcopy(dict(efforts)),
            "direction": direction}


def point_in_polygon(point, polygon):
    """Inclusive, winding-independent test for projected convex floor cells."""
    if len(polygon) < 3:
        return False
    x, y = point
    signs = []
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        cross = (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0])
        if abs(cross) > 1e-7:
            signs.append(cross > 0)
    return bool(signs) and (all(signs) or not any(signs))
