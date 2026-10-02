import copy
import math
import unittest
from fractions import Fraction

from agentgranule.core import GranuleError
from agentgranule.scene3d import COLUMN_INSET, build_scene, cuboid_mesh, point_in_polygon


def modules():
    return [{"id": key, "name": key.upper(), "directions": ["design"],
             "parent_id": None, "depends_on": []} for key in ("a", "b", "c")]


class NativeSceneTests(unittest.TestCase):
    def test_exact_partition_closed_mesh_normals_and_inset(self):
        scene = build_scene(modules(), {"a": 1.0, "b": 0.5, "c": 0.5}, "design")
        self.assertEqual(scene["allocation"]["shares"],
                         {"a": Fraction(1, 2), "b": Fraction(1, 4), "c": Fraction(1, 4)})
        areas = {}
        for key, tile in scene["tiles"].items():
            x0, y0, x1, y1 = tile["rectangle"]
            areas[key] = (x1 - x0) * (y1 - y0)
            self.assertEqual(len(tile["vertices"]), 8)
            self.assertEqual(len(tile["mesh"].vertices), 36)
            self.assertEqual(len(tile["mesh"].edges), 24)
            width = tile["vertices"][1][0] - tile["vertices"][0][0]
            self.assertAlmostEqual(width, (x1 - x0) * COLUMN_INSET)
            self.assertAlmostEqual(tile["height"], 3.5 * float(scene["allocation"]["shares"][key]))
            self.assertTrue(all(p[2] == 0 for p in tile["vertices"][:4]))
            self.assertTrue(all(p[2] == tile["height"] for p in tile["vertices"][4:]))
            triangles = tile["mesh"].vertices
            for index in range(0, len(triangles), 3):
                a, b, c = triangles[index:index + 3]
                ab, ac = [b[i] - a[i] for i in range(3)], [c[i] - a[i] for i in range(3)]
                cross = (ab[1] * ac[2] - ab[2] * ac[1], ab[2] * ac[0] - ab[0] * ac[2],
                         ab[0] * ac[1] - ab[1] * ac[0])
                self.assertGreater(sum(cross[i] * a[i + 3] for i in range(3)), 0)
                self.assertEqual(a[3:], b[3:])
                self.assertEqual(a[3:], c[3:])
                self.assertEqual(sum(v * v for v in a[3:]), 1)
        self.assertAlmostEqual(sum(areas.values()), 70)
        self.assertEqual(areas["a"], areas["b"] * 2)

    def test_larger_share_changes_height_without_rewriting_other_raw_efforts(self):
        original = {"a": 0.25, "b": 0.5, "c": 0.5}
        before = build_scene(modules(), original, "design")
        after = build_scene(modules(), {**original, "a": 1.0}, "design")
        self.assertGreater(after["tiles"]["a"]["height"], before["tiles"]["a"]["height"])
        self.assertLess(after["tiles"]["b"]["height"], before["tiles"]["b"]["height"])
        self.assertEqual(after["maximum_height"], before["maximum_height"])
        self.assertEqual(original, {"a": 0.25, "b": 0.5, "c": 0.5})
        self.assertEqual(after["raw_efforts"]["b"], before["raw_efforts"]["b"])

    def test_zero_and_not_applicable_have_no_mesh_but_keep_distinct_targets(self):
        inputs = modules()
        inputs[2]["directions"] = ["other"]
        scene = build_scene(inputs, {"a": 1.0, "b": 0.0, "c": 0.5}, "design")
        for key in ("b", "c"):
            self.assertEqual(scene["tiles"][key]["height"], 0)
            self.assertEqual(scene["tiles"][key]["vertices"], ())
            self.assertEqual(scene["tiles"][key]["mesh"].vertices, ())
        self.assertNotEqual(scene["tiles"]["b"]["center"], scene["tiles"]["c"]["center"])
        self.assertEqual(scene["raw_efforts"]["c"], 0.5)

    def test_unassigned_and_all_zero_are_flagged_preview_only(self):
        none = build_scene(modules(), {"a": None, "b": None, "c": None}, "design")
        self.assertEqual(none["allocation"]["provisional"], ("a", "b", "c"))
        zeros = build_scene(modules(), {"a": 0, "b": 0, "c": 0}, "design")
        self.assertTrue(zeros["allocation"]["zero_total"])
        self.assertEqual(zeros["raw_efforts"], {"a": 0, "b": 0, "c": 0})
        self.assertEqual(sum(zeros["allocation"]["percent_units"].values()), 10000)

    def test_empty_and_inputs_are_independent(self):
        inputs, efforts = modules(), {"a": None, "b": 0.37, "c": 1}
        original = copy.deepcopy((inputs, efforts))
        scene = build_scene(inputs, efforts, "design")
        scene["tiles"]["a"]["module"]["name"] = "preview"
        self.assertEqual((inputs, efforts), original)
        empty = build_scene([], {}, "design")
        self.assertEqual(empty["tiles"], {})
        self.assertEqual(sum(empty["allocation"]["percent_units"].values()), 0)

    def test_validation_and_degenerate_picking(self):
        for invalid in ([{"id": "a"}, {"id": "a"}], [{"id": ""}], [None]):
            with self.assertRaises(GranuleError):
                build_scene(invalid, {}, "design")
        with self.assertRaises(GranuleError):
            build_scene(modules(), {"a": 0.001}, "design")
        with self.assertRaises(GranuleError):
            cuboid_mesh([(math.nan, 0, 0)] * 8)
        square = [(0, 0), (1, 0), (1, 1), (0, 1)]
        self.assertTrue(point_in_polygon((0.5, 0.5), square))
        self.assertTrue(point_in_polygon((0, 0.5), square))
        self.assertFalse(point_in_polygon((1.01, 0.5), square))
        self.assertFalse(point_in_polygon((0, 0), [(0, 0)] * 4))


if __name__ == "__main__":
    unittest.main()
