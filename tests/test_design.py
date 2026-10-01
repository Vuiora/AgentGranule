import copy
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from agentgranule.core import GranuleError, Project
from agentgranule.design import Design, task_id


def sample_analysis():
    return {"summary": "依据需求划分基础、总结与独立模块", "context": {"material": "实际需求"},
            "modules": [
                {"id": "a", "name": "基础", "description": "基础设计", "expected_output": "模块方案",
                 "basis": "原始需求第一项", "directions": ["explanation", "classification"]},
                {"id": "b", "name": "总结", "description": "组合结果", "expected_output": "总体说明",
                 "basis": "总体流程要求", "directions": ["explanation"], "parent_id": "a", "depends_on": ["a"]},
                {"id": "c", "name": "独立", "description": "独立设计", "expected_output": "独立方案",
                 "basis": "独立要求", "directions": ["explanation"]}]}


class DesignTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.database = Path(self.tmp.name) / "design.sqlite3"
        self.project = Project(self.database)
        self.addCleanup(self.project.close)
        self.session = self.project.create_session("完整任务")
        self.design = Design(self.project)
        self.proposed = self.design.propose_analysis(self.session, sample_analysis())
        self.analysis_id = self.proposed["analysis_id"]

    def approve(self):
        return self.design.approve_analysis(self.analysis_id,
                                            self.design.get_analysis(self.analysis_id)["revision"], "human:test")

    @staticmethod
    def choices(snapshot, effort=0.37):
        return [{"module_id": control["module_id"], "direction": control["direction"], "design_effort": effort}
                for control in snapshot["controls"]]

    def allocate(self):
        self.approve()
        snapshot = self.design.allocation_snapshot(self.analysis_id)
        return self.design.save_allocation(snapshot, self.choices(snapshot), "human:test")

    def finish(self, workflow_id):
        workflow = self.design.workflow
        while True:
            report = workflow.next_task(workflow_id)
            request = report["request"]
            if request is None:
                self.assertTrue(report["complete"])
                return report
            count = request["constraints"]["item_count"]
            output = {"text": "真实说明"} if count is None else {"items": [f"方案{i}" for i in range(count)]}
            workflow.submit_task(workflow_id, request["request_id"], output)

    def assert_no_changes(self, previous_snapshot, history, workflow_count):
        self.assertEqual(self.design.allocation_snapshot(self.analysis_id), previous_snapshot)
        self.assertEqual(self.project.history(self.session), history)
        self.assertEqual(self.project._db.execute("SELECT count(*) FROM workflows").fetchone()[0], workflow_count)

    def test_proposal_update_approval_and_original_history(self):
        changed = sample_analysis()
        changed["modules"][0]["name"] = "新的模块名"
        updated = self.design.update_analysis(self.analysis_id, 1, changed)
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(updated["module_ids"], {})
        with self.assertRaisesRegex(GranuleError, "revision changed"):
            self.design.approve_analysis(self.analysis_id, 1, "human:test")
        approved = self.approve()
        self.assertEqual(approved["revision"], 3)
        self.assertIsNone(approved["workflow_id"])
        child = self.project._problem(approved["module_ids"]["b"])
        self.assertEqual(child["parent_id"], approved["module_ids"]["a"])
        self.assertIn("新的模块名", self.project._problem(approved["module_ids"]["a"])["description"])
        event = next(event for event in self.project.history(self.session) if event["kind"] == "design.analysis_updated")
        self.assertEqual(event["payload"]["previous"]["analysis"]["modules"][0]["name"], "基础")
        with self.assertRaisesRegex(GranuleError, "immutable"):
            self.design.update_analysis(self.analysis_id, 3, changed)
        new = self.design.propose_analysis(self.session, sample_analysis(), self.analysis_id)
        self.assertEqual(new["previous_analysis_id"], self.analysis_id)
        self.assertEqual(self.design.get_analysis(self.analysis_id), approved)

    def test_graph_validation_has_no_partial_proposals(self):
        variants = []
        for field, value in (("parent_id", "missing"), ("depends_on", ["missing"]),
                             ("parent_id", "b"), ("depends_on", ["b"])):
            bad = sample_analysis()
            bad["modules"][0][field] = value
            variants.append(bad)
        duplicate = sample_analysis()
        duplicate["modules"].append(copy.deepcopy(duplicate["modules"][0]))
        variants.append(duplicate)
        for field, value in (("directions", []), ("directions", ["analysis", "analysis"]),
                             ("depends_on", ["c", "c"]), ("basis", " "), ("unknown", "x")):
            bad = sample_analysis()
            bad["modules"][0][field] = value
            variants.append(bad)
        variants.extend([{}, {"summary": "x", "modules": []}, {**sample_analysis(), "extra": 1},
                         {**sample_analysis(), "context": {"bad": float("nan")}}])
        history = self.project.history(self.session)
        for bad in variants:
            with self.subTest(bad=bad), self.assertRaises(GranuleError):
                self.design.propose_analysis(self.session, bad)
        self.assertEqual(self.project.history(self.session), history)
        self.assertEqual(self.project._db.execute("SELECT count(*) FROM design_analyses").fetchone()[0], 1)

    def test_wrong_session_previous_and_foreign_allocation_rejected(self):
        approved = self.approve()
        other_session = self.project.create_session("其他任务")
        with self.assertRaisesRegex(GranuleError, "same session"):
            self.design.propose_analysis(other_session, sample_analysis(), self.analysis_id)
        foreign = self.project.add_module(other_session, "不相关模块")
        snapshot = self.design.allocation_snapshot(self.analysis_id)
        choices = self.choices(snapshot)
        choices[0]["module_id"] = foreign
        with self.assertRaisesRegex(GranuleError, "foreign"):
            self.design.save_allocation(snapshot, choices, "human:test")
        self.assertIsNone(self.design.get_analysis(approved["analysis_id"])["workflow_id"])

    def test_proposal_cannot_allocate_and_confirmation_arguments_required(self):
        with self.assertRaisesRegex(GranuleError, "Approve"):
            self.design.allocation_snapshot(self.analysis_id)
        for actor in (None, "", " ", 1):
            with self.subTest(actor=actor), self.assertRaises(GranuleError):
                self.design.approve_analysis(self.analysis_id, 1, actor)
        for revision in (True, 1.0, "1", 0):
            with self.subTest(revision=revision), self.assertRaises(GranuleError):
                self.design.approve_analysis(self.analysis_id, revision, "human:test")
        self.assertEqual(self.design.get_analysis(self.analysis_id)["state"], "proposed")

    def test_complete_first_allocation_and_parameter_preservation(self):
        approved = self.approve()
        actual_id = approved["module_ids"]["a"]
        self.project.set_granularity(actual_id, direction="classification", actor="human",
                                     parameters={"count": 2, "max_depth": 3, "detail_level": "brief", "audience": "expert"})
        snapshot = self.design.allocation_snapshot(self.analysis_id)
        self.assertEqual(len(snapshot["controls"]), 4)
        saved = self.design.save_allocation(snapshot, self.choices(snapshot), "alice")
        control = self.project.get_granularity(actual_id, "classification")
        self.assertEqual(control["parameters"], {"count": 2, "max_depth": 3, "detail_level": "brief", "audience": "expert", "design_effort": 0.37})
        self.assertEqual(set(saved["affected_task_ids"]), {task_id("a", "classification"), task_id("a", "explanation"),
                                                           task_id("b", "explanation"), task_id("c", "explanation")})
        _, data = self.design.workflow._load(saved["workflow_id"])
        self.assertEqual(data["context"], sample_analysis()["context"])
        child = next(task for task in data["tasks"] if task["module_id"] == approved["module_ids"]["b"])
        self.assertEqual(set(child["depends_on"]), {task_id("a", "classification"), task_id("a", "explanation")})
        self.assertEqual(self.design.get_analysis(self.analysis_id)["workflow_id"], saved["workflow_id"])
        self.finish(saved["workflow_id"])

    def test_partial_duplicate_invalid_choices_have_no_writes(self):
        self.approve()
        snapshot = self.design.allocation_snapshot(self.analysis_id)
        history = self.project.history(self.session)
        variants = [[], self.choices(snapshot)[:-1], self.choices(snapshot) + [self.choices(snapshot)[0]]]
        for effort in (0.375, -0.01, 1.01, True, "0.37", float("inf"), float("nan")):
            choices = self.choices(snapshot)
            choices[-1]["design_effort"] = effort
            variants.append(choices)
        for choices in variants:
            with self.subTest(choices=choices), self.assertRaises(GranuleError):
                self.design.save_allocation(snapshot, choices, "human:test")
            self.assert_no_changes(snapshot, history, 0)

    def test_snapshot_entire_control_source_and_revision_checked(self):
        self.approve()
        snapshot = self.design.allocation_snapshot(self.analysis_id)
        history = self.project.history(self.session)
        variants = []
        for field, value in (("source", "module"), ("revision", False), ("module_id", "wrong"), ("count", 99)):
            bad = copy.deepcopy(snapshot)
            bad["controls"][0][field] = value
            variants.append(bad)
        bad = copy.deepcopy(snapshot)
        bad["controls"] = bad["controls"][:-1]
        variants.append(bad)
        variants.extend([{**snapshot, "extra": 1}, {**snapshot, "workflow_id": "wrong"}])
        for bad in variants:
            with self.subTest(bad=bad), self.assertRaisesRegex(GranuleError, "snapshot changed"):
                self.design.save_allocation(bad, self.choices(snapshot), "human:test")
            self.assert_no_changes(snapshot, history, 0)

    def test_module_or_default_change_rejects_whole_old_snapshot(self):
        approved = self.approve()
        snapshot = self.design.allocation_snapshot(self.analysis_id)
        # An identical-valued default still changes source/revision and must be re-confirmed.
        self.project.set_default_granularity(self.session, "classification", {"count": 3, "detail_level": "standard"}, "human")
        history = self.project.history(self.session)
        current = self.design.allocation_snapshot(self.analysis_id)
        with self.assertRaisesRegex(GranuleError, "snapshot changed"):
            self.design.save_allocation(snapshot, self.choices(snapshot), "human:test")
        self.assert_no_changes(current, history, 0)
        self.project.set_granularity(approved["module_ids"]["c"], direction="explanation", actor="human", parameters={"detail_level": "brief"})
        with self.assertRaisesRegex(GranuleError, "snapshot changed"):
            self.design.save_allocation(current, self.choices(current), "human:test")

    def test_approval_failure_rolls_back_modules_graph_and_events(self):
        history = self.project.history(self.session)
        original = self.project._event
        def fail(session_id, kind, payload):
            if kind == "design.analysis_approved":
                raise RuntimeError("storage failed")
            original(session_id, kind, payload)
        with patch.object(self.project, "_event", side_effect=fail), self.assertRaises(RuntimeError):
            self.approve()
        self.assertEqual(self.project.history(self.session), history)
        self.assertEqual(self.project._db.execute("SELECT count(*) FROM problems").fetchone()[0], 0)
        self.assertEqual(self.design.get_analysis(self.analysis_id), self.proposed)

    def test_first_save_failure_rolls_back_all_controls_workflow_and_events(self):
        self.approve()
        snapshot = self.design.allocation_snapshot(self.analysis_id)
        history = self.project.history(self.session)
        original = self.project._event
        def fail(session_id, kind, payload):
            if kind == "design.allocation_confirmed":
                raise RuntimeError("storage failed after workflow insert")
            original(session_id, kind, payload)
        with patch.object(self.project, "_event", side_effect=fail), self.assertRaises(RuntimeError):
            self.design.save_allocation(snapshot, self.choices(snapshot), "human:test")
        self.assert_no_changes(snapshot, history, 0)
        self.assertEqual(self.project._db.execute("SELECT count(*) FROM module_controls").fetchone()[0], 0)

    def test_changes_refresh_only_real_dependency_closure_and_reject_old_request(self):
        saved = self.allocate()
        workflow_id = saved["workflow_id"]
        self.finish(workflow_id)
        snapshot = self.design.allocation_snapshot(self.analysis_id)
        choices = self.choices(snapshot)
        actual_id = self.design.get_analysis(self.analysis_id)["module_ids"]["a"]
        for choice in choices:
            if choice["module_id"] == actual_id and choice["direction"] == "classification":
                choice["design_effort"] = 0.38
        changed = self.design.save_allocation(snapshot, choices, "human:test")
        self.assertEqual(set(changed["affected_task_ids"]), {task_id("a", "classification"), task_id("b", "explanation")})
        self.assertEqual(set(changed["report"]["outputs"]), {task_id("a", "explanation"), task_id("c", "explanation")})
        request = self.design.workflow.next_task(workflow_id)["request"]
        snapshot = self.design.allocation_snapshot(self.analysis_id)
        changed_choices = [{"module_id": control["module_id"], "direction": control["direction"],
                            "design_effort": 0.39 if control["module_id"] == actual_id and control["direction"] == "classification"
                            else control["parameters"]["design_effort"]} for control in snapshot["controls"]]
        self.design.save_allocation(snapshot, changed_choices, "human:test")
        with self.assertRaisesRegex(GranuleError, "stale request"):
            self.design.workflow.submit_task(workflow_id, request["request_id"], {"items": ["1", "2", "3"]})
        self.finish(workflow_id)

    def test_no_change_records_confirmation_without_revision_or_result_invalidation(self):
        saved = self.allocate()
        self.finish(saved["workflow_id"])
        snapshot = self.design.allocation_snapshot(self.analysis_id)
        history = self.project.history(self.session)
        repeated = self.design.save_allocation(snapshot, self.choices(snapshot), "human:test")
        self.assertEqual(repeated["affected_task_ids"], [])
        self.assertTrue(repeated["report"]["complete"])
        self.assertEqual(snapshot, self.design.allocation_snapshot(self.analysis_id))
        self.assertEqual([event["kind"] for event in self.project.history(self.session)[len(history):]],
                         ["design.allocation_confirmed", "conversation.message"])

    def test_confirming_same_default_effort_pins_the_actual_human_choice(self):
        self.approve()
        self.project.set_default_granularity(self.session, "explanation", {"design_effort": 0.37}, "human")
        snapshot = self.design.allocation_snapshot(self.analysis_id)
        self.assertIsNone(snapshot["workflow_id"])
        saved = self.design.save_allocation(snapshot, self.choices(snapshot), "human:test")
        self.assertEqual(len(saved["report"]["statuses"]), 4)
        controls = [control for control in saved["controls"] if control["direction"] == "explanation"]
        self.assertTrue(all(control["source"] == "module" for control in controls))
        self.assertEqual(sum(event["kind"] == "design.allocation_confirmed" for event in self.project.history(self.session)), 1)
        self.finish(saved["workflow_id"])
        self.project.set_default_granularity(self.session, "explanation", {"design_effort": 0.9}, "human")
        snapshot = self.design.allocation_snapshot(self.analysis_id)
        self.assertTrue(all(control["parameters"]["design_effort"] == 0.37 for control in snapshot["controls"]))
        self.assertTrue(self.design.workflow.workflow_status(saved["workflow_id"])["complete"])

    def test_save_failure_restores_completed_results_and_pending_requests(self):
        saved = self.allocate()
        self.finish(saved["workflow_id"])
        snapshot = self.design.allocation_snapshot(self.analysis_id)
        choices = self.choices(snapshot, 0.38)
        history = self.project.history(self.session)
        before_workflow = self.design.workflow._load(saved["workflow_id"])
        original = self.project._event
        def fail(session_id, kind, payload):
            if kind == "design.allocation_confirmed":
                raise RuntimeError("storage failed after invalidation")
            original(session_id, kind, payload)
        with patch.object(self.project, "_event", side_effect=fail), self.assertRaises(RuntimeError):
            self.design.save_allocation(snapshot, choices, "human:test")
        self.assert_no_changes(snapshot, history, 1)
        self.assertEqual(self.design.workflow._load(saved["workflow_id"]), before_workflow)

    def test_concurrent_approvals_create_only_one_module_graph(self):
        def approve(_):
            with Project(self.database) as project:
                try:
                    Design(project).approve_analysis(self.analysis_id, 1, "human:test")
                    return True
                except GranuleError:
                    return False
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(approve, range(2))), [False, True])
        self.assertEqual(self.project._db.execute("SELECT count(*) FROM problems").fetchone()[0], 3)

    def test_restart_recovers_graph_allocation_and_workflow(self):
        saved = self.allocate()
        request = self.design.workflow.next_task(saved["workflow_id"])["request"]
        with Project(self.database) as project:
            design = Design(project)
            self.assertEqual(design.get_analysis(self.analysis_id), self.design.get_analysis(self.analysis_id))
            self.assertEqual(design.allocation_snapshot(self.analysis_id), self.design.allocation_snapshot(self.analysis_id))
            self.assertEqual(design.workflow.next_task(saved["workflow_id"])["request"], request)

    def test_concurrent_stale_full_snapshots_accept_exactly_one(self):
        self.approve()
        snapshot = self.design.allocation_snapshot(self.analysis_id)
        def save(value):
            with Project(self.database) as project:
                try:
                    Design(project).save_allocation(snapshot, self.choices(snapshot, value), "human:test")
                    return True
                except GranuleError:
                    return False
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(save, [0.37, 0.38])), [False, True])
        self.assertEqual(self.project._db.execute("SELECT count(*) FROM workflows").fetchone()[0], 1)
        events = [event for event in self.project.history(self.session) if event["kind"] == "design.allocation_confirmed"]
        self.assertEqual(len(events), 1)

    def test_midnight_does_not_reset_revision_or_approve_unsaved_choices(self):
        with patch("agentgranule.core.datetime") as clock:
            clock.now.return_value = datetime(2026, 10, 1, 15, 59, tzinfo=timezone.utc)
            approved = self.approve()
            snapshot = self.design.allocation_snapshot(self.analysis_id)
            clock.now.return_value = datetime(2026, 10, 1, 16, 1, tzinfo=timezone.utc)
            saved = self.design.save_allocation(snapshot, self.choices(snapshot, 0.0), "human:test")
        self.assertEqual(approved["revision"], self.design.get_analysis(self.analysis_id)["revision"])
        self.assertTrue(all(control["parameters"]["design_effort"] == 0 for control in saved["controls"]))
        event = next(event for event in self.project.history(self.session) if event["kind"] == "design.allocation_confirmed")
        self.assertTrue(event["timestamp"].startswith("2026-10-01T16:01"))
        self.assertFalse(saved["report"]["complete"])

    def test_boundaries_and_task_identifiers_do_not_collide(self):
        self.assertNotEqual(task_id("a:b", "c"), task_id("a", "b:c"))
        self.approve()
        for value in (0, 0.01, 0.99, 1):
            snapshot = self.design.allocation_snapshot(self.analysis_id)
            saved = self.design.save_allocation(snapshot, self.choices(snapshot, value), "human:test")
            self.assertTrue(all(control["parameters"]["design_effort"] == value for control in saved["controls"]))


if __name__ == "__main__":
    unittest.main()
