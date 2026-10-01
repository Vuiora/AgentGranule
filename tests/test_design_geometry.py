import copy
import math
import unittest

from agentgranule.core import GranuleError
from agentgranule.design_view import (AllocationModel, MAX_VOLUME, MIN_VOLUME,
    PLACEHOLDER_VOLUME, cube_vertices, effort_volume, module_positions,
    fitted_scene_camera, module_label_positions, parse_effort_text,
    pick_module, point_in_polygon, project_point)
from agentgranule.venn import region_positions, region_vertices


class GeometryTests(unittest.TestCase):
    def test_linear_volume_and_cube_root_dimensions_including_zero(self):
        for effort in (0, 0.01, 0.5, 1):
            volume = effort_volume(effort)
            self.assertAlmostEqual(volume, MIN_VOLUME + (MAX_VOLUME - MIN_VOLUME) * effort)
            vertices = cube_vertices((1, 2, 3), effort)
            edge = vertices[1][0] - vertices[0][0]
            self.assertAlmostEqual(edge ** 3, volume)
        self.assertGreater(effort_volume(0), 0)
        self.assertEqual(effort_volume(None), PLACEHOLDER_VOLUME)
        self.assertGreater(effort_volume(1), effort_volume(0.5))
        with self.assertRaises(GranuleError):
            effort_volume(0.001)

    def test_perspective_rotation_zoom_and_camera_boundary(self):
        point = (2, 1, 3)
        baseline = project_point(point, yaw=0, pitch=0, distance=10, focal=100, center=(50, 50))
        self.assertAlmostEqual(baseline[0], 50 + 200 / 13)
        self.assertAlmostEqual(baseline[1], 50 - 100 / 13)
        rotated = project_point(point, yaw=math.pi / 2, pitch=0, distance=10, focal=100, center=(50, 50))
        self.assertAlmostEqual(rotated[0], 50 + 300 / 8)
        self.assertEqual(rotated[2], 8)
        pitched = project_point(point, yaw=0, pitch=math.pi / 2, distance=10, focal=100, center=(50, 50))
        self.assertAlmostEqual(pitched[1], 50 + 300 / 11)
        enlarged = project_point(point, yaw=0, pitch=0, distance=10, focal=200, center=(50, 50))
        self.assertAlmostEqual(enlarged[0] - 50, 2 * (baseline[0] - 50))
        with self.assertRaises(GranuleError):
            project_point((0, 0, -10), yaw=0, pitch=0, distance=10)

    def test_deterministic_layout_separates_dependencies_from_parent(self):
        modules = [{"id": "a", "depends_on": []}, {"id": "b", "depends_on": ["a"]},
                   {"id": "c", "depends_on": [], "parent_id": "a"}]
        positions = module_positions(modules)
        self.assertEqual(positions, module_positions(list(reversed(modules))))
        self.assertEqual(positions["a"][0], positions["c"][0])
        self.assertGreater(positions["b"][0], positions["a"][0])
        self.assertNotEqual(positions["a"], positions["c"])
        with self.assertRaises(GranuleError):
            module_positions([{"id": "a", "depends_on": ["a"]}])

    def test_long_valid_dependency_graph_uses_iterative_layout(self):
        modules = [{"id": f"module-{i}", "depends_on": [f"module-{i - 1}"] if i else []}
                   for i in range(1500)]
        positions = module_positions(modules)
        self.assertEqual(len(positions), 1500)
        self.assertGreater(positions["module-1499"][0], positions["module-0"][0])

    def test_occlusion_pick_small_targets_and_polygon_edges(self):
        square = [(0, 0), (10, 0), (10, 10), (0, 10)]
        self.assertTrue(point_in_polygon((5, 5), square))
        self.assertTrue(point_in_polygon((0, 5), square))
        self.assertFalse(point_in_polygon((11, 5), square))
        faces = [{"module_id": "far", "points": square, "depth": 10},
                 {"module_id": "near", "points": square, "depth": 7}]
        self.assertEqual(pick_module((5, 5), faces), "near")
        self.assertEqual(pick_module((20, 20), [], [("tiny", 22, 22, 10)]), "tiny")
        self.assertIsNone(pick_module((100, 100), [], [("tiny", 22, 22, 10)]))

    def test_decimal_input_never_silently_rounds(self):
        for text, units in (("0", 0), ("0.01", 1), (".37", 37), ("0.50", 50), ("1.00", 100), ("0.0100", 1)):
            self.assertEqual(parse_effort_text(text), units)
        for invalid in ("", "nan", "inf", "0.001", "1.000000000000000000001", "-0.01", "1.01", "abc",
                        "1e-999999999", "0.37000000000000000000001"):
            with self.subTest(invalid=invalid), self.assertRaises(GranuleError):
                parse_effort_text(invalid)
        self.assertEqual(parse_effort_text("0e-999999999"), 0)

    def test_shared_diagram_fits_all_maximum_regions_across_camera_angles(self):
        for count in (12, 100):
            positions = region_positions([{"id": f"module-{i}"} for i in range(count)])
            for width, height in ((480, 210), (620, 370), (1000, 600)):
                for yaw, pitch in ((0, 0), (0.6, -0.35), (2, 1.2), (-2, -1.2)):
                    camera = fitted_scene_camera(positions, width, height, yaw=yaw, pitch=pitch)
                    for position in positions.values():
                        for vertex in region_vertices(position, 1):
                            x, y, _ = project_point(vertex, **camera)
                            self.assertTrue(0 <= x <= width and 0 <= y <= height,
                                            (count, width, height, yaw, pitch, x, y))

    def test_callouts_keep_all_modules_and_use_list_when_crowded(self):
        centers = [(f"module-{i}", 200, 50 + i * 5, 10) for i in range(8)]
        labels = module_label_positions(centers, 620, 420)
        self.assertEqual(set(labels), {p[0] for p in centers})
        self.assertTrue(all(label["expanded"] for label in labels.values()))
        rows = sorted(label["y"] for label in labels.values())
        self.assertGreaterEqual(min(b - a for a, b in zip(rows, rows[1:])), 38)
        crowded = module_label_positions(centers * 1 + [(f"extra-{i}", 200, 50, 10) for i in range(32)], 620, 370)
        self.assertEqual(len(crowded), 40)
        self.assertTrue(all(not label["expanded"] for label in crowded.values()))


class AllocationModelTests(unittest.TestCase):
    def setUp(self):
        self.graph = {"module_ids": {"a": "actual-a", "b": "actual-b"}, "analysis": {"modules": [
            {"id": "a", "directions": ["explanation", "analysis"]},
            {"id": "b", "directions": ["explanation"]}]}}
        self.snapshot = {"controls": [
            {"problem_id": "actual-a", "direction": "explanation", "parameters": {"design_effort": 0.37}, "source": "module"},
            {"problem_id": "actual-a", "direction": "analysis", "parameters": {"design_effort": 0.5}, "source": "project_default"},
            {"problem_id": "actual-b", "direction": "explanation", "parameters": {"detail_level": "standard"}, "source": "builtin_default"}]}

    def test_unset_and_defaults_require_explicit_pending_choice_each_direction(self):
        model = AllocationModel(self.graph, self.snapshot)
        self.assertEqual(model.effort("a", "explanation"), 0.37)
        self.assertIsNone(model.effort("a", "analysis"))
        self.assertIsNone(model.effort("b", "explanation"))
        self.assertIsNone(model.effort("b", "analysis"))
        with self.assertRaises(GranuleError):
            model.submitted_choices()
        model.set_units("a", "analysis", 50)
        model.set_units("b", "explanation", 0)
        self.assertEqual(model.submitted_choices(), [
            {"module_id": "actual-a", "direction": "explanation", "design_effort": 0.37},
            {"module_id": "actual-a", "direction": "analysis", "design_effort": 0.5},
            {"module_id": "actual-b", "direction": "explanation", "design_effort": 0}])

    def test_pending_choices_preserve_snapshot_and_validate_pairs(self):
        before = copy.deepcopy(self.snapshot)
        model = AllocationModel(self.graph, self.snapshot)
        model.set_units("a", "explanation", 99)
        self.assertEqual(model.effort("a", "explanation"), 0.99)
        self.assertEqual(self.snapshot, before)
        for direction, units in (("missing", 10), ("explanation", 0.1), ("explanation", True), ("explanation", 101)):
            with self.assertRaises(GranuleError):
                model.set_units("a", direction, units)


if __name__ == "__main__":
    unittest.main()
