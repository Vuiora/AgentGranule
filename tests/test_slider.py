import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from agentgranule import GranuleError, Project, Workflow
from agentgranule.slider import PopupControl, main


class PopupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / "popup.sqlite3")
        with Project(self.db) as project:
            self.session = project.create_session("测试弹窗")
            self.module = project.add_module(self.session, "解释方案")
            project.set_granularity(self.module, direction="explanation", actor="human",
                parameters={"detail_level": "custom", "audience": "expert", "max_depth": 2})
        self.service = PopupControl(self.db)

    def test_discovery_and_custom_values_preserved(self):
        self.assertEqual(self.service.modules()[0]["id"], self.module)
        self.assertIn("explanation", self.service.directions())
        result = self.service.save(self.service.load(self.module, "explanation"), "detailed")
        self.assertEqual(result["control"]["parameters"], {"detail_level": "detailed", "audience": "expert", "max_depth": 2})
        self.assertNotIn("count", result["control"]["parameters"])
        self.assertEqual(result["control"]["actor"], "human:popup")

    def test_old_dialog_cannot_overwrite_new_choice(self):
        snapshot = self.service.load(self.module, "explanation")
        self.service.save(snapshot, "detailed")
        with self.assertRaisesRegex(GranuleError, "重新加载"):
            self.service.save(snapshot, "brief")
        self.assertEqual(self.service.load(self.module, "explanation")["parameters"]["detail_level"], "detailed")

    def test_concurrent_dialogs_accept_only_one(self):
        snapshot = self.service.load(self.module, "explanation")
        def save(_):
            try:
                PopupControl(self.db).save(snapshot, "brief")
                return True
            except GranuleError:
                return False
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(save, range(2))), [False, True])

    def test_invalid_level_no_write(self):
        before = self.service.load(self.module, "explanation")
        with self.assertRaises(GranuleError):
            self.service.save(before, "invented")
        self.assertEqual(self.service.load(self.module, "explanation"), before)

    def test_default_change_makes_open_dialog_stale(self):
        snapshot = self.service.load(self.module, "analysis")
        with Project(self.db) as project:
            project.set_default_granularity(self.session, "analysis", {"detail_level": "detailed"}, "human")
        with self.assertRaises(GranuleError):
            self.service.save(snapshot, "brief")

    def test_save_invalidates_only_relevant_workflow_tasks_and_records_choice(self):
        with Project(self.db) as project:
            other = project.add_module(self.session, "其他模块")
            engine = Workflow(project)
            wid = engine.create_workflow(self.session, [
                {"id": "a", "module_id": self.module, "direction": "explanation"},
                {"id": "b", "module_id": other, "direction": "explanation"},
                {"id": "c", "module_id": self.module, "direction": "analysis", "depends_on": ["a"]}])["workflow_id"]
            while True:
                req = engine.next_task(wid)["request"]
                if req is None:
                    break
                engine.submit_task(wid, req["request_id"], {"text": "真实测试结果"})
        result = self.service.save(self.service.load(self.module, "explanation"), "brief")
        self.assertEqual(result["workflows"][0]["statuses"], {"a": "ready", "b": "completed", "c": "blocked"})
        with Project(self.db) as project:
            self.assertTrue(any(e["kind"] == "conversation.message" and "粒度弹窗确认" in e["payload"]["content"] for e in project.history(self.session)))

    def test_cancel_reports_to_skill_without_setting_mutation(self):
        before = self.service.load(self.module, "explanation")
        output = str(Path(self.tmp.name) / "choice.json")
        with patch("agentgranule.slider.show_popup", return_value={"status": "cancelled"}):
            self.assertEqual(main(["--database", self.db, "--module-id", self.module, "--output-file", output]), 0)
        self.assertEqual(json.loads(Path(output).read_text(encoding="utf-8")), {"status": "cancelled"})
        self.assertEqual(self.service.load(self.module, "explanation"), before)

    def test_gui_failure_never_returns_human_choice(self):
        output = str(Path(self.tmp.name) / "choice.json")
        with patch("agentgranule.slider.show_popup", side_effect=RuntimeError("no display")):
            self.assertEqual(main(["--database", self.db, "--output-file", output]), 2)
        self.assertFalse(Path(output).exists())


if __name__ == "__main__":
    unittest.main()
