"""Exercise real native GL frames; REQUIRE_OPENGL makes missing support fail."""

import copy
import os
import sys
import unittest

from agentgranule.scene3d import point_in_polygon

REQUIRED = os.environ.get("AGENTGRANULE_REQUIRE_OPENGL") == "1"
try:
    from PySide6.QtCore import QEvent, QPoint, Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from agentgranule.gl_viewport import ModuleViewport, configure_default_format
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


MODULES = [{"id": key, "name": key.upper(), "directions": ["design"],
            "parent_id": None, "depends_on": []} for key in ("a", "b", "c")]


class NativeOpenGLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reason = None
        if not QT_AVAILABLE:
            reason = "Optional PySide6 design dependency is unavailable"
        elif sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or
                os.environ.get("WAYLAND_DISPLAY") or os.environ.get("QT_QPA_PLATFORM")):
            reason = "Native OpenGL requires a display (CI uses Xvfb/Mesa)"
        if reason:
            if REQUIRED:
                raise AssertionError(reason)
            raise unittest.SkipTest(reason)
        configure_default_format()
        cls.owns_app = QApplication.instance() is None
        cls.app = QApplication.instance() or QApplication([])
        probe = ModuleViewport()
        probe.resize(480, 360)
        probe.show()
        QTest.qWait(350)
        ready, error = probe.gl_ready, probe.gl_error
        probe.close()
        probe.deleteLater()
        cls.app.processEvents()
        if not ready or error:
            reason = "Native OpenGL context failed: " + str(error)
            if REQUIRED:
                raise AssertionError(reason)
            raise unittest.SkipTest(reason)

    @classmethod
    def tearDownClass(cls):
        cls.app.sendPostedEvents(None, QEvent.DeferredDelete)
        # On Windows/NVIDIA, starting and completing the main Qt event loop
        # releases native platform resources cleanly after QTest local loops.
        if cls.owns_app:
            QTimer.singleShot(0, cls.app.quit)
            cls.app.exec()

    def setUp(self):
        self.viewport = ModuleViewport()
        self.viewport.resize(720, 520)
        self.viewport.set_modules(MODULES, {"a": 1.0, "b": 0.5, "c": 0.5}, "design")
        self.viewport.show()
        QTest.qWait(100)
        self.assertTrue(self.viewport.gl_ready, self.viewport.gl_error)

    def tearDown(self):
        self.viewport.close()
        self.viewport.deleteLater()
        self.app.processEvents()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)

    def frame(self):
        self.app.processEvents()
        image = self.viewport.grabFramebuffer()
        self.assertIsNone(self.viewport.gl_error)
        return image

    def test_real_context_full_physical_pixels_resize_and_msaa(self):
        image = self.frame()
        ratio = self.viewport.devicePixelRatioF()
        self.assertEqual(image.size(), self.viewport.size() * ratio)
        report = self.viewport.diagnostics()
        self.assertEqual(report["backend"], "native-opengl")
        self.assertTrue(report["shader_linked"])
        self.assertGreaterEqual(tuple(report["context_version"]), (3, 0))
        self.assertGreaterEqual(report["depth_bits"], 16)
        self.assertGreaterEqual(report["stencil_bits"], 1)
        self.assertEqual(report["framebuffer_size"], [image.width(), image.height()])
        self.assertGreater(report["frame_count"], 0)
        self.assertIsInstance(report["samples"], int)
        # Both halves-round-up and halves-round-down cases occur at DPR 1.5;
        # the viewport must follow Qt's FBO sizing rather than Python round.
        for size in ((930, 610), (721, 521), (931, 611)):
            with self.subTest(logical_size=size, dpr=ratio):
                self.viewport.resize(*size)
                QTest.qWait(80)
                resized = self.frame()
                self.assertEqual(resized.size(), self.viewport.size() * ratio)
                self.assertEqual(self.viewport.diagnostics()["framebuffer_size"],
                                 [resized.width(), resized.height()])

    def test_rotation_uses_same_world_mesh_and_full_resolution(self):
        before_mesh = copy.deepcopy({key: tile["vertices"] for key, tile in self.viewport.scene["tiles"].items()})
        before = self.frame()
        camera = self.viewport.diagnostics()["camera"]
        QTest.mousePress(self.viewport, Qt.LeftButton, pos=QPoint(240, 230))
        QTest.mouseMove(self.viewport, QPoint(340, 280), delay=20)
        QTest.mouseRelease(self.viewport, Qt.LeftButton, pos=QPoint(340, 280))
        QTest.qWait(50)
        after = self.frame()
        self.assertNotEqual(camera, self.viewport.diagnostics()["camera"])
        self.assertEqual({key: tile["vertices"] for key, tile in self.viewport.scene["tiles"].items()}, before_mesh)
        self.assertEqual(before.size(), after.size())
        self.assertNotEqual(before, after)
        self.viewport.reset_camera()
        self.assertEqual(camera, self.viewport.diagnostics()["camera"])

    def test_gpu_color_change_cannot_cover_other_floor_cells(self):
        before = self.frame()
        polygons = copy.deepcopy(self.viewport._projected)
        ratio = self.viewport.devicePixelRatioF()
        # The full mesh reaches outside its own projected allocation, so this
        # verifies the actual stencil policy rather than an accidental layout.
        matrix = self.viewport._matrix()
        outside = [p for p in self.viewport.scene["tiles"]["a"]["vertices"]
                   if not point_in_polygon(self.viewport._project(p, matrix), polygons["a"])]
        self.assertTrue(outside)
        self.viewport.scene["tiles"]["a"]["color"] = (230, 30, 30)
        self.viewport.update()
        QTest.qWait(50)
        after = self.frame()
        changed_owner = checked_neighbors = 0
        for y in range(6, self.viewport.height() - 6, 8):
            for x in range(6, self.viewport.width() - 6, 8):
                # Stay away from sample coverage and antialiased shared edges.
                owner = next((key for key, polygon in polygons.items()
                              if all(point_in_polygon(p, polygon) for p in
                                     ((x - 3, y - 3), (x + 3, y - 3),
                                      (x - 3, y + 3), (x + 3, y + 3)))), None)
                if owner is None:
                    continue
                pixel = (round(x * ratio), round(y * ratio))
                if owner == "a":
                    changed_owner += before.pixel(*pixel) != after.pixel(*pixel)
                else:
                    checked_neighbors += 1
                    self.assertEqual(before.pixel(*pixel), after.pixel(*pixel), (owner, x, y))
        self.assertGreater(changed_owner, 50)
        self.assertGreater(checked_neighbors, 100)

    def test_zero_marker_selection_and_pending_raw_values(self):
        efforts = {"a": 1.0, "b": 0.0, "c": 0.5}
        self.viewport.set_modules(MODULES, efforts, "design")
        self.frame()
        self.assertEqual(self.viewport.scene["tiles"]["b"]["height"], 0)
        self.assertEqual(self.viewport.scene["tiles"]["b"]["mesh"].vertices, ())
        selected = []
        self.viewport.module_selected.connect(selected.append)
        x, y = self.viewport._zero_targets["b"]
        QTest.mouseClick(self.viewport, Qt.LeftButton, pos=QPoint(round(x), round(y)))
        self.assertEqual(selected, ["b"])
        self.assertEqual(efforts, {"a": 1.0, "b": 0.0, "c": 0.5})
        camera = self.viewport.diagnostics()["camera"]
        initial = self.viewport.scene["tiles"]["c"]["height"]
        self.viewport.set_modules(MODULES, {"a": 0.5, "b": 0.0, "c": 0.5}, "design")
        self.frame()
        self.assertGreater(self.viewport.scene["tiles"]["c"]["height"], initial)
        self.assertEqual(self.viewport.diagnostics()["camera"], camera)


if __name__ == "__main__":
    unittest.main()
