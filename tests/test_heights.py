import copy
import math
import unittest
from decimal import Decimal
from fractions import Fraction

from agentgranule.core import GranuleError
from agentgranule.heights import box_vertices, maximum_height, proportional_heights
from agentgranule.proportions import normalized_shares, treemap_rectangles


class ProportionalHeightTests(unittest.TestCase):
    def test_increasing_one_share_grows_its_height_and_reduces_other_heights(self):
        efforts = {"a": 0.25, "b": 0.5, "c": 0.75}
        original = copy.deepcopy(efforts)
        before = normalized_shares(efforts)
        after = normalized_shares({**efforts, "a": 0.75})
        previous = proportional_heights(before["shares"], 10)
        updated = proportional_heights(after["shares"], 10)
        self.assertGreater(updated["a"], previous["a"])
        for key in ("b", "c"):
            self.assertLess(updated[key], previous[key])
            self.assertEqual(after["weights"][key], before["weights"][key])
        self.assertEqual(efforts, original)
        self.assertEqual(sum(after["percent_units"].values()), 10_000)

    def test_height_is_linear_and_camera_envelope_is_fixed(self):
        shares = {"a": Fraction(1, 6), "b": Fraction(2, 6), "c": Fraction(3, 6)}
        heights = proportional_heights(shares, 10)
        self.assertAlmostEqual(heights["b"] - heights["a"], heights["c"] - heights["b"])
        self.assertAlmostEqual(proportional_heights({"full": 1}, 10)["full"], 3.5)
        for key, share in shares.items():
            self.assertAlmostEqual(heights[key] / maximum_height(10), float(share))
        self.assertEqual(maximum_height(10), maximum_height(10.0))
        self.assertGreaterEqual(maximum_height(10), max(heights.values()))
        self.assertEqual(heights, proportional_heights(dict(reversed(list(shares.items()))), 10))

    def test_zero_and_provisional_shares_follow_existing_area_semantics(self):
        preview = normalized_shares({"assigned": 1.0, "pending": None, "zero": 0})
        heights = proportional_heights(preview["shares"], 12)
        self.assertGreater(heights["assigned"], heights["pending"])
        self.assertGreater(heights["pending"], 0)
        self.assertEqual(heights["zero"], 0)
        self.assertEqual(preview["provisional"], ("pending",))
        equal = normalized_shares({"a": 0, "b": 0})
        self.assertTrue(equal["zero_total"])
        self.assertEqual(len(set(proportional_heights(equal["shares"], 12).values())), 1)
        self.assertEqual(proportional_heights({"a": 0}, 12), {"a": 0})
        self.assertEqual(proportional_heights({}, 12), {})

    def test_invalid_shares_and_spans_are_rejected(self):
        for shares in ([], {"": 1}, {"a": True}, {"a": None}, {"a": "1"},
                       {"a": -1}, {"a": 1.1}, {"a": Fraction(1, 3)},
                       {"a": math.inf}, {"a": Decimal("NaN")}):
            with self.subTest(shares=shares), self.assertRaises(GranuleError):
                proportional_heights(shares, 10)
        for span in (0, -1, True, "10", math.inf, math.nan):
            with self.subTest(span=span), self.assertRaises(GranuleError):
                proportional_heights({"a": 1}, span)
            with self.subTest(span=span), self.assertRaises(GranuleError):
                maximum_height(span)


class ExtrusionGeometryTests(unittest.TestCase):
    def test_vertices_preserve_partitioned_footprints_and_true_z_height(self):
        shares = normalized_shares({"a": 0.7, "b": 0.3})["shares"]
        rectangles = treemap_rectangles(shares, 8, 4)
        heights = proportional_heights(shares, 8)
        for key, rectangle in rectangles.items():
            vertices = box_vertices(rectangle, heights[key], base_z=0.25)
            self.assertEqual(len(vertices), 8)
            self.assertEqual({vertex[2] for vertex in vertices}, {0.25, 0.25 + heights[key]})
            self.assertEqual(vertices[:4], tuple((x, y, 0.25) for x, y in
                                                ((rectangle[0], rectangle[1]),
                                                 (rectangle[2], rectangle[1]),
                                                 (rectangle[2], rectangle[3]),
                                                 (rectangle[0], rectangle[3]))))
            footprint = ((max(point[0] for point in vertices) - min(point[0] for point in vertices)) *
                         (max(point[1] for point in vertices) - min(point[1] for point in vertices)))
            self.assertAlmostEqual(footprint / 32, float(shares[key]))

    def test_zero_area_or_height_has_no_visible_box(self):
        for rectangle, height in (((2, 3, 2, 3), 0), ((2, 3, 2, 5), 1),
                                   ((2, 3, 4, 3), 1), ((2, 3, 4, 5), 0)):
            self.assertEqual(box_vertices(rectangle, height), ())

    def test_invalid_geometry_is_rejected(self):
        for rectangle in (None, "1234", (0, 1, 2), (1, 0, 0, 1), (0, 2, 1, 1),
                          (0, 0, math.inf, 1), (0, 0, True, 1)):
            with self.subTest(rectangle=rectangle), self.assertRaises(GranuleError):
                box_vertices(rectangle, 1)
        for height in (-1, True, math.inf, math.nan, "1"):
            with self.subTest(height=height), self.assertRaises(GranuleError):
                box_vertices((0, 0, 1, 1), height)
        with self.assertRaises(GranuleError):
            box_vertices((0, 0, 1, 1), 1e308, base_z=1e308)


if __name__ == "__main__":
    unittest.main()
