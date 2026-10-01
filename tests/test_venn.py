import copy
import math
import unittest

from agentgranule.core import GranuleError
from agentgranule.venn import (
    BACKGROUND, MAX_RENDER_HEIGHT, MAX_RENDER_WIDTH, MAX_VOLUME, MIN_VOLUME,
    PLACEHOLDER_VOLUME, REGION_THICKNESS, hit_regions, region_positions,
    region_vertices, region_volume, render_scene,
)


def pixel(scene, x, y):
    data = scene["ppm"].split(b"\n", 3)[3]
    offset = (y * scene["width"] + x) * 3
    return tuple(data[offset:offset + 3])


def region(key, center=(0, 0, 0), effort=0.37, color=(180, 60, 70)):
    return {"module_id": key, "center": center, "effort": effort, "color": color}


class RegionGeometryTests(unittest.TestCase):
    def test_fixed_thickness_and_linear_area_volume_without_spheres(self):
        for effort in (None, 0, 0.01, 0.37, 1):
            vertices = region_vertices((2, -1, 3), effort)
            self.assertEqual(len(vertices), 16)
            self.assertAlmostEqual(max(p[2] for p in vertices) - min(p[2] for p in vertices),
                                   REGION_THICKNESS)
            outline = vertices[:8]
            area = abs(sum(a[0] * b[1] - a[1] * b[0]
                           for a, b in zip(outline, outline[1:] + outline[:1]))) / 2
            self.assertAlmostEqual(area * REGION_THICKNESS, region_volume(effort))
            if effort is not None:
                self.assertAlmostEqual(region_volume(effort),
                                       MIN_VOLUME + (MAX_VOLUME - MIN_VOLUME) * effort)
            # Each flat side ends in two real chamfer corners rather than a round surface.
            self.assertEqual(len(set(point[2] for point in vertices)), 2)
        self.assertEqual(region_volume(None), PLACEHOLDER_VOLUME)
        self.assertGreater(region_volume(0), 0)
        for effort in (True, 0.375, float("nan"), -0.01, 1.01):
            with self.assertRaises(GranuleError):
                region_vertices((0, 0, 0), effort)

    def test_layout_one_shared_space_deterministic_and_not_dependency_semantics(self):
        modules = [{"id": "a"}, {"id": "b", "depends_on": ["a"]},
                   {"id": "c", "parent_id": "a"}]
        positions = region_positions(modules)
        self.assertEqual(positions, region_positions(list(reversed(modules))))
        self.assertEqual(positions, region_positions([{"id": m["id"]} for m in modules]))
        self.assertEqual(len(set(positions.values())), 3)
        for axis in range(3):
            self.assertAlmostEqual(sum(point[axis] for point in positions.values()), 0)
        self.assertEqual(region_positions([]), {})
        self.assertEqual(region_positions([{"id": "only"}]), {"only": (0, 0, 0)})
        self.assertEqual(len(region_positions([{"id": str(i)} for i in range(1500)])), 1500)
        with self.assertRaises(GranuleError):
            region_positions([{"id": "same"}, {"id": "same"}])

    def test_all_modules_share_one_camera_and_rotation_changes_actual_depth(self):
        regions = [region("a", (1, 1, 0)), region("b", (-1, -1, 0.2))]
        camera = {"yaw": 0, "pitch": 0, "distance": 10, "focal": 120}
        baseline = render_scene(regions, camera, 160, 120, 2)
        centers = {p[0]: p[1:] for p in baseline["centers"]}
        self.assertEqual(centers["a"], (92, 48, 10))
        yawed = render_scene(regions, {**camera, "yaw": math.pi / 2}, 160, 120, 2)
        self.assertNotEqual(baseline["centers"], yawed["centers"])
        self.assertNotEqual(baseline["faces"][0]["points"], yawed["faces"][0]["points"])
        self.assertAlmostEqual(yawed["centers"][0][3], 9)
        pitched = render_scene(regions, {**camera, "pitch": math.pi / 3}, 160, 120, 2)
        self.assertNotEqual(baseline["centers"], pitched["centers"])
        enlarged = render_scene(regions, {**camera, "focal": 240}, 160, 120, 2)
        self.assertEqual(enlarged["centers"][0][1] - 80, 2 * (centers["a"][0] - 80))
        self.assertEqual(len(baseline["faces"]), 20)

    def test_transparency_blends_back_module_in_same_pixel_and_retains_all_hits(self):
        camera = {"yaw": 0, "pitch": 0, "distance": 10, "focal": 120}
        red = region("far", (0, 0, 0.3), color=(220, 35, 35))
        blue = region("near", (0, 0, -0.3), color=(35, 70, 220))
        only_red = render_scene([red], camera, 160, 120, 1)
        only_blue = render_scene([blue], camera, 160, 120, 1)
        overlapping = render_scene([red, blue], camera, 160, 120, 1)
        mixed = pixel(overlapping, 80, 60)
        self.assertNotEqual(mixed, pixel(only_blue, 80, 60))
        self.assertNotEqual(mixed, pixel(only_red, 80, 60))
        self.assertGreater(mixed[0] / mixed[2],
                           pixel(only_blue, 80, 60)[0] / pixel(only_blue, 80, 60)[2])
        self.assertLess(mixed[0] / mixed[2],
                        pixel(only_red, 80, 60)[0] / pixel(only_red, 80, 60)[2])
        self.assertEqual(hit_regions((80, 60), overlapping), ("near", "far"))
        reversed_scene = render_scene([blue, red], camera, 160, 120, 1)
        self.assertEqual(hit_regions((80, 60), reversed_scene), ("near", "far"))
        self.assertEqual(overlapping["ppm"], reversed_scene["ppm"])
        self.assertEqual(hit_regions((159, 119), overlapping), ())

    def test_unassigned_and_zero_remain_visible_without_being_applied(self):
        camera = {"yaw": 0.35, "pitch": -0.2, "distance": 10, "focal": 120}
        for effort in (None, 0):
            scene = render_scene([region("unassigned", effort=effort)], camera, 160, 120, 1)
            self.assertNotEqual(pixel(scene, 80, 60), BACKGROUND)
            self.assertEqual(hit_regions((80, 60), scene), ("unassigned",))
        small = render_scene([region("small", effort=0)], camera, 160, 120, 1)
        large = render_scene([region("large", effort=1)], camera, 160, 120, 1)
        self.assertGreater(large["bounds"][1][0] - large["bounds"][0][0],
                           small["bounds"][1][0] - small["bounds"][0][0])

    def test_native_ppm_grid_is_bounded_and_pure(self):
        regions = [region("a", color="#8844aa")]
        camera = {"yaw": 0.2}
        before = copy.deepcopy((regions, camera))
        scene = render_scene(regions, camera, 1920, 1080, 3)
        self.assertLessEqual(scene["width"], MAX_RENDER_WIDTH)
        self.assertLessEqual(scene["height"], MAX_RENDER_HEIGHT)
        self.assertEqual(len(scene["ppm"].split(b"\n", 3)[3]),
                         scene["width"] * scene["height"] * 3)
        self.assertEqual(scene["canvas_width"], 1920)
        self.assertGreaterEqual(scene["pixel_step"], 3)
        self.assertEqual((regions, camera), before)
        empty = render_scene([], {}, 20, 10, 1)
        self.assertIsNone(empty["bounds"])
        self.assertEqual(empty["centers"], [])
        self.assertEqual(hit_regions((10, 5), empty), ())
        self.assertEqual(pixel(empty, 10, 5), BACKGROUND)

    def test_invalid_camera_and_inputs_do_not_hide_graph_failures(self):
        for camera in ({"distance": 0}, {"distance": float("inf")}, {"focal": -1},
                       {"yaw": float("nan")}, {"center": (0,)}, {"center": (0, float("inf"))},
                       {"yaw": 0, "pitch": 0, "distance": 0.01}):
            with self.subTest(camera=camera), self.assertRaises(GranuleError):
                render_scene([region("a")], camera, 160, 120)
        for width, height, step in ((0, 10, 1), (10, 0, 1), (True, 10, 1), (10, 10, 0), (10, 10, 17)):
            with self.assertRaises(GranuleError):
                render_scene([], {}, width, height, step)
        for regions in ([region("a"), region("a")], [region("a", color="#zzzzzz")],
                        [region("a", center=(0, 0, float("nan")))]):
            with self.assertRaises(GranuleError):
                render_scene(regions, {}, 160, 120)


if __name__ == "__main__":
    unittest.main()
