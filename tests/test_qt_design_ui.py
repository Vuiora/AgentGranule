"""Actual native Qt controls and framebuffers using isolated fake services.

No test connects to the real project database or makes a real human decision.
AGENTGRANULE_REQUIRE_OPENGL=1 makes missing Qt/GL fail rather than skip.
"""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from agentgranule.core import GranuleError

try:
    from PySide6.QtCore import QEvent, QPoint, QTimer, Qt, Signal
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QPushButton, QWidget
    from agentgranule.gl_viewport import configure_default_format
    from agentgranule.qt_design_view import AllocationReview, DesignWindow, ModuleEditor, show_design_qt
except ImportError as exc:
    QT_IMPORT_ERROR = str(exc)
else:
    QT_IMPORT_ERROR = None


class IsolatedService:
    """Synthetic graph, settings and save responses; no persistent database."""

    def __init__(self, approved=False):
        modules = [{"id": key, "name": f"隔离模块 {key}", "description": f"职责 {key}",
                    "expected_output": f"输出 {key}", "basis": "仅用于原生界面隔离测试",
                    "directions": ["analysis", "explanation"], "parent_id": None,
                    "depends_on": []} for key in ("a", "b", "c")]
        self.graph = {"analysis_id": "isolated-qt-ui", "revision": 2 if approved else 1,
                      "state": "approved" if approved else "proposed",
                      "analysis": {"summary": "隔离界面测试，非真实人工确认。", "modules": modules,
                                   "context": {"isolated": True}},
                      "module_ids": {key: f"test-{key}" for key in ("a", "b", "c")} if approved else {},
                      "workflow_id": None}
        self.approvals, self.saves, self.updates = [], [], []
        self.snapshot = None
        self.fail_get = self.fail_snapshot = self.fail_save = False

    def get_analysis(self, analysis_id):
        if self.fail_get or analysis_id != self.graph["analysis_id"]:
            raise GranuleError("Unknown analysis")
        return copy.deepcopy(self.graph)

    def allocation_snapshot(self, analysis_id):
        if self.fail_snapshot:
            raise GranuleError("Allocation snapshot failed")
        self.snapshot = {"analysis_id": analysis_id, "revision": self.graph["revision"],
                         "workflow_id": None,
                         "controls": [{"module_id": self.graph["module_ids"][module["id"]],
                                       "direction": direction, "parameters": {"detail_level": "standard", "count": 3},
                                       "source": "builtin_default", "revision": 0}
                                      for module in self.graph["analysis"]["modules"] for direction in module["directions"]]}
        return copy.deepcopy(self.snapshot)

    def update_analysis(self, analysis_id, revision, analysis):
        assert revision == self.graph["revision"]
        self.updates.append(copy.deepcopy(analysis))
        self.graph["analysis"] = copy.deepcopy(analysis)
        self.graph["revision"] += 1
        return copy.deepcopy(self.graph)

    def approve_analysis(self, analysis_id, revision):
        assert revision == self.graph["revision"]
        self.approvals.append((analysis_id, revision))
        self.graph["state"] = "approved"
        self.graph["revision"] += 1
        self.graph["module_ids"] = {m["id"]: f"test-{m['id']}" for m in self.graph["analysis"]["modules"]}
        return copy.deepcopy(self.graph)

    def save_allocation(self, snapshot, choices):
        if self.fail_save:
            raise GranuleError("Control revision changed; reload before confirmation")
        assert snapshot == self.snapshot
        self.saves.append((copy.deepcopy(snapshot), copy.deepcopy(choices)))
        return {"status": "saved", "workflow_id": "isolated-workflow", "choices": copy.deepcopy(choices)}


class QtDesignUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.require_gl = os.environ.get("AGENTGRANULE_REQUIRE_OPENGL") == "1"
        if QT_IMPORT_ERROR is not None:
            if cls.require_gl:
                raise AssertionError("Native Qt/OpenGL is required: " + QT_IMPORT_ERROR)
            raise unittest.SkipTest("Native Qt unavailable: " + QT_IMPORT_ERROR)
        if QApplication.instance() is None:
            configure_default_format()
            cls.app = QApplication([])
        else:
            cls.app = QApplication.instance()
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.windows = []

    def tearDown(self):
        for window in reversed(self.windows):
            window.close()
            window.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def wait(self, predicate, *, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            QTest.qWait(10)
        self.fail("Timed out waiting for the native window condition")

    def open_window(self, service):
        window = DesignWindow(service, service.graph["analysis_id"])
        self.windows.append(window)
        window.show()
        self.wait(lambda: window.viewport.gl_ready or window.fatal_error is not None)
        if window.fatal_error is not None:
            message = "Native OpenGL unavailable: " + window.fatal_error
            if self.require_gl:
                self.fail(message)
            self.skipTest(message)
        self.wait(lambda: window.confirm.isEnabled())
        return window

    def fill_all(self, window):
        for direction in ("analysis", "explanation"):
            window.direction.setCurrentText(direction)
            for index, module in enumerate(window.modules):
                window.select_module(module["id"])
                window.entry.setText(f"{(index + 1) * 0.17:.2f}")
                QTest.mouseClick(window.apply_button, Qt.MouseButton.LeftButton)
        self.assertEqual(window.model.unassigned(), [])

    def test_cancel_and_suggested_slider_do_not_approve_or_allocate(self):
        service = IsolatedService(True)
        window = self.open_window(service)
        self.assertEqual(window.slider.value(), 50)
        self.assertEqual(len(window.model.unassigned()), 6)
        window.select_module("b")
        window.direction.setCurrentText("explanation")
        window.select_module("a")
        self.assertEqual(len(window.model.unassigned()), 6)
        QTest.mouseClick(window.findChild(QPushButton, "cancelDesign"), Qt.MouseButton.LeftButton)
        self.assertEqual(service.approvals, [])
        self.assertEqual(service.saves, [])
        self.assertIsNone(window.saved_result)

    def test_slider_and_decimal_entry_change_only_pending_selected_direction(self):
        service = IsolatedService(True)
        window = self.open_window(service)
        window.select_module("a")
        window.slider.setValue(37)
        self.assertEqual(window.model.effort("a", "analysis"), 0.37)
        self.assertIsNone(window.model.effort("b", "analysis"))
        self.assertIsNone(window.model.effort("a", "explanation"))
        self.assertEqual(window.entry.text(), "0.37")
        for invalid in ("0.371", "0.370000000000000001", "1.01", "NaN"):
            window.entry.setText(invalid)
            QTest.mouseClick(window.apply_button, Qt.MouseButton.LeftButton)
            self.assertEqual(window.model.effort("a", "analysis"), 0.37)
        window.entry.setText("0.01")
        QTest.keyClick(window.entry, Qt.Key.Key_Return)
        self.assertEqual(window.model.effort("a", "analysis"), 0.01)
        window.direction.setCurrentText("explanation")
        self.assertEqual(window.slider.value(), 50)
        self.assertIsNone(window.model.effort("a", "explanation"))
        window.direction.setCurrentText("analysis")
        self.assertEqual(window.slider.value(), 1)
        self.assertEqual(service.saves, [])

    def test_visible_graph_confirmation_preserves_editable_summary_and_enters_manual_allocation(self):
        service = IsolatedService()
        window = self.open_window(service)
        window.summary.setPlainText("调用者修订后的完整分解依据。")
        self.assertTrue(window.add_button.isEnabled())
        with patch("agentgranule.qt_design_view.QMessageBox.question", return_value=QMessageBox.StandardButton.Cancel):
            QTest.mouseClick(window.confirm, Qt.MouseButton.LeftButton)
        self.assertEqual(service.approvals, [])
        with patch("agentgranule.qt_design_view.QMessageBox.question", return_value=QMessageBox.StandardButton.Ok):
            QTest.mouseClick(window.confirm, Qt.MouseButton.LeftButton)
        self.assertEqual(len(service.updates), 1)
        self.assertEqual(service.updates[0]["summary"], "调用者修订后的完整分解依据。")
        self.assertEqual(len(service.approvals), 1)
        self.assertEqual(len(window.model.unassigned()), 6)
        self.assertTrue(window.summary.isReadOnly())
        self.assertFalse(window.add_button.isEnabled())
        self.assertEqual(service.saves, [])

    def test_full_review_lists_all_directions_and_only_final_confirmation_saves(self):
        service = IsolatedService(True)
        window = self.open_window(service)
        window.confirm_phase()
        self.assertIn("还有 6 项", window.status.text())
        self.assertIsNone(window.review)
        self.fill_all(window)
        # Enter in an effort field applies the pending value; completing the
        # final field must not also invoke QDialog's default phase button.
        QTest.keyClick(window.entry, Qt.Key.Key_Return)
        self.assertIsNone(window.review)
        self.assertEqual(service.saves, [])
        inspected = []

        def cancel_review():
            review = window.review
            inspected.append(review.findChild(QWidget, "allocationReviewTable").rowCount())
            QTest.mouseClick(review.findChild(QPushButton, "returnToAllocation"), Qt.MouseButton.LeftButton)

        QTimer.singleShot(20, cancel_review)
        QTest.mouseClick(window.confirm, Qt.MouseButton.LeftButton)
        self.assertEqual(inspected, [6])
        self.assertEqual(service.saves, [])
        self.assertEqual(len(window.model.unassigned()), 0)

        def save_review():
            QTest.mouseClick(window.review.save_button, Qt.MouseButton.LeftButton)

        QTimer.singleShot(20, save_review)
        QTest.mouseClick(window.confirm, Qt.MouseButton.LeftButton)
        self.assertEqual(len(service.saves), 1)
        snapshot, choices = service.saves[0]
        self.assertEqual(snapshot, service.snapshot)
        self.assertEqual(len(choices), 6)
        self.assertEqual({(c["module_id"], c["direction"]) for c in choices},
                         {(f"test-{key}", direction) for key in ("a", "b", "c")
                          for direction in ("analysis", "explanation")})
        self.assertEqual(window.saved_result["status"], "saved")

    def test_stale_snapshot_save_keeps_pending_choices_and_returns_no_saved_result(self):
        service = IsolatedService(True)
        window = self.open_window(service)
        self.fill_all(window)
        choices = window.model.submitted_choices()
        service.fail_save = True
        review = AllocationReview(window, choices)
        self.windows.append(review)
        review.show()
        QTest.mouseClick(review.save_button, Qt.MouseButton.LeftButton)
        self.assertIn("revision changed", review.error.text())
        self.assertTrue(review.isVisible())
        self.assertIsNone(window.saved_result)
        self.assertEqual(window.model.submitted_choices(), choices)
        self.assertEqual(service.saves, [])

    def test_module_editor_retains_full_fields_relations_and_rejects_duplicate_ids(self):
        service = IsolatedService()
        modules = service.graph["analysis"]["modules"]
        editor = ModuleEditor(modules, modules[0])
        self.windows.append(editor)
        editor.fields["id"].setText("b")
        editor.validate_and_accept()
        self.assertIsNone(editor.item)
        self.assertIn("不能重复", editor.error.text())
        editor.fields["id"].setText("renamed-a")
        editor.fields["name"].setText("修改后模块")
        editor.fields["directions"].setText("analysis，review")
        editor.text_fields["basis"].setPlainText("完整来源与分解依据")
        editor.parent_picker.setCurrentIndex(editor.parent_picker.findData("b"))
        editor.dependencies.item(1).setSelected(True)
        editor.validate_and_accept()
        self.assertEqual(editor.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(editor.item["directions"], ["analysis", "review"])
        self.assertEqual(editor.item["parent_id"], "b")
        self.assertEqual(editor.item["depends_on"], ["c"])
        self.assertEqual(editor.item["basis"], "完整来源与分解依据")
        self.assertEqual(editor.item["description"], modules[0]["description"])
        self.assertEqual(service.updates, [])
        self.assertEqual(service.approvals, [])

    def test_deleting_proposed_module_clears_only_explicit_relations_without_persistence(self):
        service = IsolatedService()
        service.graph["analysis"]["modules"][1]["parent_id"] = "a"
        service.graph["analysis"]["modules"][2]["depends_on"] = ["a", "b"]
        window = self.open_window(service)
        window.select_module("a")
        with patch("agentgranule.qt_design_view.QMessageBox.question", return_value=QMessageBox.StandardButton.Ok):
            window.remove_module()
        self.assertEqual([m["id"] for m in window.modules], ["b", "c"])
        self.assertIsNone(window.module("b")["parent_id"])
        self.assertEqual(window.module("c")["depends_on"], ["b"])
        self.assertEqual(len(service.graph["analysis"]["modules"]), 3)
        self.assertEqual(service.updates, [])

    def test_initial_load_errors_propagate_and_reload_failure_blocks_confirmation(self):
        service = IsolatedService(True)
        with self.assertRaisesRegex(GranuleError, "Unknown analysis"):
            DesignWindow(service, "does-not-exist")
        service.fail_snapshot = True
        with self.assertRaisesRegex(GranuleError, "snapshot failed"):
            DesignWindow(service, service.graph["analysis_id"])
        service.fail_snapshot = False
        window = self.open_window(service)
        service.fail_get = True
        window.load_state()
        self.assertFalse(window.confirm.isEnabled())
        window._graphics_initialized()
        self.assertFalse(window.confirm.isEnabled())
        window.confirm_phase()
        self.assertEqual(service.approvals, [])
        service.fail_get = False
        window.load_state()
        self.assertTrue(window.confirm.isEnabled())

    def test_gl_failure_disables_confirmations_and_is_reported_as_error_on_close(self):
        class FailedViewport(QWidget):
            module_selected = Signal(str)
            fatal_error = Signal(str)
            initialized = Signal()

            def __init__(self, parent=None):
                super().__init__(parent)
                self.gl_ready = False
                self.gl_error = "isolated shader initialization failure"
                QTimer.singleShot(0, lambda: self.fatal_error.emit(self.gl_error))

            def set_modules(self, *_):
                pass

            def select_module(self, *_):
                pass

            def reset_camera(self):
                pass

        service = IsolatedService(True)
        inspected = []

        def close_failed():
            window = next(w for w in self.app.topLevelWidgets() if isinstance(w, DesignWindow) and w.isVisible())
            inspected.append((window.confirm.isEnabled(), window.status.text()))
            window.confirm_phase()
            window.reject()

        with patch("agentgranule.qt_design_view.ModuleViewport", FailedViewport):
            QTimer.singleShot(40, close_failed)
            with self.assertRaisesRegex(GranuleError, "shader initialization failure"):
                show_design_qt(service, service.graph["analysis_id"])
        self.assertFalse(inspected[0][0])
        self.assertIn("不能批准或保存", inspected[0][1])
        self.assertEqual(service.saves, [])
        self.assertEqual(service.approvals, [])

    def test_window_framebuffer_stays_full_resolution_while_dragging_and_controls_remain_reachable(self):
        service = IsolatedService(True)
        window = self.open_window(service)
        for width, height in ((1120, 780), (900, 680), (820, 600)):
            window.resize(width, height)
            QTest.qWait(30)
            self.app.processEvents()
            viewport = window.viewport
            image = viewport.grabFramebuffer()
            ratio = viewport.devicePixelRatioF()
            self.assertLessEqual(abs(image.width() - round(viewport.width() * ratio)), 1)
            self.assertLessEqual(abs(image.height() - round(viewport.height() * ratio)), 1)
            self.assertGreater(image.width(), 320)
            self.assertTrue(viewport.gl_ready)
            for widget in (window.confirm, window.reload_button, window.slider, window.entry, window.apply_button):
                self.assertTrue(widget.isVisible())
                bounds = widget.rect()
                top_left = widget.mapTo(window, bounds.topLeft())
                bottom_right = widget.mapTo(window, bounds.bottomRight())
                self.assertTrue(window.rect().contains(top_left))
                self.assertTrue(window.rect().contains(bottom_right))
            baseline = viewport.diagnostics()
            point = QPoint(viewport.width() // 2, viewport.height() // 2)
            QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=point)
            QTest.mouseMove(viewport, point + QPoint(60, 25), delay=20)
            self.app.processEvents()
            during_drag = viewport.grabFramebuffer()
            self.assertEqual(during_drag.size(), image.size())
            QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=point + QPoint(60, 25))
            self.app.processEvents()
            after_drag = viewport.grabFramebuffer()
            self.assertEqual(after_drag.size(), image.size())
            self.assertNotEqual(viewport.diagnostics()["camera"], baseline["camera"])
            self.assertNotEqual(after_drag, image)
        self.assertEqual(service.approvals, [])
        self.assertEqual(service.saves, [])

    def test_standalone_python_and_pythonw_cancel_cleanly_after_real_gl_context(self):
        # These child processes own QApplication, unlike the in-process UI
        # suite. They catch driver crashes during interpreter/app teardown
        # after show_design_qt returns, including the Windows pythonw host.
        self.open_window(IsolatedService(True))
        repository = Path(__file__).resolve().parents[1]
        binaries = [Path(sys.executable)]
        if os.name == "nt":
            binary = Path(sys.executable).with_name("pythonw.exe")
            self.assertTrue(binary.is_file(), "Windows native-host pythonw must be available")
            binaries.append(binary)
        snippet = '''
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path.cwd() / "tests"))
from test_qt_design_ui import IsolatedService
import agentgranule.qt_design_view as q
from PySide6.QtCore import QTimer
service = IsolatedService(True)
diagnostics = {}
original = q.DesignWindow
class IsolatedWindow(original):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        def cancel_owned_window():
            diagnostics.update(self.viewport.diagnostics())
            self.reject()
        QTimer.singleShot(800, cancel_owned_window)
q.DesignWindow = IsolatedWindow
result = q.show_design_qt(service, service.graph["analysis_id"])
Path(sys.argv[1]).write_text(json.dumps({"result": result, "diagnostics": diagnostics,
    "saves": service.saves, "approvals": service.approvals}), encoding="utf-8")
'''
        with tempfile.TemporaryDirectory(prefix="agentgranule-qt-exit-") as directory:
            for binary in binaries:
                with self.subTest(executable=binary.name):
                    output = Path(directory) / f"{binary.stem}.json"
                    process = subprocess.run([str(binary), "-c", snippet, str(output)],
                                             cwd=repository, capture_output=True, text=True, timeout=20)
                    self.assertEqual(process.returncode, 0, process.stderr)
                    self.assertTrue(output.is_file(), "The native host must return before publishing its result")
                    record = json.loads(output.read_text(encoding="utf-8"))
                    self.assertEqual(record["result"], {"status": "cancelled"})
                    self.assertTrue(record["diagnostics"]["gl_ready"])
                    self.assertGreater(record["diagnostics"]["frame_count"], 0)
                    self.assertEqual(record["saves"], [])
                    self.assertEqual(record["approvals"], [])


if __name__ == "__main__":
    unittest.main()
