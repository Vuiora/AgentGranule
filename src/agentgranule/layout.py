"""View-aware spacing for unrelated regions in one native 3D scene.

Explicit parent and dependency edges define undirected connected groups.  A
group retains the compact, overlapping region layout; unrelated groups reserve
space for their largest legal regions.  Camera-plane translations are mapped
back into real world coordinates, so the existing shared perspective renderer
and its ray picking continue to operate on actual three-dimensional prisms.
"""

from __future__ import annotations

import math

from .core import GranuleError
from .venn import _inverse_rotate, _project, _rotate, region_positions, region_vertices

GROUP_GAP = 1.0


def related_components(modules):
    """Return deterministic groups joined by existing parent/dependency edges.

    Connections are used only for spacing, never added to the module graph.
    Directed graph validation remains the responsibility of the design backend.
    Missing references are rejected rather than silently inventing a group.
    """
    modules = list(modules)
    ids = [module.get("id") for module in modules]
    if (any(not isinstance(key, str) or not key for key in ids) or
            len(set(ids)) != len(ids)):
        raise GranuleError("Diagram module IDs must be unique non-empty strings")
    parents = {key: key for key in ids}

    def find(key):
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    for module in modules:
        dependencies = module.get("depends_on", [])
        if not isinstance(dependencies, (list, tuple)):
            raise GranuleError("Diagram dependencies must be a list of module IDs")
        links = list(dependencies)
        if module.get("parent_id") is not None:
            links.append(module["parent_id"])
        for linked in links:
            if not isinstance(linked, str) or linked not in parents:
                raise GranuleError("Diagram relationships must refer to existing module IDs")
            left, right = find(module["id"]), find(linked)
            if left != right:
                # Canonical roots make union independent of input ordering.
                parents[max(left, right)] = min(left, right)
    groups = {}
    for key in sorted(ids):
        groups.setdefault(find(key), []).append(key)
    return tuple(tuple(group) for _, group in sorted(groups.items()))


def _bounds(points):
    return (min(point[0] for point in points), min(point[1] for point in points),
            max(point[0] for point in points), max(point[1] for point in points))


def separated_scene_layout(modules, width, height, *, yaw=0.6, pitch=-0.35, zoom=1.0):
    """Return positions and one fitted camera with disjoint unrelated groups.

    Every cell reserves the projected extent at effort 1.00, including depth.
    The layout therefore does not depend on the current effort or direction.
    Recompute it when rotating or resizing: fixed world distances alone cannot
    prevent a camera from looking through one unrelated region into another.

    Perspective displacement, measured in camera-plane world units, is bounded
    by ``M * Z / (D - Z)``.  The distance below keeps it below one eighth of the
    reserved cell gap.  Two unrelated groups consequently retain more than
    three quarters of that gap before the common positive focal/zoom scaling.
    Smaller regions are contained in the maximum prism, including effort zero
    and an unassigned placeholder, so they inherit this separation guarantee.

    ``projected_bounds`` are maximum-size module pixel rectangles;
    ``component_bounds`` correspond to the ordered ``components``.  All data is
    local geometry: this function does not read or persist a human allocation.
    """
    if (type(width) is not int or type(height) is not int or width <= 0 or height <= 0):
        raise GranuleError("Diagram dimensions must be positive integers")
    for key, value in (("yaw", yaw), ("pitch", pitch), ("zoom", zoom)):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise GranuleError(f"Diagram {key} must be finite")
    if zoom <= 0:
        raise GranuleError("Diagram zoom must be positive")
    modules = list(modules)
    components = related_components(modules)
    lookup = {module["id"]: module for module in modules}
    rotation = (math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch))
    local_positions, group_boxes = {}, []
    for group in components:
        positions = region_positions([lookup[key] for key in group])
        local_positions.update(positions)
        points = [_rotate(point, rotation) for key in group
                  for point in region_vertices(positions[key], 1.0)]
        group_boxes.append(_bounds(points))

    usable_width = max(70, width - 290)
    usable_height = max(70, height - 76)
    max_width = max((box[2] - box[0] for box in group_boxes), default=1)
    max_height = max((box[3] - box[1] for box in group_boxes), default=1)
    cell_width, cell_height = max_width + GROUP_GAP, max_height + GROUP_GAP
    count = len(components)
    columns = min(count, max(1, round(math.sqrt(
        count * usable_width * cell_height / (usable_height * cell_width))))) if count else 1
    rows = (count + columns - 1) // columns
    positions = {}
    for index, (group, box) in enumerate(zip(components, group_boxes)):
        row, column = divmod(index, columns)
        row_count = min(columns, count - row * columns)
        target_x = (column - (row_count - 1) / 2) * cell_width
        target_y = ((rows - 1) / 2 - row) * cell_height
        offset = _inverse_rotate((target_x - (box[0] + box[2]) / 2,
                                  target_y - (box[1] + box[3]) / 2, 0), rotation)
        for key in group:
            positions[key] = tuple(local_positions[key][axis] + offset[axis] for axis in range(3))

    vertices = {key: [_rotate(point, rotation) for point in region_vertices(position, 1.0)]
                for key, position in positions.items()}
    points = [point for shape in vertices.values() for point in shape]
    extent_xy = max((max(abs(point[0]), abs(point[1])) for point in points), default=0)
    extent_z = max((abs(point[2]) for point in points), default=0)
    distance = max(8.0, extent_z + 1 + 8 * extent_z * extent_xy / GROUP_GAP)
    normalized = [(point[0] / (distance + point[2]), -point[1] / (distance + point[2]))
                  for point in points]
    span_x = max((abs(point[0]) for point in normalized), default=1)
    span_y = max((abs(point[1]) for point in normalized), default=1)
    focal = min(usable_width / 2 / max(span_x, 1e-9),
                usable_height / 2 / max(span_y, 1e-9)) * zoom
    camera = {"yaw": yaw, "pitch": pitch, "distance": distance, "focal": focal,
              "center": (width / 2, height / 2 + 4)}
    projected_bounds = {
        key: _bounds([_project(point, camera, rotation)
                      for point in region_vertices(position, 1.0)])
        for key, position in positions.items()
    }
    component_bounds = tuple((min(projected_bounds[key][0] for key in group),
                              min(projected_bounds[key][1] for key in group),
                              max(projected_bounds[key][2] for key in group),
                              max(projected_bounds[key][3] for key in group))
                             for group in components)
    return {"positions": positions, "camera": camera, "components": components,
            "projected_bounds": projected_bounds, "component_bounds": component_bounds}
