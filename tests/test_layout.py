import copy
import math
import unittest

from agentgranule.core import GranuleError
from agentgranule.layout import related_components, separated_scene_layout
from agentgranule.venn import _project, _rotation, region_vertices, render_scene, hit_regions


def disjoint(left, right):
    return (left[2] < right[0] or right[2] < left[0] or
            left[3] < right[1] or right[3] < left[1])


def projected_bounds(position, effort, camera):
    points = [_project(point, camera, _rotation(camera)) for point in region_vertices(position, effort)]
    return (min(point[0] for point in points), min(point[1] for point in points),
            max(point[0] for point in points), max(point[1] for point in points))


class SeparatedSceneLayoutTests(unittest.TestCase):
    def setUp(self):
        self.modules = [{"id": "a"}, {"id": "b", "parent_id": "a"},
                        {"id": "c", "depends_on": ["b"]}, {"id": "isolated"},
                        {"id": "x"}, {"id": "y", "depends_on": ["x"]},
                        {"id": "another"}]

    def assert_components_separated(self, layout):
        boxes = layout["component_bounds"]
        for index, left in enumerate(boxes):
            for right in boxes[index + 1:]:
                self.assertTrue(disjoint(left, right), (left, right, layout["camera"]))

    def test_only_explicit_parent_dependency_transitive_links_group_modules(self):
        before = copy.deepcopy(self.modules)
        self.assertEqual(related_components(self.modules),
                         (("a", "b", "c"), ("another",), ("isolated",), ("x", "y")))
        self.assertEqual(related_components(reversed(self.modules)), related_components(self.modules))
        self.assertEqual(self.modules, before)
        # A directed cycle is a backend validation concern, not a new layout edge.
        self.assertEqual(related_components([{"id": "a", "depends_on": ["b"]},
                                             {"id": "b", "depends_on": ["a"]}]),
                         (("a", "b"),))

    def test_maximum_regions_separate_through_rotation_zoom_and_resize(self):
        for yaw in (0, 0.6, math.pi / 2, math.pi, -math.pi / 2, 5.7):
            for pitch in (0, -0.35, math.pi / 2, -math.pi / 2, 2.8):
                for width, height, zoom in ((720, 520, 1), (90, 120, 0.25), (1600, 900, 3.5)):
                    with self.subTest(yaw=yaw, pitch=pitch, width=width, height=height, zoom=zoom):
                        layout = separated_scene_layout(self.modules, width, height,
                                                        yaw=yaw, pitch=pitch, zoom=zoom)
                        self.assert_components_separated(layout)
                        for position in layout["positions"].values():
                            points = [_project(point, layout["camera"], _rotation(layout["camera"]))
                                      for point in region_vertices(position, 1)]
                            self.assertTrue(all(point[2] > 0 for point in points))

    def test_effort_zero_to_one_and_direction_changes_never_move_reserved_layout(self):
        baseline = separated_scene_layout(self.modules, 720, 520, yaw=1.3, pitch=-0.8)
        membership = {key: index for index, group in enumerate(baseline["components"]) for key in group}
        for effort in (None, 0, 0.01, 0.37, 0.99, 1):
            changed = copy.deepcopy(self.modules)
            changed[0].update(effort=effort, directions=["other", "detail"])
            self.assertEqual(separated_scene_layout(changed, 720, 520, yaw=1.3, pitch=-0.8), baseline)
            boxes = {key: projected_bounds(position, effort, baseline["camera"])
                     for key, position in baseline["positions"].items()}
            for key, box in boxes.items():
                reserved = baseline["projected_bounds"][key]
                self.assertGreaterEqual(box[0] + 1e-9, reserved[0])
                self.assertGreaterEqual(box[1] + 1e-9, reserved[1])
                self.assertLessEqual(box[2] - 1e-9, reserved[2])
                self.assertLessEqual(box[3] - 1e-9, reserved[3])
                for other, other_box in boxes.items():
                    if membership[key] != membership[other]:
                        self.assertTrue(disjoint(box, other_box))

    def test_actual_shared_renderer_and_rays_cannot_hit_unrelated_regions(self):
        layout = separated_scene_layout(self.modules, 240, 180, yaw=1.1, pitch=0.6)
        membership = {key: index for index, group in enumerate(layout["components"]) for key in group}
        for effort in (0, 1):
            regions = [{"module_id": key, "center": position, "effort": effort}
                       for key, position in layout["positions"].items()]
            scene = render_scene(regions, layout["camera"], 240, 180, pixel_step=3)
            for region in scene["regions"]:
                self.assertEqual(region["bbox"], projected_bounds(region["center"], effort, layout["camera"]))
            for x in range(0, 240, 3):
                for y in range(0, 180, 3):
                    hits = hit_regions((x + 0.5, y + 0.5), scene, min_target=0)
                    self.assertLessEqual(len({membership[key] for key in hits}), 1)

    def test_stable_deterministic_empty_single_and_large_disconnected_inventory(self):
        before = copy.deepcopy(self.modules)
        layout = separated_scene_layout(self.modules, 720, 520)
        self.assertEqual(layout, separated_scene_layout(list(reversed(self.modules)), 720, 520))
        self.assertEqual(self.modules, before)
        empty = separated_scene_layout([], 720, 520)
        self.assertEqual(empty["positions"], {})
        self.assertEqual(empty["components"], ())
        self.assertEqual(empty["component_bounds"], ())
        single = separated_scene_layout([{"id": "only"}], 720, 520)
        self.assertEqual(single["positions"], {"only": (0, 0, 0)})
        many = separated_scene_layout([{"id": str(i)} for i in range(160)],
                                      1600, 900, yaw=math.pi / 2, pitch=math.pi / 2)
        self.assert_components_separated(many)

    def test_invalid_ids_links_and_camera_are_rejected_without_persistence(self):
        for modules in ([{"id": "a"}, {"id": "a"}], [{"id": ""}], [{"id": []}],
                        [{"id": "a", "parent_id": "missing"}],
                        [{"id": "a", "depends_on": ["missing"]}],
                        [{"id": "a", "depends_on": "a"}],
                        [{"id": "a", "depends_on": [[]]}]):
            with self.subTest(modules=modules), self.assertRaises(GranuleError):
                separated_scene_layout(modules, 720, 520)
        for arguments in ({"zoom": 0}, {"zoom": -1}, {"zoom": True}, {"zoom": math.inf},
                          {"yaw": math.nan}, {"pitch": "0"}):
            with self.subTest(arguments=arguments), self.assertRaises(GranuleError):
                separated_scene_layout(self.modules, 720, 520, **arguments)
        for width, height in ((0, 100), (100, -1), (True, 100), (1.5, 100)):
            with self.assertRaises(GranuleError):
                separated_scene_layout(self.modules, width, height)


if __name__ == "__main__":
    unittest.main()
