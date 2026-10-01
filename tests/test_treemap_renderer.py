import copy
import math
import unittest

from agentgranule.core import GranuleError
from agentgranule.venn import (
    BACKGROUND, MAX_RENDER_HEIGHT, MAX_RENDER_WIDTH, hit_regions,
    render_treemap_scene,
)


def area(points):
    return abs(sum(a[0] * b[1] - a[1] * b[0]
                   for a, b in zip(points, points[1:] + points[:1]))) / 2


def pixel(scene, x, y):
    data = scene["ppm"].split(b"\n", 3)[3]
    offset = (y * scene["width"] + x) * 3
    return tuple(data[offset:offset + 3])


class TreemapRendererTests(unittest.TestCase):
    def test_common_real_3d_plane_preserves_projected_area_ratios(self):
        rectangles = {"a": (0, 0, 2, 6), "b": (2, 0, 5, 6), "c": (5, 0, 10, 6)}
        for yaw, pitch in ((0, 0), (0.6, -0.35), (1.2, 0.7), (2.3, -1.1), (-0.8, 0.4)):
            with self.subTest(yaw=yaw, pitch=pitch):
                scene = render_treemap_scene(rectangles, {"yaw": yaw, "pitch": pitch}, 300, 220)
                areas = {face["module_id"]: area(face["points"]) for face in scene["faces"]}
                total = sum(areas.values())
                self.assertGreater(total, 0)
                for key, expected in (("a", 0.2), ("b", 0.3), ("c", 0.5)):
                    self.assertAlmostEqual(areas[key] / total, expected, places=12)
                    center = next(p for p in scene["centers"] if p[0] == key)
                    self.assertEqual(hit_regions(center[1:3], scene), (key,))
                self.assertEqual(scene["projection"], "orthographic")
                self.assertEqual(scene["camera"]["projection"], "orthographic")

    def test_raising_one_share_expands_it_and_shrinks_others_without_refitting(self):
        old = render_treemap_scene({"a": (0, 0, 2.5, 6), "b": (2.5, 0, 10, 6)}, {}, 300, 220)
        new = render_treemap_scene({"a": (0, 0, 7.5, 6), "b": (7.5, 0, 10, 6)}, {}, 300, 220)
        self.assertEqual(old["bounds"], new["bounds"])
        self.assertEqual(old["camera"], new["camera"])
        areas_old = {f["module_id"]: area(f["points"]) for f in old["faces"]}
        areas_new = {f["module_id"]: area(f["points"]) for f in new["faces"]}
        self.assertAlmostEqual(areas_new["a"], 3 * areas_old["a"])
        self.assertAlmostEqual(areas_new["b"], areas_old["b"] / 3)
        self.assertAlmostEqual(sum(areas_new.values()), sum(areas_old.values()))

    def test_adjacent_faces_are_unique_picks_and_colours_do_not_mix(self):
        rectangles = {"red": (0, 0, 5, 6), "blue": (5, 0, 10, 6)}
        colors = {"red": (220, 35, 35), "blue": (35, 70, 220)}
        camera = {"yaw": 0, "pitch": 0, "focal": 20}
        scene = render_treemap_scene(rectangles, camera, 300, 220, 1, colors)
        for key, x, y, _ in scene["centers"]:
            self.assertEqual(hit_regions((x, y), scene), (key,))
            expected = tuple(round(old * 0.22 + channel * 0.78)
                             for old, channel in zip(BACKGROUND, colors[key]))
            self.assertEqual(pixel(scene, int(x), int(y)), expected)
        # The shared boundary has one deterministic owner, independent of order.
        self.assertEqual(hit_regions((150, 110), scene), ("blue",))
        reversed_scene = render_treemap_scene(dict(reversed(list(rectangles.items()))), camera,
                                            300, 220, 1, colors)
        self.assertEqual(scene["ppm"], reversed_scene["ppm"])
        self.assertEqual(hit_regions((150, 110), reversed_scene), ("blue",))

    def test_zero_share_has_no_face_and_does_not_steal_a_positive_face(self):
        rectangles = {"positive": (0, 0, 10, 6), "zero": (5, 0, 5, 0)}
        scene = render_treemap_scene(rectangles, {"yaw": 0, "pitch": 0}, 300, 220)
        self.assertEqual([f["module_id"] for f in scene["faces"]], ["positive"])
        self.assertEqual({c[0] for c in scene["centers"]}, {"positive", "zero"})
        zero_center = next(c for c in scene["centers"] if c[0] == "zero")
        self.assertEqual(hit_regions(zero_center[1:3], scene), ("positive",))
        alone = render_treemap_scene({"positive": (0, 0, 10, 6)}, {"yaw": 0, "pitch": 0}, 300, 220)
        self.assertEqual(scene["ppm"], alone["ppm"])

    def test_rotation_changes_depth_and_zoom_scales_the_common_board(self):
        rectangles = {"a": (0, 0, 5, 6), "b": (5, 0, 10, 6)}
        front = render_treemap_scene(rectangles, {"yaw": 0, "pitch": 0}, 300, 220)
        rotated = render_treemap_scene(rectangles, {"yaw": 0.6, "pitch": -0.35}, 300, 220)
        self.assertNotEqual(front["centers"], rotated["centers"])
        self.assertNotEqual(front["faces"][0]["points"], rotated["faces"][0]["points"])
        self.assertGreater(len(rotated["base_faces"]), 0)
        self.assertAlmostEqual(rotated["bounds"][1][2] - rotated["bounds"][0][2], 0.35)
        zoomed = render_treemap_scene(rectangles, {"yaw": 0.6, "pitch": -0.35, "zoom": 1.7}, 300, 220)
        self.assertAlmostEqual(zoomed["camera"]["focal"], rotated["camera"]["focal"] * 1.7)
        self.assertAlmostEqual(sum(area(f["points"]) for f in zoomed["faces"]),
                               sum(area(f["points"]) for f in rotated["faces"]) * 1.7 ** 2)

    def test_edge_on_camera_has_finite_image_and_center_pick(self):
        scene = render_treemap_scene({"a": (0, 0, 5, 6), "b": (5, 0, 10, 6)},
                                     {"yaw": math.pi / 2, "pitch": 0}, 300, 220)
        self.assertTrue(all(math.isfinite(v) for f in scene["faces"] for p in f["points"] for v in p))
        center = scene["centers"][0]
        self.assertEqual(len(hit_regions(center[1:3], scene)), 1)
        self.assertEqual(len(scene["ppm"].split(b"\n", 3)[3]), scene["width"] * scene["height"] * 3)

    def test_native_raster_cap_and_input_purity_and_empty_scene(self):
        rectangles = {"a": (0, 0, 10, 6)}
        camera = {"yaw": 0.2, "zoom": 1.2}
        colors = {"a": "#8844aa"}
        before = copy.deepcopy((rectangles, camera, colors))
        scene = render_treemap_scene(rectangles, camera, 1920, 1080, 3, colors)
        self.assertLessEqual(scene["width"], MAX_RENDER_WIDTH)
        self.assertLessEqual(scene["height"], MAX_RENDER_HEIGHT)
        self.assertEqual((rectangles, camera, colors), before)
        empty = render_treemap_scene({}, {}, 20, 10, 1)
        self.assertIsNone(empty["bounds"])
        self.assertEqual(empty["centers"], [])
        self.assertEqual(hit_regions((10, 5), empty), ())
        self.assertEqual(pixel(empty, 10, 5), BACKGROUND)

    def test_invalid_rectangles_camera_colours_and_dimensions_fail_explicitly(self):
        invalid_rectangles = ([], {1: (0, 0, 1, 1)}, {"": (0, 0, 1, 1)},
                              {"a": (0, 0, 1)}, {"a": (1, 0, 0, 1)},
                              {"a": (0, 0, float("nan"), 1)},
                              {"a": (-1e308, 0, 1e308, 1)},
                              {"a": (0, 0, 2, 2), "b": (1, 1, 3, 3)})
        for rectangles in invalid_rectangles:
            with self.subTest(rectangles=rectangles), self.assertRaises(GranuleError):
                render_treemap_scene(rectangles, {}, 300, 220)
        for camera in ({"zoom": 0}, {"zoom": float("nan")}, {"margin_x": -1},
                       {"yaw": float("inf")}, {"focal": -1}):
            with self.subTest(camera=camera), self.assertRaises(GranuleError):
                render_treemap_scene({"a": (0, 0, 1, 1)}, camera, 300, 220)
        with self.assertRaises(GranuleError):
            render_treemap_scene({"a": (0, 0, 1, 1)}, {}, 300, 220, colors={"a": "#zzzzzz"})
        for width, height, step in ((0, 10, 1), (10, 0, 1), (True, 10, 1), (10, 10, 0), (10, 10, 17)):
            with self.assertRaises(GranuleError):
                render_treemap_scene({}, {}, width, height, step)


if __name__ == "__main__":
    unittest.main()
