import tempfile
import unittest
from pathlib import Path

from agentgranule import GranuleError, Project


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.project = Project(":memory:")
        self.session = self.project.create_session("问题 A")
        self.parent = self.project.add_problem(self.session, "A")
        self.child = self.project.add_problem(self.session, "B", self.parent)

    def tearDown(self):
        self.project.close()

    def test_human_classification_plan_and_result(self):
        self.project.set_granularity(self.child, 3, "human")
        plan = self.project.prepare_plan(self.child)
        self.assertEqual(plan["parent_id"], self.parent)
        self.assertEqual(plan["count"], 3)
        result = self.project.submit_result(plan["plan_id"], ["甲", "乙", "丙"])
        self.assertTrue(result["accepted"])

    def test_plan_requires_human_control(self):
        with self.assertRaises(GranuleError):
            self.project.prepare_plan(self.child)

    def test_invalid_counts_do_not_change_control(self):
        for count in [0, -1, True, 2.5, "3"]:
            with self.subTest(count=count), self.assertRaises(GranuleError):
                self.project.set_granularity(self.child, count, "human")
        self.assertFalse(any(e["kind"] == "granularity.changed" for e in self.project.history(self.session)))

    def test_revisions_invalidate_old_plans_and_keep_history(self):
        self.project.set_granularity(self.child, 3, "human")
        plan = self.project.prepare_plan(self.child)
        change = self.project.set_granularity(self.child, 5, "human")
        self.assertEqual(change["previous_count"], 3)
        with self.assertRaisesRegex(GranuleError, "changed"):
            self.project.submit_result(plan["plan_id"], ["a", "b", "c"])
        self.assertEqual(self.project.prepare_plan(self.child)["count"], 5)
        self.assertFalse(self.project.history(self.session)[-2]["payload"]["accepted"])

    def test_rejected_results_are_recorded(self):
        self.project.set_granularity(self.child, 3, "human")
        plan = self.project.prepare_plan(self.child)
        with self.assertRaisesRegex(GranuleError, "exactly 3"):
            self.project.submit_result(plan["plan_id"], ["a"])
        event = self.project.history(self.session)[-1]
        self.assertEqual(event["kind"], "result.submitted")
        self.assertEqual(event["payload"]["categories"], ["a"])

    def test_messages_preserve_original_text_and_roles(self):
        for role in ["user", "assistant", "tool", "system"]:
            self.project.record_message(self.session, role, " 中文\n原文  ")
        messages = [e for e in self.project.history(self.session) if e["kind"] == "conversation.message"]
        self.assertEqual(len(messages), 4)
        self.assertTrue(all(e["payload"]["content"] == " 中文\n原文  " for e in messages))

    def test_parent_cannot_cross_sessions(self):
        other = self.project.create_session("other")
        with self.assertRaises(GranuleError):
            self.project.add_problem(other, "child", self.parent)

    def test_controls_are_scoped_to_problem(self):
        self.project.set_granularity(self.parent, 2, "human")
        self.project.set_granularity(self.child, 5, "human")
        self.assertEqual(self.project.prepare_plan(self.parent)["count"], 2)
        self.assertEqual(self.project.prepare_plan(self.child)["count"], 5)

    def test_persistence_after_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.sqlite3"
            with Project(path) as project:
                session = project.create_session("persistent")
                project.record_message(session, "user", "原文")
            with Project(path) as project:
                self.assertEqual(project.history(session)[-1]["payload"]["content"], "原文")

    def test_unknown_identifiers_fail(self):
        with self.assertRaises(GranuleError):
            self.project.history("unknown")
        with self.assertRaises(GranuleError):
            self.project.set_granularity("unknown", 3, "human")
        with self.assertRaises(GranuleError):
            self.project.submit_result("unknown", [])


if __name__ == "__main__":
    unittest.main()
