"""Actual Tk layout regressions with isolated in-memory preview services.

These tests never access project data. A generated click is only meaningful
after the target is mapped and has a usable rectangle inside the window.
"""

import copy
import unittest
from unittest.mock import patch

from agentgranule.design_view import show_design


class PreviewService:
    """Synthetic proposals and settings; no database or real approval actor."""

    def __init__(self, approved=False):
        modules = [
            {"id": f"preview-{index}", "name": f"隔离测试模块 {index}",
             "description": "检查模块职责及其分析依据。",
             "expected_output": "测试窗口布局，不执行实际任务。",
             "basis": "隔离测试数据，不代表真实人工确认。",
             "directions": ["explanation"], "parent_id": None,
             "depends_on": []}
            for index in range(3)
        ]
        self.graph = {
            "analysis_id": "isolated-preview", "revision": 2 if approved else 1,
            "state": "approved" if approved else "proposed",
            "analysis": {"summary": "仅用于验证窗口中的确认操作可见且可达。", "modules": modules},
            "module_ids": {module["id"]: module["id"] for module in modules} if approved else {},
            "workflow_id": None,
        }
        self.approvals = []
        self.saves = []

    def get_analysis(self, analysis_id):
        assert analysis_id == self.graph["analysis_id"]
        return copy.deepcopy(self.graph)

    def approve_analysis(self, analysis_id, revision):
        assert analysis_id == self.graph["analysis_id"]
        assert revision == self.graph["revision"]
        self.approvals.append((analysis_id, revision))
        self.graph["state"] = "approved"
        self.graph["revision"] += 1
        self.graph["module_ids"] = {module["id"]: module["id"]
                                    for module in self.graph["analysis"]["modules"]}
        return copy.deepcopy(self.graph)

    def update_analysis(self, *_):
        raise AssertionError("Layout tests must not change the proposed module graph")

    def allocation_snapshot(self, analysis_id):
        assert self.graph["state"] == "approved"
        assert analysis_id == self.graph["analysis_id"]
        return {
            "analysis_id": analysis_id, "revision": self.graph["revision"],
            "workflow_id": None,
            "controls": [{"module_id": module["id"], "direction": "explanation",
                          "parameters": {"detail_level": "standard"}, "source": "builtin_default", "revision": 0}
                         for module in self.graph["analysis"]["modules"]],
        }

    def save_allocation(self, *_):
        self.saves.append("unexpected")
        raise AssertionError("Layout tests must never save any allocation")


def descendants(widget):
    yield widget
    for child in widget.winfo_children():
        yield from descendants(child)


class DesignUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import tkinter as tk
        except ImportError as exc:
            raise unittest.SkipTest(f"Tk is unavailable: {exc}")
        cls.tk = tk
        try:
            probe = tk.Tk()
        except tk.TclError as exc:
            raise unittest.SkipTest(f"Tk display is unavailable: {exc}")
        probe.withdraw()
        probe.destroy()

    def assert_visible(self, root, widget):
        self.assertTrue(widget.winfo_ismapped(), f"Unmapped control: {widget}")
        self.assertGreaterEqual(widget.winfo_width(), 20, f"Collapsed width: {widget}")
        # A horizontal Scale intentionally has a 9px trough and a 13px total
        # height; it is usable without the larger button/entry line height.
        self.assertGreaterEqual(widget.winfo_height(), 10, f"Collapsed height: {widget}")
        x = widget.winfo_rootx() - root.winfo_rootx()
        y = widget.winfo_rooty() - root.winfo_rooty()
        self.assertGreaterEqual(x, 0, f"Control left of window: {widget}")
        self.assertGreaterEqual(y, 0, f"Control above window: {widget}")
        self.assertLessEqual(x + widget.winfo_width(), root.winfo_width() + 1,
                             f"Control beyond right edge: {widget}")
        self.assertLessEqual(y + widget.winfo_height(), root.winfo_height() + 1,
                             f"Control below bottom edge: {widget}")

    @staticmethod
    def button(root, text):
        return next(widget for widget in descendants(root)
                    if widget.winfo_class() == "TButton" and widget.cget("text") == text)

    def run_preview(self, service, inspect, *, scaling=1.333, messagebox=None):
        original_tk = self.tk.Tk
        failures = []

        def factory():
            root = original_tk()
            root.tk.call("tk", "scaling", scaling)
            root.report_callback_exception = lambda _, error, __: failures.append(error)

            def run_inspection():
                try:
                    inspect(root)
                except Exception as exc:
                    failures.append(exc)
                finally:
                    # Each test owns its Tcl interpreter. Cancel its deferred
                    # drawing before destroying it and opening the next root.
                    for timer in root.tk.splitlist(root.tk.call("after", "info")):
                        root.after_cancel(timer)
                    root.destroy()

            root.after(80, run_inspection)
            return root

        with patch("tkinter.Tk", factory):
            if messagebox is None:
                result = show_design(service, service.graph["analysis_id"])
            else:
                with patch("tkinter.messagebox.askokcancel", messagebox):
                    result = show_design(service, service.graph["analysis_id"])
        if failures:
            raise failures[0]
        self.assertEqual(result, {"status": "cancelled"})
        self.assertEqual(service.saves, [])

    def test_action_bar_and_allocation_controls_survive_resize_and_dpi(self):
        for scaling in (1.333, 1.667, 2.0):
            for approved in (False, True):
                with self.subTest(scaling=scaling, approved=approved):
                    service = PreviewService(approved)

                    def inspect(root):
                        for geometry in ("1120x780", "900x680"):
                            root.geometry(geometry)
                            root.update()
                            with self.subTest(geometry=geometry):
                                confirm_text = ("查看分配清单并确认 →" if approved
                                                else "确认模块清单 →")
                                for text in ("取消", "重新加载", "重置视角", confirm_text):
                                    self.assert_visible(root, self.button(root, text))
                                if approved:
                                    scale = next(widget for widget in descendants(root)
                                                 if isinstance(widget, self.tk.Scale))
                                    entry = next(widget for widget in descendants(root)
                                                 if widget.winfo_class() == "TEntry")
                                    apply = self.button(root, "应用当前值")
                                    for widget in (scale, entry, apply):
                                        self.assert_visible(root, widget)
                                        self.assertEqual(str(widget.cget("state")), "normal")

                    self.run_preview(service, inspect, scaling=scaling)
                    self.assertEqual(service.approvals, [])

    def test_visible_confirmation_click_enters_allocation_once(self):
        service = PreviewService()
        dialog_parents = []

        def isolated_confirmation(*_, **kwargs):
            dialog_parents.append(kwargs["parent"])
            return True

        def inspect(root):
            root.geometry("900x680")
            root.update()
            button = self.button(root, "确认模块清单 →")
            # Synthetic events can invoke an unmapped widget. First prove that
            # a human can reach the actual visible target at this window size.
            self.assert_visible(root, button)
            x, y = button.winfo_width() // 2, button.winfo_height() // 2
            button.event_generate("<Enter>", x=x, y=y)
            button.event_generate("<ButtonPress-1>", x=x, y=y)
            root.update()
            button.event_generate("<ButtonRelease-1>", x=x, y=y)
            root.update()
            self.assertEqual(service.approvals, [("isolated-preview", 1)])
            self.assertEqual(dialog_parents, [root])
            self.assertEqual(service.graph["state"], "approved")
            self.assert_visible(root, self.button(root, "查看分配清单并确认 →"))

        self.run_preview(service, inspect, messagebox=isolated_confirmation)


if __name__ == "__main__":
    unittest.main()
