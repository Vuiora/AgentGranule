"""Hundredth precision across persistence, popup and task dispatch boundaries."""

import tempfile
import unittest
from pathlib import Path
from decimal import localcontext
from unittest.mock import patch

from agentgranule import GranuleError, Project, Workflow, compile_constraints
from agentgranule.slider import PopupControl


class DecimalGranularityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / "decimal.sqlite3")
        with Project(self.db) as project:
            self.session = project.create_session("精确分配设计力度")
            self.module = project.add_module(self.session, "框架分析")
        self.service = PopupControl(self.db)

    def test_all_hundredths_round_trip_and_compile_without_mutating_input(self):
        for units in range(101):
            parameters = {"design_effort": units / 100, "detail_level": "detailed", "count": 2, "max_depth": 1}
            with Project(self.db) as project:
                project.set_granularity(self.module, parameters=parameters, actor="test-human", direction="analysis")
            with Project(self.db) as project:
                stored = project.get_granularity(self.module, "analysis")["parameters"]
                self.assertEqual(stored, parameters)
                constraints = compile_constraints(stored)
                self.assertEqual(constraints["design_effort"], units / 100)
                self.assertEqual(constraints["item_count"], 2)
                self.assertNotIn("design_effort", constraints["custom"])
                self.assertEqual(project.prepare_plan(self.module, "analysis")["parameters"], stored)
        original = {"design_effort": 1}
        compile_constraints(original)
        self.assertEqual(type(original["design_effort"]), int)

    def test_invalid_effort_rejected_at_all_write_and_compile_entries(self):
        with Project(self.db) as project:
            baseline = project.history(self.session)
            snapshot = self.service.load(self.module, "analysis")
            for effort in (True, False, "0.37", None, -0.01, 1.01, 0.001, 0.375,
                           0.1 + 0.2, float("nan"), float("inf"), float("-inf")):
                parameters = {"design_effort": effort}
                with self.subTest(effort=effort):
                    with self.assertRaises(GranuleError):
                        project.set_granularity(self.module, parameters=parameters, actor="test-human", direction="analysis")
                    with self.assertRaises(GranuleError):
                        project.set_default_granularity(self.session, "analysis", parameters, "test-human")
                    with self.assertRaises(GranuleError):
                        compile_constraints(parameters)
                    with self.assertRaises(GranuleError):
                        self.service.save(snapshot, effort, parameter="design_effort")
            self.assertEqual(project.history(self.session), baseline)
            self.assertEqual(self.service.load(self.module, "analysis"), snapshot)

    def test_integer_constraints_remain_integer_only(self):
        for parameter in ("count", "max_depth"):
            for value in (0.01, 1.0, 1.37, True):
                with self.subTest(parameter=parameter, value=value), self.assertRaises(GranuleError):
                    compile_constraints({parameter: value, "design_effort": 0.37})

    def test_host_decimal_context_cannot_round_accepted_values_or_extra_precision(self):
        for precision in (1, 2):
            with self.subTest(precision=precision), localcontext() as context:
                context.prec = precision
                for units in range(101):
                    self.assertEqual(compile_constraints({"design_effort": units / 100})["design_effort"], units / 100)
                for invalid in (0.375, 0.001, 0.999, 0.1 + 0.2):
                    with self.assertRaises(GranuleError):
                        compile_constraints({"design_effort": invalid})

    def test_default_effort_persists_and_module_override_wins(self):
        with Project(self.db) as project:
            project.set_default_granularity(self.session, "analysis", {"design_effort": 0.37}, "test-human")
        snapshot = self.service.load(self.module, "analysis")
        self.assertEqual(snapshot["source"], "project_default")
        self.assertEqual(snapshot["parameters"]["design_effort"], 0.37)
        self.service.save(snapshot, 0.01, parameter="design_effort")
        with Project(self.db) as project:
            project.set_default_granularity(self.session, "analysis", {"design_effort": 0.99}, "test-human")
        self.assertEqual(self.service.load(self.module, "analysis")["parameters"]["design_effort"], 0.01)

    def test_effort_popup_preserves_labels_counts_and_custom_parameters(self):
        parameters = {"detail_level": "custom", "count": 4, "max_depth": 2, "audience": "expert"}
        with Project(self.db) as project:
            project.set_granularity(self.module, parameters=parameters, actor="test-human", direction="analysis")
        result = self.service.save(self.service.load(self.module, "analysis"), 0.37, parameter="design_effort")
        self.assertEqual(result["control"]["parameters"], {**parameters, "design_effort": 0.37})
        with Project(self.db) as project:
            choice = [event for event in project.history(self.session) if event["kind"] == "conversation.message"][-1]
            self.assertIn('"design_effort": 0.37', choice["payload"]["content"])
        result = self.service.save(self.service.load(self.module, "analysis"), "brief")
        self.assertEqual(result["control"]["parameters"]["design_effort"], 0.37)

    def test_unknown_popup_parameter_rejected(self):
        before = self.service.load(self.module, "analysis")
        with self.assertRaises(GranuleError):
            self.service.save(before, 0.37, parameter="count")
        self.assertEqual(self.service.load(self.module, "analysis"), before)

    def test_effort_change_invalidates_dispatched_task_and_dependents_only(self):
        with Project(self.db) as project:
            other = project.add_module(self.session, "独立模块")
            engine = Workflow(project)
            project.set_granularity(self.module, parameters={"design_effort": 0.37}, actor="test-human", direction="analysis")
            wid = engine.create_workflow(self.session, [
                {"id": "a", "module_id": self.module, "direction": "analysis"},
                {"id": "b", "module_id": other, "direction": "analysis"},
                {"id": "c", "module_id": self.module, "direction": "summary", "depends_on": ["a"]}])["workflow_id"]
            while (request := engine.next_task(wid)["request"]) is not None:
                engine.submit_task(wid, request["request_id"], {"text": "测试实际结果"})
        snapshot = self.service.load(self.module, "analysis")
        result = self.service.save(snapshot, 0.38, parameter="design_effort")
        self.assertEqual(result["workflows"][0]["statuses"], {"a": "ready", "b": "completed", "c": "blocked"})
        with self.assertRaisesRegex(GranuleError, "重新加载"):
            self.service.save(snapshot, 0.39, parameter="design_effort")
        with Project(self.db) as project:
            engine = Workflow(project)
            request = engine.next_task(wid)["request"]
            self.assertEqual(request["constraints"]["design_effort"], 0.38)
            self.service.save(self.service.load(self.module, "analysis"), 0.39, parameter="design_effort")
            with self.assertRaisesRegex(GranuleError, "stale"):
                engine.submit_task(wid, request["request_id"], {"text": "过期结果"})
            self.assertEqual(engine.next_task(wid)["request"]["constraints"]["design_effort"], 0.39)

    def test_previous_request_schema_is_invalidated_on_upgrade(self):
        with Project(self.db) as project, patch("agentgranule.workflow.WORKFLOW_VERSION", 1):
            engine = Workflow(project)
            wid = engine.create_workflow(self.session, [
                {"id": "a", "module_id": self.module, "direction": "analysis"}])["workflow_id"]
            old = engine.next_task(wid)["request"]
        with Project(self.db) as project:
            engine = Workflow(project)
            with self.assertRaisesRegex(GranuleError, "stale"):
                engine.submit_task(wid, old["request_id"], {"text": "旧版请求的结果"})
            self.assertNotEqual(engine.next_task(wid)["request"]["request_id"], old["request_id"])


if __name__ == "__main__":
    unittest.main()
