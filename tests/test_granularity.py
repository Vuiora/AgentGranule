import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing

from agentgranule import GranuleError, Project


class GranularityTests(unittest.TestCase):
    def setUp(self):
        self.project = Project(":memory:")
        self.session = self.project.create_session("评估某个事件")
        self.module = self.project.add_module(self.session, "优缺分析")

    def tearDown(self):
        self.project.close()

    def test_module_can_have_independent_direction_details(self):
        self.project.set_granularity(self.module, actor="human", direction="advantages", parameters={"count": 2})
        self.project.set_granularity(self.module, actor="human", direction="disadvantages", parameters={"count": 5})
        self.project.set_granularity(self.module, actor="human", direction="explanation", parameters={"detail_level": "detailed", "max_depth": 2})
        self.assertEqual(self.project.prepare_plan(self.module, "advantages")["count"], 2)
        self.assertEqual(self.project.prepare_plan(self.module, "disadvantages")["count"], 5)
        plan = self.project.prepare_plan(self.module, "explanation")
        self.assertIsNone(plan["count"])
        self.assertEqual(plan["parameters"], {"detail_level": "detailed", "max_depth": 2})
        self.assertTrue(self.project.submit_result(plan["plan_id"], output={"text": "详细说明"})["accepted"])

    def test_prompt_exposes_default_without_claiming_human_answer(self):
        request = self.project.request_granularity(self.module, "advantages")
        self.assertIn("列举多少项", request["question"])
        self.assertEqual(request["count"], 3)
        self.assertEqual(request["source"], "builtin_default")
        self.assertEqual(self.project.history(self.session)[-1]["kind"], "granularity.requested")
        self.assertEqual(self.project.prepare_plan(self.module, "advantages")["source"], "builtin_default")

    def test_project_defaults_apply_only_without_module_override(self):
        self.project.set_default_granularity(self.session, "advantages", {"count": 4}, "human")
        other = self.project.add_module(self.session, "另一个模块")
        self.project.set_granularity(self.module, actor="human", direction="advantages", parameters={"count": 2})
        self.assertEqual(self.project.prepare_plan(other, "advantages")["count"], 4)
        plan = self.project.prepare_plan(self.module, "advantages")
        self.project.set_default_granularity(self.session, "advantages", {"count": 6}, "human")
        self.assertTrue(self.project.submit_result(plan["plan_id"], output={"items": ["a", "b"]})["accepted"])
        self.assertEqual(self.project.prepare_plan(other, "advantages")["count"], 6)

    def test_default_change_and_new_override_invalidate_default_plans(self):
        plan = self.project.prepare_plan(self.module, "advantages")
        self.project.set_default_granularity(self.session, "advantages", {"count": 3}, "human")
        with self.assertRaisesRegex(GranuleError, "changed"):
            self.project.submit_result(plan["plan_id"], output={"items": ["a", "b", "c"]})
        plan = self.project.prepare_plan(self.module, "advantages")
        self.project.set_granularity(self.module, actor="human", direction="advantages", parameters={"count": 3})
        with self.assertRaisesRegex(GranuleError, "changed"):
            self.project.submit_result(plan["plan_id"], output={"items": ["a", "b", "c"]})

    def test_changing_other_direction_does_not_invalidate_plan(self):
        plan = self.project.prepare_plan(self.module, "advantages")
        self.project.set_granularity(self.module, actor="human", direction="disadvantages", parameters={"count": 6})
        self.assertTrue(self.project.submit_result(plan["plan_id"], output={"items": ["a", "b", "c"]})["accepted"])

    def test_arbitrary_direction_is_not_forced_into_classification(self):
        plan = self.project.prepare_plan(self.module, "risk-depth")
        self.assertEqual(plan["parameters"], {"detail_level": "standard"})
        self.assertNotIn("exactly", plan["instruction"])
        self.assertTrue(self.project.submit_result(plan["plan_id"], output={"text": "分析"})["accepted"])

    def test_invalid_parameters_do_not_change_history(self):
        baseline = self.project.history(self.session)
        for parameters in [{}, {"count": True}, {"count": 0}, {"max_depth": 1.5}, {"detail_level": ""}, {"x": float("nan")}]:
            with self.subTest(parameters=parameters), self.assertRaises(GranuleError):
                self.project.set_granularity(self.module, actor="human", direction="analysis", parameters=parameters)
        self.assertEqual(self.project.history(self.session), baseline)

    def test_old_count_database_migrates_without_losing_messages_or_plans(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            with Project(path) as project:
                session = project.create_session("legacy")
                module = project.add_problem(session, "B")
                project.record_message(session, "user", "保留原文")
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("CREATE TABLE controls (problem_id TEXT PRIMARY KEY, count INTEGER, revision INTEGER)")
                db.execute("INSERT INTO controls VALUES (?, 2, 4)", (module,))
                db.execute("PRAGMA user_version = 0")
                plan = {"plan_id": "old-plan", "session_id": session, "problem_id": module,
                        "direction": "classification", "count": 2, "revision": 4}
                db.execute("INSERT INTO plans VALUES (?, ?, ?)", ("old-plan", session, json.dumps(plan)))
            with Project(path) as project:
                self.assertEqual(project.get_granularity(module)["parameters"], {"count": 2})
                self.assertTrue(project.submit_result("old-plan", ["a", "b"])["accepted"])
                self.assertEqual(project.history(session)[-2]["payload"]["content"], "保留原文")
                project.set_granularity(module, 5, "human")
            with Project(path) as project:
                self.assertEqual(project.get_granularity(module)["count"], 5)

    def test_defaults_persist_and_apply_across_project_sessions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "defaults.sqlite3"
            with Project(path) as project:
                audit = project.create_session("设置默认值")
                project.set_default_granularity(audit, "advantages", {"count": 4}, "human")
            with Project(path) as project:
                session = project.create_session("另一个会话")
                module = project.add_module(session, "评估模块")
                plan = project.prepare_plan(module, "advantages")
                self.assertEqual(plan["count"], 4)
                self.assertEqual(plan["source"], "project_default")
                self.assertEqual(project.history(audit)[-1]["kind"], "granularity.default_changed")

    def test_invalid_text_result_is_retained(self):
        plan = self.project.prepare_plan(self.module, "explanation")
        with self.assertRaisesRegex(GranuleError, "non-empty text"):
            self.project.submit_result(plan["plan_id"], output={"text": ""})
        self.assertFalse(self.project.history(self.session)[-1]["payload"]["accepted"])


if __name__ == "__main__":
    unittest.main()
