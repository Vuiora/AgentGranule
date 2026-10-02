import copy
import math
import unittest

from agentgranule.core import GranuleError
from agentgranule.proportions import normalized_shares, treemap_rectangles
from agentgranule.venn import (
    COLUMN_FOOTPRINT_RATIO, _inside_polygon, _orthographic_project, _projected_depth_plane,
    _rotation, _treemap_hit_key, _treemap_point,
    hit_regions, render_treemap_scene,
)


def area(points):
    return abs(sum(a[0] * b[1] - a[1] * b[0]
                   for a, b in zip(points, points[1:] + points[:1]))) / 2


def raster_points(scene):
    step = scene["pixel_step"]
    data = scene["ppm"].split(b"\n", 3)[3]
    for y in range(scene["height"]):
        for x in range(scene["width"]):
            point = (min(scene["canvas_width"] - 0.5, (x + 0.5) * step),
                     min(scene["canvas_height"] - 0.5, (y + 0.5) * step))
            offset = (y * scene["width"] + x) * 3
            yield point, tuple(data[offset:offset + 3])


class HeightRendererTests(unittest.TestCase):
    def scene(self, efforts, camera=None, colors=None):
        allocation = normalized_shares(efforts)
        rectangles = treemap_rectangles(allocation["shares"], 10, 6)
        return render_treemap_scene(rectangles, camera or {"yaw": 0.6, "pitch": 0.6},
                                    240, 180, 2, colors, height_mode=True)

    def test_increasing_one_share_grows_area_and_world_height_without_refitting(self):
        efforts = {"a": 0.25, "b": 0.5, "c": 0.75}
        original = copy.deepcopy(efforts)
        old, new = self.scene(efforts), self.scene({**efforts, "a": 0.75})
        self.assertEqual(old["camera"], new["camera"])
        self.assertEqual(old["bounds"], new["bounds"])
        self.assertEqual(old["reserved_bounds"], new["reserved_bounds"])
        self.assertEqual(old["reserved_height"], 3.5)
        old_areas = {face["module_id"]: area(face["points"]) for face in old["faces"]}
        new_areas = {face["module_id"]: area(face["points"]) for face in new["faces"]}
        old_heights = {region["module_id"]: region["height"] for region in old["regions"]}
        for region in new["regions"]:
            key = region["module_id"]
            share = new_areas[key] / sum(new_areas.values())
            self.assertAlmostEqual(region["height"], 3.5 * share)
            self.assertAlmostEqual(max(p[2] for p in region["height_vertices"]) -
                                   min(p[2] for p in region["height_vertices"]), region["height"])
            if key == "a":
                self.assertGreater(region["height"], old_heights[key])
                self.assertGreater(new_areas[key], old_areas[key])
            else:
                self.assertLess(region["height"], old_heights[key])
                self.assertLess(new_areas[key], old_areas[key])
        self.assertEqual(efforts, original)
        self.assertAlmostEqual(sum(old_areas.values()), sum(new_areas.values()))

    def test_real_world_columns_remain_fixed_when_camera_turns_to_the_back(self):
        front = self.scene({"a": 0.2, "b": 0.8})
        rear = self.scene({"a": 0.2, "b": 0.8}, {"yaw": 2.3, "pitch": 0.6})
        self.assertEqual(front["height_vertices"], rear["height_vertices"])
        self.assertEqual(front["height_bounds"], rear["height_bounds"])
        self.assertEqual(front["reserved_bounds"], rear["reserved_bounds"])
        self.assertLess(front["height_base_z"], 0)
        self.assertGreater(len(front["height_faces"]), 0)
        # The opaque plate lies in front of the actual columns from the back.
        self.assertEqual(rear["height_faces"], [])
        for key, x, y, _ in rear["centers"]:
            self.assertEqual(hit_regions((x, y), rear), (key,))

    def test_height_surfaces_and_native_edges_stay_inside_their_own_allocation(self):
        for yaw, pitch in ((0, 0), (0.6, 0.6), (1.2, -0.7), (2.3, -1.1), (-0.8, 0.4)):
            with self.subTest(yaw=yaw, pitch=pitch):
                scene = self.scene({"a": 0.02, "b": 0.37, "c": 0.99}, {"yaw": yaw, "pitch": pitch})
                floors = {face["module_id"]: face["points"] for face in scene["faces"]}
                for face in scene["height_faces"]:
                    boundary = floors[face["module_id"]]
                    plane = _projected_depth_plane(boundary)
                    self.assertGreater(area(face["points"]), 0)
                    for point in face["points"]:
                        self.assertTrue(_inside_polygon(point, boundary))
                        self.assertLessEqual(point[2], plane[0] * point[0] + plane[1] * point[1] + plane[2] + 1e-8)
                for key, x, y, _ in scene["centers"]:
                    self.assertEqual(hit_regions((x, y), scene), (key,))

    def test_module_colours_never_repaint_a_neighbour_at_any_height(self):
        efforts = {"red": 0.97, "blue": 0.03}
        colors = {"red": (220, 30, 30), "blue": (30, 50, 220)}
        for camera in ({"yaw": 0.6, "pitch": 0.6}, {"yaw": -1.2, "pitch": 1.4},
                       {"yaw": 2.3, "pitch": -1.1}):
            with self.subTest(camera=camera):
                scene = self.scene(efforts, camera, colors)
                changed = self.scene(efforts, camera, {**colors, "red": (30, 220, 30)})
                other_pixels = 0
                for (point, color), (_, new_color) in zip(raster_points(scene), raster_points(changed)):
                    owner = _treemap_hit_key(_treemap_point(point, scene), scene)
                    if owner == "red":
                        self.assertGreater(color[0], color[2])
                    elif owner == "blue":
                        self.assertGreater(color[2], color[0])
                        self.assertEqual(color, new_color)
                        other_pixels += 1
                self.assertGreater(other_pixels, 0)

    def test_zero_share_has_no_column_and_does_not_replace_positive_area(self):
        scene = self.scene({"positive": 1, "zero": 0})
        without_zero = self.scene({"positive": 1})
        zero = next(region for region in scene["regions"] if region["module_id"] == "zero")
        self.assertEqual(zero["height"], 0)
        self.assertEqual(zero["height_vertices"], ())
        self.assertEqual(scene["height_vertices"]["zero"], ())
        self.assertFalse(any(face["module_id"] == "zero" for face in scene["height_faces"]))
        self.assertEqual(scene["ppm"], without_zero["ppm"])
        self.assertEqual(sum(area(face["points"]) for face in scene["faces"]),
                         sum(area(face["points"]) for face in without_zero["faces"]))

    def test_unassigned_and_all_zero_previews_follow_the_existing_display_shares(self):
        pending = self.scene({"assigned": 1.0, "pending": None})
        heights = {region["module_id"]: region["height"] for region in pending["regions"]}
        self.assertAlmostEqual(heights["assigned"], heights["pending"] * 2)
        all_zero = self.scene({"a": 0, "b": 0})
        self.assertEqual({region["height"] for region in all_zero["regions"]}, {1.75})

    def test_edge_on_geometry_stays_finite_without_crossing_collapsed_allocations(self):
        scene = self.scene({"a": 0.5, "b": 0.5}, {"yaw": math.pi / 2, "pitch": 0})
        self.assertEqual(scene["height_faces"], [])
        self.assertTrue(all(math.isfinite(v) for region in scene["regions"]
                            for point in region["height_vertices"] for v in point))
        self.assertTrue(all(math.isfinite(v) for face in scene["faces"] for p in face["points"] for v in p))
        self.assertEqual(len(hit_regions(scene["centers"][0][1:3], scene)), 1)
        self.assertEqual(len(scene["ppm"].split(b"\n", 3)[3]), scene["width"] * scene["height"] * 3)

    def test_mode_is_explicit_and_pure_with_an_empty_scene(self):
        rectangles, camera, colors = {"a": (0, 0, 10, 6)}, {"yaw": 0.6}, {"a": "#8877aa"}
        before = copy.deepcopy((rectangles, camera, colors))
        plate = render_treemap_scene(rectangles, camera, 240, 180, colors=colors)
        raised = render_treemap_scene(rectangles, camera, 240, 180, colors=colors, height_mode=True)
        self.assertEqual((rectangles, camera, colors), before)
        self.assertEqual(plate["height_faces"], [])
        self.assertEqual(plate["reserved_height"], 0)
        self.assertEqual(raised["bounds"], plate["bounds"])
        self.assertEqual(raised["camera"]["focal"], plate["camera"]["focal"])
        empty = render_treemap_scene({}, {}, 20, 10, height_mode=True)
        self.assertIsNone(empty["height_bounds"])
        self.assertIsNone(empty["reserved_bounds"])
        self.assertEqual(empty["height_vertices"], {})
        self.assertEqual(empty["height_faces"], [])
        with self.assertRaises(GranuleError):
            render_treemap_scene(rectangles, camera, 240, 180, height_mode=1)

    def test_default_ground_view_exposes_each_cuboid_top_and_two_full_sides(self):
        camera = {"yaw": 0.6, "pitch": 0.6, "roll": -0.97}
        scene = self.scene({str(i): 0.5 for i in range(8)}, camera)
        for region in scene["regions"]:
            key = region["module_id"]
            surfaces = [face for face in scene["height_faces"] if face["module_id"] == key]
            self.assertEqual(sorted(face["surface"] for face in surfaces), ["side", "side", "top"])
            self.assertTrue(all(len(face["points"]) == 4 for face in surfaces))
            x0, y0, x1, y1 = region["rectangle"]
            cx0, cy0, cx1, cy1 = region["column_rectangle"]
            self.assertAlmostEqual((cx1 - cx0) / (x1 - x0), COLUMN_FOOTPRINT_RATIO)
            self.assertAlmostEqual((cy1 - cy0) / (y1 - y0), COLUMN_FOOTPRINT_RATIO)
            top = next(face for face in surfaces if face["surface"] == "top")
            actual_top = [_orthographic_project(p, scene["camera"], _rotation(scene["camera"]))
                          for p in scene["height_vertices"][key][4:]]
            # Entire top remains visible at the default eight-module layout;
            # this catches the previous clipped stripes that looked planar.
            self.assertAlmostEqual(area(top["points"]), area(actual_top), places=9)
            floor = next(face for face in scene["faces"] if face["module_id"] == key)
            self.assertGreater(area(floor["points"]), area(top["points"]))
            self.assertEqual(hit_regions(top["points"][0][:2], scene), (key,))
        self.assertEqual(len({face["color"] for face in scene["height_faces"]}), 3)

    def test_roll_changes_ground_orientation_without_changing_geometry_or_share_picks(self):
        efforts = {"a": 0.25, "b": 0.5, "c": 0.75}
        unrolled = self.scene(efforts, {"yaw": 0.6, "pitch": 0.6, "roll": 0})
        for roll in (-0.97, 0.8, math.pi, -math.pi / 2):
            with self.subTest(roll=roll):
                rolled = self.scene(efforts, {"yaw": 0.6, "pitch": 0.6, "roll": roll})
                self.assertEqual(rolled["height_vertices"], unrolled["height_vertices"])
                self.assertEqual(rolled["reserved_bounds"], unrolled["reserved_bounds"])
                self.assertNotEqual(rolled["faces"][0]["points"], unrolled["faces"][0]["points"])
                areas = {face["module_id"]: area(face["points"]) for face in rolled["faces"]}
                for key, share in normalized_shares(efforts)["shares"].items():
                    self.assertAlmostEqual(areas[key] / sum(areas.values()), float(share), places=12)
                for key, x, y, _ in rolled["centers"]:
                    self.assertEqual(hit_regions((x, y), rolled), (key,))
        for roll in (True, "0.6", math.inf, math.nan):
            with self.subTest(roll=roll), self.assertRaises(GranuleError):
                self.scene(efforts, {"roll": roll})


if __name__ == "__main__":
    unittest.main()
