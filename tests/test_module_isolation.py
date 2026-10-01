"""Real-service regressions for independent module and direction allocation."""

import copy
import tempfile
import unittest
from pathlib import Path

from agentgranule.core import Project
from agentgranule.design import Design, task_id
from agentgranule.design_view import AllocationModel


class ModuleIsolationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / "isolated.sqlite3"
        self.project = Project(self.database)
        self.addCleanup(self.project.close)
        session_id = self.project.create_session("Independent allocation regression")
        self.design = Design(self.project)
        proposal = self.design.propose_analysis(session_id, {
            "summary": "Two unrelated modules with independently controlled directions",
            "modules": [
                {"id": name, "name": name, "description": f"Independent module {name}",
                 "expected_output": f"Output for {name}", "basis": "Isolated test fixture",
                 "directions": ["classification", "explanation"]}
                for name in ("a", "b")
            ],
        })
        # These are test-only confirmations in a temporary database, never real user choices.
        self.graph = self.design.approve_analysis(
            proposal["analysis_id"], proposal["revision"], "test:module-isolation")
        for name, count, effort in (("a", 3, 0.25), ("b", 2, 0.73)):
            for direction in ("classification", "explanation"):
                parameters = {"design_effort": effort, "detail_level": "standard",
                              "max_depth": 2, "audience": {"module": name}}
                if direction == "classification":
                    parameters["count"] = count
                self.project.set_granularity(
                    self.graph["module_ids"][name], direction=direction,
                    parameters=parameters, actor="test:module-isolation")
        self.snapshot = self.design.allocation_snapshot(self.graph["analysis_id"])
        saved = self.design.save_allocation(
            self.snapshot, AllocationModel(self.graph, self.snapshot).submitted_choices(),
            "test:module-isolation")
        self.workflow_id = saved["workflow_id"]
        self.graph = self.design.get_analysis(self.graph["analysis_id"])
        self.snapshot = self.design.allocation_snapshot(self.graph["analysis_id"])

    def finish_workflow(self):
        while True:
            current = self.design.workflow.next_task(self.workflow_id)
            request = current["request"]
            if request is None:
                self.assertTrue(current["complete"])
                return
            count = request["constraints"]["item_count"]
            identifier = request["task"]["id"]
            output = ({"text": f"Durable output for {identifier}"} if count is None else
                      {"items": [f"{identifier}: item {index}" for index in range(count)]})
            self.design.workflow.submit_task(self.workflow_id, request["request_id"], output)

    def test_preview_changes_one_pair_without_mutating_other_directions_or_database(self):
        model = AllocationModel(self.graph, self.snapshot)
        before_choices = copy.deepcopy(model.choices)
        before_snapshot = copy.deepcopy(self.snapshot)
        model.set_units("a", "classification", 100)
        self.assertEqual(model.effort("a", "classification"), 1.0)
        for name, direction in (("a", "explanation"), ("b", "classification"),
                                ("b", "explanation")):
            key = (self.graph["module_ids"][name], direction)
            self.assertEqual(model.choices[key], before_choices[key])
        # Inspecting a different displayed direction must retain its own value.
        self.assertEqual(model.effort("b", "explanation"), 0.73)
        self.assertEqual(model.effort("b", "classification"), 0.73)
        self.assertEqual(self.snapshot, before_snapshot)
        self.assertEqual(self.design.allocation_snapshot(self.graph["analysis_id"]), before_snapshot)

    def test_save_and_refresh_preserve_unrelated_parameters_revisions_and_valid_results(self):
        self.finish_workflow()
        _, completed = self.design.workflow._load(self.workflow_id)
        original_results = copy.deepcopy(completed["results"])
        original_controls = {(control["module_id"], control["direction"]): copy.deepcopy(control)
                             for control in self.snapshot["controls"]}
        model = AllocationModel(self.graph, self.snapshot)
        model.set_units("a", "classification", 100)
        saved = self.design.save_allocation(
            self.snapshot, model.submitted_choices(), "test:module-isolation")
        changed_task = task_id("a", "classification")
        self.assertEqual(saved["affected_task_ids"], [changed_task])
        self.assertEqual(saved["report"]["statuses"][changed_task], "ready")
        self.assertNotIn(changed_task, saved["report"]["outputs"])
        _, refreshed = self.design.workflow._load(self.workflow_id)
        for name, direction in (("a", "explanation"), ("b", "classification"),
                                ("b", "explanation")):
            actual_id = self.graph["module_ids"][name]
            current_control = self.project.get_granularity(actual_id, direction)
            self.assertEqual({**current_control, "module_id": actual_id},
                             original_controls[(actual_id, direction)])
            identifier = task_id(name, direction)
            self.assertEqual(refreshed["results"][identifier], original_results[identifier])
            self.assertEqual(saved["report"]["statuses"][identifier], "completed")
        # Durable restart and the subsequent rerun must retain those receipts and outputs.
        with Project(self.database) as restarted_project:
            restarted = Design(restarted_project)
            request = restarted.workflow.next_task(self.workflow_id)["request"]
            self.assertEqual(request["task"]["id"], changed_task)
            self.assertEqual(request["constraints"]["design_effort"], 1.0)
            restarted.workflow.submit_task(self.workflow_id, request["request_id"],
                                            {"items": ["Updated 1", "Updated 2", "Updated 3"]})
            _, rerun = restarted.workflow._load(self.workflow_id)
            for identifier in original_results.keys() - {changed_task}:
                self.assertEqual(rerun["results"][identifier], original_results[identifier])
            self.assertTrue(restarted.workflow.workflow_status(self.workflow_id)["complete"])


if __name__ == "__main__":
    unittest.main()
