"""Shared native 3D diagrams of flat module regions.

Each region is a chamfered rectangular prism, never a sphere.  All regions use
one world-space coordinate system and one perspective camera.  Rendering clips
camera rays against the actual prism planes and alpha-composites their front
and back surfaces by depth, so overlapping areas retain their colours.  Visual
overlap is a layout choice and does not infer shared module responsibilities.
The proportional treemap renderer partitions one thin plate under an
orthographic camera, preserving relative module face areas while rotating.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

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
    if scene.get("projection") == "orthographic":
        return _hit_treemap_regions(point, scene, min_target)
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


def _treemap_rectangles(rectangles):
    if not isinstance(rectangles, Mapping):
        raise GranuleError("Treemap rectangles must map module IDs to four coordinates")
    result = {}
    if any(not isinstance(key, str) or not key for key in rectangles):
        raise GranuleError("Diagram module IDs must be unique non-empty strings")
    for key, value in sorted(rectangles.items()):
        try:
            rectangle = tuple(float(v) for v in value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise GranuleError("Treemap rectangles must contain four finite coordinates") from exc
        if (len(rectangle) != 4 or not all(math.isfinite(v) for v in rectangle) or
                rectangle[2] < rectangle[0] or rectangle[3] < rectangle[1]):
            raise GranuleError("Treemap rectangles must contain ordered finite coordinates")
        result[key] = rectangle
    active = []
    for key, rectangle in sorted(result.items(), key=lambda item: (item[1][0], item[0])):
        x0, y0, x1, y1 = rectangle
        if x1 == x0 or y1 == y0:
            continue
        active = [(other, bounds) for other, bounds in active if bounds[2] > x0 + 1e-10]
        if any(min(y1, bounds[3]) - max(y0, bounds[1]) > 1e-10 for _, bounds in active):
            raise GranuleError("Treemap module interiors must not overlap")
        active.append((key, rectangle))
    return result


def _inside_polygon(point, polygon):
    """Include the boundary of a convex projected face."""
    if len(polygon) < 3:
        return False
    cross = [(b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0])
             for a, b in zip(polygon, polygon[1:] + polygon[:1])]
    return all(v >= -1e-8 for v in cross) or all(v <= 1e-8 for v in cross)


def _orthographic_project(point, camera, rotation):
    wx, wy = camera["world_center"]
    x, y, z = _rotate((point[0] - wx, point[1] - wy, point[2]), rotation)
    return (camera["center"][0] + x * camera["focal"],
            camera["center"][1] - y * camera["focal"], camera["distance"] + z)


def _treemap_point(point, scene):
    """Intersect the orthographic view ray with the common module face."""
    camera = scene["camera"]
    cy, sy, cp, sp = _rotation(camera)
    if abs(cy * cp) < 1e-10:
        return None
    sx = (point[0] - camera["center"][0]) / camera["focal"]
    sy_screen = -(point[1] - camera["center"][1]) / camera["focal"]
    z = scene["front_z"]
    x = (sx - sy * z) / cy
    y = (sy_screen - sp * sy * x + sp * cy * z) / cp
    return x + camera["world_center"][0], y + camera["world_center"][1]


def _treemap_hit_key(world_point, scene):
    if world_point is None:
        return None
    x, y = world_point
    # Positive faces win over zero-share markers. Boundary ties are stable and
    # return one module, never an overlap menu for adjoining allocations.
    for region in scene["regions"]:
        x0, y0, x1, y1 = region["rectangle"]
        if x1 > x0 and y1 > y0 and x0 - 1e-10 <= x <= x1 + 1e-10 and y0 - 1e-10 <= y <= y1 + 1e-10:
            return region["module_id"]
    return None


def _hit_treemap_regions(point, scene, min_target):
    key = _treemap_hit_key(_treemap_point(point, scene), scene)
    if key is not None:
        return (key,)
    targets = [(math.hypot(point[0] - x, point[1] - y), key) for key, x, y, _ in scene["centers"]
               if math.hypot(point[0] - x, point[1] - y) <= min_target]
    return (min(targets)[1],) if targets else ()


def render_treemap_scene(rectangles, camera, width, height, pixel_step=3, colors=None):
    """Render one partitioned 3D plate with exactly proportional face areas.

    ``rectangles`` maps module IDs to ``(x0, y0, x1, y1)`` in one world XY
    plane; interiors must not overlap. All rectangles rotate together on the
    same thin plate. Orthographic projection preserves their relative areas at
    every non-edge-on angle. The plate fits its entire fixed bounds, so changing
    shares moves partition boundaries without compensating zoom. Zero-area
    modules retain centers for independent UI labels, but receive no visible
    area. The host should use separate label targets to select zero shares on a
    shared border; picking a positive face always selects its allocated area.

    Camera options include yaw/pitch, zoom, center and optional explicit focal
    (pixels per world unit); default fitting margins are 24px horizontal/40px
    vertical. Returned fields match ``render_scene``. ``faces`` contains only
    allocation faces; the shared plate's visible thickness is ``base_faces``.
    This pure renderer does not apply effort values or approve any setting.
    """
    if (type(width) is not int or type(height) is not int or width <= 0 or height <= 0 or
            type(pixel_step) is not int or not 1 <= pixel_step <= 16):
        raise GranuleError("Diagram dimensions must be positive integers and pixel_step must be 1–16")
    rectangles = _treemap_rectangles(rectangles)
    if colors is None:
        colors = {}
    if not isinstance(colors, Mapping):
        raise GranuleError("Treemap colours must map module IDs to RGB colours")
    prepared_colors = {key: _color(colors.get(key, (92, 144, 163))) for key in rectangles}
    pixel_step = max(pixel_step, math.ceil(width / MAX_RENDER_WIDTH), math.ceil(height / MAX_RENDER_HEIGHT))
    camera_options = dict(camera)
    camera = _camera(camera_options, width, height)
    for option, default in (("zoom", 1.0), ("margin_x", 24.0), ("margin_y", 40.0)):
        value = camera_options.get(option, default)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (option == "zoom" and value == 0):
            raise GranuleError(f"Treemap camera {option} must be finite and positive")
        camera[option] = value
    if rectangles:
        left = min(r[0] for r in rectangles.values())
        top = min(r[1] for r in rectangles.values())
        right = max(r[2] for r in rectangles.values())
        bottom = max(r[3] for r in rectangles.values())
    else:
        left, top, right, bottom = 0.0, 0.0, 1.0, 1.0
    if not math.isfinite(right - left) or not math.isfinite(bottom - top):
        raise GranuleError("Treemap world bounds must have finite extents")
    camera["world_center"] = (left + (right - left) / 2, top + (bottom - top) / 2)
    camera["projection"] = "orthographic"
    rotation = _rotation(camera)
    thickness = max(right - left, bottom - top, 1e-3) * 0.035
    vertices = [(x, y, z) for z in (-thickness / 2, thickness / 2)
                for x, y in ((left, top), (right, top), (right, bottom), (left, bottom))]
    rotated = [_rotate((x - camera["world_center"][0], y - camera["world_center"][1], z), rotation)
               for x, y, z in vertices]
    camera["distance"] = max(camera["distance"], 1 + max(abs(p[2]) for p in rotated))
    if "focal" not in camera_options:
        extent_x = max((abs(p[0]) for p in rotated), default=0)
        extent_y = max((abs(p[1]) for p in rotated), default=0)
        camera["focal"] = min(max(1.0, width / 2 - camera["margin_x"]) / max(extent_x, 1e-10),
                              max(1.0, height / 2 - camera["margin_y"]) / max(extent_y, 1e-10))
    camera["focal"] *= camera["zoom"]
    if not math.isfinite(camera["focal"]) or not math.isfinite(camera["distance"]):
        raise GranuleError("Treemap camera must produce finite projected coordinates")
    front_z = -thickness / 2 if rotation[0] * rotation[2] >= 0 else thickness / 2
    projected = [_orthographic_project(point, camera, rotation) for point in vertices]
    prepared, centers, faces, base_faces = [], [], [], []
    for key, rectangle in rectangles.items():
        x0, y0, x1, y1 = rectangle
        center = ((x0 + x1) / 2, (y0 + y1) / 2, front_z)
        center_projection = _orthographic_project(center, camera, rotation)
        centers.append((key, *center_projection))
        points = [_orthographic_project((x, y, front_z), camera, rotation)
                  for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
        bbox = (min(p[0] for p in points), min(p[1] for p in points),
                max(p[0] for p in points), max(p[1] for p in points))
        prepared.append({"module_id": key, "rectangle": rectangle, "center": center,
                         "bbox": bbox, "colors": (prepared_colors[key],), "points": points})
        if x1 > x0 and y1 > y0:
            faces.append({"module_id": key, "points": points,
                          "depth": sum(p[2] for p in points) / 4})
    # Only the plate's four exterior walls have thickness. Internal module
    # borders share one surface and cannot cover neighbouring allocations.
    for index, normal in enumerate(((0, -1, 0), (1, 0, 0), (0, 1, 0), (-1, 0, 0))):
        if _rotate(normal, rotation)[2] >= -1e-10:
            continue
        points = [projected[i] for i in (index, (index + 1) % 4, (index + 1) % 4 + 4, index + 4)]
        base_faces.append({"points": points, "depth": sum(p[2] for p in points) / 4})
    base_faces.sort(key=lambda face: face["depth"], reverse=True)
    out_width = (width + pixel_step - 1) // pixel_step
    out_height = (height + pixel_step - 1) // pixel_step
    scene = {"width": out_width, "height": out_height, "pixel_step": pixel_step,
             "canvas_width": width, "canvas_height": height, "centers": centers,
             "faces": faces, "base_faces": base_faces, "regions": prepared,
             "bounds": ((left, top, -thickness / 2), (right, bottom, thickness / 2)) if rectangles else None,
             "camera": camera, "projection": "orthographic", "front_z": front_z}
    pixels = bytearray(BACKGROUND * (out_width * out_height))
    for face in base_faces if faces else ():
        polygon = face["points"]
        for y in range(max(0, int(min(p[1] for p in polygon) // pixel_step)),
                       min(out_height - 1, int(max(p[1] for p in polygon) // pixel_step)) + 1):
            for x in range(max(0, int(min(p[0] for p in polygon) // pixel_step)),
                           min(out_width - 1, int(max(p[0] for p in polygon) // pixel_step)) + 1):
                point = (min(width - 0.5, (x + 0.5) * pixel_step), min(height - 0.5, (y + 0.5) * pixel_step))
                if _inside_polygon(point, polygon):
                    offset = (y * out_width + x) * 3
                    pixels[offset:offset + 3] = bytes(round(old * 0.4 + color * 0.6)
                                                    for old, color in zip(BACKGROUND, (120, 140, 141)))
    # Raster each rectangle's projected bounds, intersecting only the shared
    # front plane; colour never includes another module's face or back surface.
    for region in prepared:
        x0, y0, x1, y1 = region["rectangle"]
        if x1 <= x0 or y1 <= y0 or abs(rotation[0] * rotation[2]) < 1e-10:
            continue
        bx0, by0, bx1, by1 = region["bbox"]
        color = bytes(round(old * 0.22 + channel * 0.78)
                      for old, channel in zip(BACKGROUND, region["colors"][0]))
        for y in range(max(0, int(by0 // pixel_step)), min(out_height - 1, int(by1 // pixel_step)) + 1):
            for x in range(max(0, int(bx0 // pixel_step)), min(out_width - 1, int(bx1 // pixel_step)) + 1):
                point = (min(width - 0.5, (x + 0.5) * pixel_step), min(height - 0.5, (y + 0.5) * pixel_step))
                world = _treemap_point(point, scene)
                if world is not None and x0 <= world[0] < x1 and y0 <= world[1] < y1:
                    offset = (y * out_width + x) * 3
                    pixels[offset:offset + 3] = color
    scene["ppm"] = f"P6\n{out_width} {out_height}\n255\n".encode("ascii") + pixels
    return scene
