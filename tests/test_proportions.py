import copy
import math
import random
import unittest
from decimal import Decimal, localcontext
from fractions import Fraction

from agentgranule.core import GranuleError
from agentgranule.proportions import normalized_shares, treemap_rectangles


class DisplayShareTests(unittest.TestCase):
    def test_exact_shares_and_two_decimal_labels_total_one_hundred(self):
        result = normalized_shares({"c": 0.33, "b": 0.33, "a": 0.33})
        self.assertEqual(result["shares"], {key: Fraction(1, 3) for key in "abc"})
        self.assertEqual(result["percent_units"], {"a": 3334, "b": 3333, "c": 3333})
        self.assertEqual(sum(result["percent_units"].values()), 10_000)
        self.assertFalse(result["zero_total"])
        self.assertEqual(result["provisional"], ())
        with localcontext() as context:
            context.prec = 2
            self.assertEqual(normalized_shares({"a": 0.37, "b": 0.99})["shares"],
                             {"a": Fraction(37, 136), "b": Fraction(99, 136)})

    def test_raising_one_effort_changes_only_display_shares_of_other_modules(self):
        efforts = {"a": 0.25, "b": 0.5, "c": 0.75}
        original = copy.deepcopy(efforts)
        old = normalized_shares(efforts)
        new = normalized_shares({**efforts, "a": 0.5})
        self.assertGreater(new["shares"]["a"], old["shares"]["a"])
        for key in ("b", "c"):
            self.assertLess(new["shares"][key], old["shares"][key])
            self.assertEqual(new["weights"][key], old["weights"][key])
        self.assertEqual(efforts, original)

    def test_unassigned_preview_and_inapplicable_directions_are_explicit(self):
        result = normalized_shares({"assigned": 1.0, "pending": None, "other": None},
                                   applicable={"assigned", "pending"})
        self.assertEqual(result["weights"], {"assigned": 100, "other": 0, "pending": 50})
        self.assertEqual(result["shares"], {"assigned": Fraction(2, 3), "other": 0,
                                             "pending": Fraction(1, 3)})
        self.assertEqual(result["provisional"], ("pending",))
        self.assertEqual(result["applicable"], ("assigned", "pending"))
        self.assertEqual(result["percent_units"]["other"], 0)

    def test_all_zero_preview_is_equal_and_marked_without_inventing_effort(self):
        result = normalized_shares({"a": 0, "b": 0, "not_applicable": 1}, {"a", "b"})
        self.assertTrue(result["zero_total"])
        self.assertEqual(result["weights"], {key: 0 for key in result["shares"]})
        self.assertEqual(result["shares"], {"a": Fraction(1, 2), "b": Fraction(1, 2),
                                             "not_applicable": 0})
        self.assertEqual(result["provisional"], ())
        for empty in (normalized_shares({}), normalized_shares({"a": None}, [])):
            self.assertFalse(empty["zero_total"])
            self.assertEqual(sum(empty["shares"].values()), 0)
            self.assertEqual(sum(empty["percent_units"].values()), 0)

    def test_many_ties_use_stable_ids_and_labels_always_sum_one_hundred(self):
        efforts = {f"m{index:04d}": 0.01 for index in range(1001)}
        first = normalized_shares(efforts)
        second = normalized_shares(dict(reversed(list(efforts.items()))))
        self.assertEqual(first, second)
        self.assertEqual(sum(first["shares"].values()), 1)
        self.assertEqual(sum(first["percent_units"].values()), 10_000)
        self.assertEqual(first["percent_units"]["m0000"], 10)
        self.assertEqual(first["percent_units"]["m1000"], 9)

    def test_invalid_grid_and_module_inputs_are_rejected(self):
        for value in (True, "0.5", Decimal("0.5"), -0.01, 1.01, 0.001,
                      float("inf"), float("nan")):
            with self.subTest(value=value), self.assertRaises(GranuleError):
                normalized_shares({"a": value})
        for efforts, applicable in (([], None), ({"": 0.5}, None),
                                    ({"a": 0.5}, {"missing"}), ({"a": 0.5}, "a")):
            with self.subTest(efforts=efforts, applicable=applicable), self.assertRaises(GranuleError):
                normalized_shares(efforts, applicable)


class CompactPartitionTests(unittest.TestCase):
    def assert_partition(self, shares, width, height):
        cells = treemap_rectangles(shares, width, height)
        area = 0.0
        for key, (x0, y0, x1, y1) in cells.items():
            self.assertTrue(0 <= x0 <= x1 <= width)
            self.assertTrue(0 <= y0 <= y1 <= height)
            size = (x1 - x0) * (y1 - y0)
            self.assertAlmostEqual(size / (width * height), float(shares[key]), places=12)
            area += size
            if shares[key] > 0:
                self.assertGreater(x1, x0)
                self.assertGreater(y1, y0)
        self.assertAlmostEqual(area, width * height, places=7)
        for index, (left_key, left) in enumerate(cells.items()):
            for right_key, right in list(cells.items())[index + 1:]:
                overlap_x = max(0, min(left[2], right[2]) - max(left[0], right[0]))
                overlap_y = max(0, min(left[3], right[3]) - max(left[1], right[1]))
                self.assertEqual(overlap_x * overlap_y, 0, (left_key, right_key))
        return cells

    def test_partition_fills_frame_proportionally_without_overlap(self):
        randomizer = random.Random(7)
        for count in (1, 2, 3, 8, 13, 61):
            efforts = {f"m{index:03d}": randomizer.randrange(101) / 100 for index in range(count)}
            shares = normalized_shares(efforts)["shares"]
            for width, height in ((720, 480), (80, 600), (1400.5, 90.25)):
                with self.subTest(count=count, width=width, height=height):
                    cells = self.assert_partition(shares, width, height)
                    self.assertEqual(cells, treemap_rectangles(dict(reversed(list(shares.items()))),
                                                              width, height))

    def test_zero_area_modules_have_stable_distinct_boundary_anchors(self):
        shares = {"big": Fraction(1), "zero-a": Fraction(0), "zero-b": Fraction(0)}
        cells = self.assert_partition(shares, 600, 400)
        self.assertEqual(cells["zero-a"], (150, 400, 150, 400))
        self.assertEqual(cells["zero-b"], (450, 400, 450, 400))
        self.assertEqual(treemap_rectangles({"a": 0}, 20, 30), {"a": (10, 30, 10, 30)})
        self.assertEqual(treemap_rectangles({}, 20, 30), {})

    def test_increase_expands_one_area_and_reduces_each_other_area(self):
        old = normalized_shares({"a": 0.25, "b": 0.5, "c": 0.5})["shares"]
        new = normalized_shares({"a": 0.75, "b": 0.5, "c": 0.5})["shares"]
        old_cells, new_cells = self.assert_partition(old, 600, 400), self.assert_partition(new, 600, 400)
        areas = lambda cells: {key: (cell[2] - cell[0]) * (cell[3] - cell[1])
                               for key, cell in cells.items()}
        old_area, new_area = areas(old_cells), areas(new_cells)
        self.assertGreater(new_area["a"], old_area["a"])
        self.assertLess(new_area["b"], old_area["b"])
        self.assertLess(new_area["c"], old_area["c"])

    def test_equal_weights_form_compact_cells_in_shared_frame(self):
        shares = normalized_shares({str(index): 0.5 for index in range(8)})["shares"]
        cells = self.assert_partition(shares, 800, 400)
        for x0, y0, x1, y1 in cells.values():
            self.assertLessEqual(max(x1 - x0, y1 - y0) / min(x1 - x0, y1 - y0), 2)

    def test_share_and_geometry_validation_rejects_invalid_values(self):
        for shares in ({"a": True}, {"a": float("inf")}, {"a": Decimal("NaN")},
                       {"a": "1"}, {"a": -1}, {"a": 1.1}, {"a": Fraction(1, 3)}, {"": 1}):
            with self.subTest(shares=shares), self.assertRaises(GranuleError):
                treemap_rectangles(shares, 40, 30)
        for width, height in ((0, 20), (20, -1), (math.inf, 20), (20, math.nan), (True, 20)):
            with self.subTest(width=width, height=height), self.assertRaises(GranuleError):
                treemap_rectangles({"a": 1}, width, height)


if __name__ == "__main__":
    unittest.main()
