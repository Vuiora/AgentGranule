"""Shared native 3D diagram of translucent, flat module regions.

Each region is a chamfered rectangular prism, never a sphere.  All regions use
one world-space coordinate system and one perspective camera.  Rendering clips
camera rays against the actual prism planes and alpha-composites their front
and back surfaces by depth, so overlapping areas retain their colours.  Visual
overlap is a layout choice and does not infer shared module responsibilities.
"""

from __future__ import annotations

import math

from .core import GranuleError, _design_effort_units

MIN_VOLUME = 0.125
MAX_VOLUME = 8.0
PLACEHOLDER_VOLUME = 1.0
REGION_THICKNESS = 0.35
CORNER_CUT = 0.28
# A square of side 2 with four corner triangles removed.
UNIT_AREA = 4.0 - 2.0 * CORNER_CUT ** 2
REGION_FACES = (
    tuple(range(8)), tuple(range(8, 16)),
    *((i, (i + 1) % 8, (i + 1) % 8 + 8, i + 8) for i in range(8)),
)
_NORMALS = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0),
            (0, 0, 1), (0, 0, -1),
            (1, 1, 0), (1, -1, 0), (-1, 1, 0), (-1, -1, 0))
BACKGROUND = (240, 245, 241)
MAX_RENDER_WIDTH = 320
MAX_RENDER_HEIGHT = 240


def region_volume(effort):
    """Linear volume mapping; zero and unassigned regions remain visible."""
    if effort is None:
        return PLACEHOLDER_VOLUME
    return MIN_VOLUME + (MAX_VOLUME - MIN_VOLUME) * _design_effort_units(effort) / 100


def region_vertices(center, effort):
    """Return 16 actual 3D vertices, with fixed thickness and varying area."""
    cx, cy, cz = _vector(center, "region center")
    half = math.sqrt(region_volume(effort) / (REGION_THICKNESS * UNIT_AREA))
    cut = CORNER_CUT * half
    outline = ((-half, -half + cut), (-half + cut, -half),
               (half - cut, -half), (half, -half + cut),
               (half, half - cut), (half - cut, half),
               (-half + cut, half), (-half, half - cut))
    return [(cx + x, cy + y, cz + dz)
            for dz in (-REGION_THICKNESS / 2, REGION_THICKNESS / 2)
            for x, y in outline]


def region_positions(modules):
    """Deterministic compact layout, independent of parent/dependency meaning.

    All modules share the diagram.  A spiral spreads their centers in its X/Y
    plane; small Z offsets expose depth when the common camera rotates.
    """
    ids = [module["id"] for module in modules]
    if any(not isinstance(key, str) or not key for key in ids) or len(set(ids)) != len(ids):
        raise GranuleError("Diagram module IDs must be unique non-empty strings")
    ids.sort()
    if not ids:
        return {}
    if len(ids) == 1:
        return {ids[0]: (0.0, 0.0, 0.0)}
    angle = math.pi * (3 - math.sqrt(5))
    points = {key: (0.67 * math.sqrt(i + 0.5) * math.cos(i * angle),
                    0.67 * math.sqrt(i + 0.5) * math.sin(i * angle),
                    (i % 5 - 2) * 0.13)
              for i, key in enumerate(ids)}
    averages = tuple(sum(p[axis] for p in points.values()) / len(points) for axis in range(3))
    return {key: tuple(value - averages[axis] for axis, value in enumerate(point))
            for key, point in points.items()}


venn_positions = region_positions


def _vector(value, name):
    try:
        result = tuple(float(component) for component in value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise GranuleError(f"{name} must contain three finite numbers") from exc
    if len(result) != 3 or not all(math.isfinite(component) for component in result):
        raise GranuleError(f"{name} must contain three finite numbers")
    return result


def _camera(camera, width, height):
    result = {"yaw": 0.6, "pitch": -0.35, "distance": 10.0, "focal": 500.0,
              "center": (width / 2, height / 2)}
    result.update(camera)
    for key in ("yaw", "pitch", "distance", "focal"):
        if not isinstance(result[key], (int, float)) or not math.isfinite(result[key]):
            raise GranuleError(f"Camera {key} must be finite")
    if result["distance"] <= 0 or result["focal"] <= 0:
        raise GranuleError("Camera distance and focal length must be positive")
    try:
        center = tuple(float(v) for v in result["center"])
    except (TypeError, ValueError, OverflowError) as exc:
        raise GranuleError("Camera center must contain two finite numbers") from exc
    if len(center) != 2 or not all(math.isfinite(v) for v in center):
        raise GranuleError("Camera center must contain two finite numbers")
    result["center"] = center
    return result


def _rotation(camera):
    return (math.cos(camera["yaw"]), math.sin(camera["yaw"]),
            math.cos(camera["pitch"]), math.sin(camera["pitch"]))


def _rotate(point, rotation):
    x, y, z = point
    cy, sy, cp, sp = rotation
    x, z = x * cy + z * sy, -x * sy + z * cy
    return (x, y * cp - z * sp, y * sp + z * cp)


def _inverse_rotate(point, rotation):
    x, y, z = point
    cy, sy, cp, sp = rotation
    y, z = y * cp + z * sp, -y * sp + z * cp
    return (x * cy - z * sy, y, x * sy + z * cy)


def _project(point, camera, rotation):
    x, y, z = _rotate(point, rotation)
    depth = camera["distance"] + z
    if depth <= 0:
        raise GranuleError("3D camera must remain outside the module diagram")
    return (camera["center"][0] + x * camera["focal"] / depth,
            camera["center"][1] - y * camera["focal"] / depth, depth)


def _color(value):
    if isinstance(value, str) and len(value) == 7 and value.startswith("#"):
        try:
            value = tuple(int(value[start:start + 2], 16) for start in (1, 3, 5))
        except ValueError as exc:
            raise GranuleError("Region colour must be an RGB tuple or #rrggbb") from exc
    if (not isinstance(value, (tuple, list)) or len(value) != 3 or
            any(type(v) is not int or not 0 <= v <= 255 for v in value)):
        raise GranuleError("Region colour must be an RGB tuple or #rrggbb")
    return tuple(value)


def _clip(direction, numerators):
    """Clip one ray against the ten convex prism planes."""
    near, far, near_plane, far_plane = 0.0, math.inf, None, None
    dx, dy, dz = direction
    denominators = (dx, -dx, dy, -dy, dz, -dz, dx + dy, dx - dy, -dx + dy, -dx - dy)
    for index, (denominator, numerator) in enumerate(zip(denominators, numerators)):
        if abs(denominator) < 1e-12:
            if numerator < 0:
                return None
            continue
        depth = numerator / denominator
        if denominator < 0:
            if depth > near:
                near, near_plane = depth, index
        elif depth < far:
            far, far_plane = depth, index
        if near > far:
            return None
    if far <= 0 or near_plane is None:
        return None
    return near, far, near_plane, far_plane


def _ray(point, camera, rotation):
    return _inverse_rotate(((point[0] - camera["center"][0]) / camera["focal"],
                            -(point[1] - camera["center"][1]) / camera["focal"], 1), rotation)


def render_scene(regions, camera, width, height, pixel_step=3):
    """Render depth-composited translucent prisms into Tk-compatible PPM bytes.

    ``regions`` contains ``module_id``, ``center``, ``effort`` and RGB ``color``.
    The returned image is downsampled by at least ``pixel_step``; the raster is
    capped at 320 by 240 samples even when the native window is enlarged.
    Native Tk can display
    ``PhotoImage(data=scene['ppm'], format='PPM').zoom(scene['pixel_step'])``.
    Centers/faces use original canvas pixels, allowing crisp labels and lines.
    No database, approval, host UI or external library is accessed.
    """
    if (type(width) is not int or type(height) is not int or width <= 0 or height <= 0 or
            type(pixel_step) is not int or not 1 <= pixel_step <= 16):
        raise GranuleError("Diagram dimensions must be positive integers and pixel_step must be 1–16")
    pixel_step = max(pixel_step, math.ceil(width / MAX_RENDER_WIDTH), math.ceil(height / MAX_RENDER_HEIGHT))
    camera = _camera(camera, width, height)
    rotation = _rotation(camera)
    origin = _inverse_rotate((0, 0, -camera["distance"]), rotation)
    prepared, centers, faces, all_vertices = [], [], [], []
    ids = set()
    for region in regions:
        key = region.get("module_id")
        if not isinstance(key, str) or not key or key in ids:
            raise GranuleError("Diagram module IDs must be unique non-empty strings")
        ids.add(key)
        center = _vector(region["center"], "region center")
        vertices = region_vertices(center, region.get("effort"))
        projected = [_project(point, camera, rotation) for point in vertices]
        half = (vertices[2][0] - center[0]) / (1 - CORNER_CUT)
        diagonal = (2 - CORNER_CUT) * half
        distances = (half, half, half, half, REGION_THICKNESS / 2, REGION_THICKNESS / 2,
                     diagonal, diagonal, diagonal, diagonal)
        relative = tuple(origin[axis] - center[axis] for axis in range(3))
        numerators = tuple(distance - sum(n * v for n, v in zip(normal, relative))
                           for normal, distance in zip(_NORMALS, distances))
        color = _color(region.get("color", (92, 144, 163)))
        # A fixed light makes the finite thickness legible when the camera rotates.
        shaded = []
        for normal in _NORMALS:
            nx, ny, nz = _rotate(normal, rotation)
            norm = math.sqrt(nx * nx + ny * ny + nz * nz)
            light = max(0.0, (-0.3 * nx + 0.45 * ny - nz) / (1.137 * norm))
            shade = 0.76 + 0.24 * light
            shaded.append(tuple(round(channel * shade) for channel in color))
        bbox = (min(point[0] for point in projected), min(point[1] for point in projected),
                max(point[0] for point in projected), max(point[1] for point in projected))
        prepared.append({"module_id": key, "numerators": numerators, "bbox": bbox,
                         "center": center, "colors": tuple(shaded)})
        center_projection = _project(center, camera, rotation)
        centers.append((key, *center_projection))
        for face in REGION_FACES:
            points = [projected[index] for index in face]
            faces.append({"module_id": key, "points": points,
                          "depth": sum(point[2] for point in points) / len(points)})
        all_vertices.extend(vertices)
    out_width = (width + pixel_step - 1) // pixel_step
    out_height = (height + pixel_step - 1) // pixel_step
    layers = [[] for _ in range(out_width * out_height)]
    # Rays are shared by every module in the same camera space.
    rays = [_ray((min(width - 0.5, (x + 0.5) * pixel_step),
                  min(height - 0.5, (y + 0.5) * pixel_step)), camera, rotation)
            for y in range(out_height) for x in range(out_width)]
    for region in prepared:
        x0, y0, x1, y1 = region["bbox"]
        left, right = max(0, int(x0 // pixel_step)), min(out_width - 1, int(x1 // pixel_step))
        top, bottom = max(0, int(y0 // pixel_step)), min(out_height - 1, int(y1 // pixel_step))
        for y in range(top, bottom + 1):
            for x in range(left, right + 1):
                index = y * out_width + x
                hit = _clip(rays[index], region["numerators"])
                if hit is not None:
                    near, far, near_plane, far_plane = hit
                    # Two individually transparent surfaces preserve all layers.
                    layers[index].append((near, region["colors"][near_plane], 0.30))
                    layers[index].append((far, region["colors"][far_plane], 0.12))
    pixels = bytearray()
    for pixel in layers:
        rgb = list(BACKGROUND)
        for _, color, alpha in sorted(pixel, reverse=True):
            rgb = [old * (1 - alpha) + channel * alpha for old, channel in zip(rgb, color)]
        pixels.extend(round(channel) for channel in rgb)
    ppm = f"P6\n{out_width} {out_height}\n255\n".encode("ascii") + pixels
    bounds = (tuple(min(point[axis] for point in all_vertices) for axis in range(3)),
              tuple(max(point[axis] for point in all_vertices) for axis in range(3))) if all_vertices else None
    return {"ppm": ppm, "width": out_width, "height": out_height, "pixel_step": pixel_step,
            "canvas_width": width, "canvas_height": height, "centers": centers,
            "faces": faces, "regions": prepared, "bounds": bounds, "camera": camera}


def hit_regions(point, scene, min_target=18):
    """Return every ray-hit module, nearest first, retaining overlap choices.

    A small center target is only used when no region is hit; it never replaces
    the actual overlap list with whichever module was drawn last.
    """
    direction = _ray(point, scene["camera"], _rotation(scene["camera"]))
    hits = []
    for region in scene["regions"]:
        hit = _clip(direction, region["numerators"])
        if hit is not None:
            hits.append((hit[0], region["module_id"]))
    if hits:
        return tuple(key for _, key in sorted(hits))
    targets = [(depth, key) for key, x, y, depth in scene["centers"]
               if math.hypot(point[0] - x, point[1] - y) <= min_target]
    return tuple(key for _, key in sorted(targets))


hit_modules = hit_regions
