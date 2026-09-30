import tempfile
import unittest
from pathlib import Path

from agentgranule import AlgorithmRunner, GranuleError, Project, Task, compile_constraints, topological_order
from agentgranule.algorithms import validate_output


class PlanningTests(unittest.TestCase):
    def test_order_is_deterministic_and_dependencies_precede_consumers(self):
        a, b = Task("a", "module-a", "analysis"), Task("b", "module-b", "analysis")
        c = Task("c", "module-c", "report", ["b", "a"])
        self.assertEqual([task.id for task in topological_order([c, b, a])], ["a", "b", "c"])
        self.assertEqual(topological_order([a, b, c]), topological_order([c, b, a]))

    def test_invalid_graphs_are_rejected(self):
        graphs = [[], [Task("a", "m", "x"), Task("a", "n", "x")],
                  [Task("a", "m", "x", ["missing"])], [Task("a", "m", "x", ["a"])],
                  [Task("a", "m", "x", ["b"]), Task("b", "n", "x", ["a"])]]
        for graph in graphs:
            with self.subTest(graph=graph), self.assertRaises(GranuleError):
                topological_order(graph)
        with self.assertRaises(GranuleError):
            Task("a", "m", "x", ["b", "b"])

    def test_long_graph_sort_does_not_depend_on_python_recursion(self):
        tasks = [Task(str(i), str(i), "analysis", [str(i - 1)] if i else []) for i in range(1500)]
        self.assertEqual([task.id for task in topological_order(reversed(tasks))], [str(i) for i in range(1500)])

    def test_compiler_preserves_non_numeric_and_custom_detail(self):
        parameters = {"detail_level": "domain-specific", "max_depth": 2, "audience": {"type": "expert"}}
        compiled = compile_constraints(parameters)
        self.assertIsNone(compiled["item_count"])
        self.assertEqual(compiled["detail_level"], "domain-specific")
        self.assertEqual(compiled["custom"], {"audience": {"type": "expert"}})
        parameters["audience"]["type"] = "changed"
        self.assertEqual(compiled["custom"]["audience"]["type"], "expert")

    def test_cardinality_and_detail_tree_boundary(self):
        constraints = compile_constraints({"count": 2, "max_depth": 2, "detail_level": "detailed"})
        output = {"items": ["a", "b"], "details": [{"text": "root", "children": [{"text": "child"}]}]}
        validate_output(output, constraints)
        output["details"][0]["children"][0]["children"] = [{"text": "too deep"}]
        with self.assertRaisesRegex(GranuleError, "max_depth=2"):
            validate_output(output, constraints)
        with self.assertRaisesRegex(GranuleError, "exactly 2"):
            validate_output({"items": ["one"]}, constraints)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.project = Project(":memory:")
        self.session = self.project.create_session("事件评估")
        self.modules = {name: self.project.add_module(self.session, name) for name in ["a", "b", "c"]}
        self.tasks = [Task("a", self.modules["a"], "advantages"), Task("b", self.modules["b"], "disadvantages"),
                      Task("c", self.modules["c"], "explanation", ["a", "b"])]
        self.calls = {name: 0 for name in self.modules}
        self.runner = AlgorithmRunner(self.project)

        def enumerator(request):
            name = request["task"]["id"]
            self.calls[name] += 1
            return {"items": [f"{name}-{i}" for i in range(request["constraints"]["item_count"])]}

        def reporter(request):
            self.calls["c"] += 1
            return {"text": "; ".join(f"{name}={len(output['items'])}" for name, output in request["inputs"].items())}

        self.enumerator, self.reporter = enumerator, reporter
        self.runner.register("advantages", enumerator, version="1")
        self.runner.register("disadvantages", enumerator, version="1")
        self.runner.register("explanation", reporter, version="1")

    def tearDown(self):
        self.project.close()

    def run_graph(self, **kwargs):
        return self.runner.run(self.session, self.tasks, **kwargs)

    def test_first_run_executes_and_records_actual_inputs_and_outputs(self):
        report = self.run_graph()
        self.assertTrue(report["complete"])
        self.assertEqual(self.calls, {"a": 1, "b": 1, "c": 1})
        self.assertEqual(report["tasks"]["c"]["output"], {"text": "a=3; b=3"})
        events = self.project.history(self.session)
        starts = [event for event in events if event["kind"] == "algorithm.task_started"]
        self.assertEqual(starts[-1]["payload"]["request"]["inputs"]["a"]["items"], ["a-0", "a-1", "a-2"])
        self.assertEqual(events[-1]["kind"], "algorithm.run_finished")

    def test_repeat_run_reuses_all_successful_tasks(self):
        self.run_graph()
        report = self.run_graph()
        self.assertTrue(report["complete"])
        self.assertTrue(all(task["status"] == "cached" for task in report["tasks"].values()))
        self.assertEqual(self.calls, {"a": 1, "b": 1, "c": 1})

    def test_human_override_recomputes_only_affected_dependency_closure(self):
        self.run_graph()
        self.project.set_granularity(self.modules["a"], actor="human", direction="advantages", parameters={"count": 2})
        report = self.run_graph()
        self.assertEqual([report["tasks"][name]["status"] for name in ["a", "b", "c"]], ["completed", "cached", "completed"])
        self.assertEqual(self.calls, {"a": 2, "b": 1, "c": 2})
        self.assertEqual(report["tasks"]["c"]["output"]["text"], "a=2; b=3")

    def test_default_change_does_not_invalidate_explicit_override(self):
        self.project.set_granularity(self.modules["a"], actor="human", direction="advantages", parameters={"count": 2})
        self.run_graph()
        self.project.set_default_granularity(self.session, "advantages", {"count": 5}, "human")
        self.project.set_default_granularity(self.session, "disadvantages", {"count": 4}, "human")
        report = self.run_graph()
        self.assertEqual(report["tasks"]["a"]["status"], "cached")
        self.assertEqual(report["tasks"]["c"]["output"]["text"], "a=2; b=4")
        self.assertEqual(self.calls, {"a": 1, "b": 2, "c": 2})

    def test_unrelated_direction_change_preserves_cache(self):
        self.run_graph()
        self.project.set_granularity(self.modules["a"], actor="human", direction="explanation", parameters={"detail_level": "detailed"})
        self.run_graph()
        self.assertEqual(self.calls, {"a": 1, "b": 1, "c": 1})

    def test_handler_version_and_context_changes_invalidate_cache(self):
        self.run_graph(context={"question": "first"})
        self.runner.register("advantages", self.enumerator, version="2")
        self.run_graph(context={"question": "first"})
        self.assertEqual(self.calls, {"a": 2, "b": 1, "c": 2})
        self.run_graph(context={"question": "second"})
        self.assertEqual(self.calls, {"a": 3, "b": 2, "c": 3})
        with self.assertRaisesRegex(GranuleError, "new version"):
            self.runner.register("advantages", lambda request: {"items": []}, version="2")

    def test_dependency_edge_change_recomputes_consumer(self):
        self.run_graph()
        self.tasks[-1] = Task("c", self.modules["c"], "explanation", ["a"])
        report = self.run_graph()
        self.assertEqual(report["tasks"]["c"]["output"]["text"], "a=3")
        self.assertEqual(self.calls, {"a": 1, "b": 1, "c": 2})

    def test_failed_task_blocks_dependents_but_keeps_independent_success(self):
        def fail(request):
            raise RuntimeError("solver failed")

        self.runner.register("advantages", fail, version="failure")
        report = self.run_graph()
        self.assertFalse(report["complete"])
        self.assertEqual([report["tasks"][name]["status"] for name in ["a", "b", "c"]], ["failed", "completed", "blocked"])
        self.runner.register("advantages", self.enumerator, version="repaired")
        report = self.run_graph()
        self.assertTrue(report["complete"])
        self.assertEqual(report["tasks"]["b"]["status"], "cached")
        self.assertEqual(self.calls, {"a": 1, "b": 1, "c": 1})

    def test_count_and_depth_failures_are_recorded_and_not_cached(self):
        def too_deep(request):
            return {"text": "report", "details": [{"text": "root", "children": [{"text": "child"}]}]}

        self.project.set_granularity(self.modules["c"], actor="human", direction="explanation", parameters={"max_depth": 1})
        self.runner.register("explanation", too_deep, version="depth")
        report = self.run_graph()
        self.assertEqual(report["tasks"]["c"]["status"], "failed")
        self.assertIn("max_depth=1", report["tasks"]["c"]["error"])
        self.runner.register("advantages", lambda request: {"items": ["one"]}, version="wrong-count")
        report = self.run_graph()
        self.assertEqual(report["tasks"]["a"]["status"], "failed")
        failures = [event for event in self.project.history(self.session) if event["kind"] == "algorithm.task_failed"]
        self.assertEqual(failures[-1]["payload"]["output"], {"items": ["one"]})

    def test_mid_execution_human_change_rejects_own_result(self):
        def mutate(request):
            self.project.set_granularity(self.modules["a"], actor="human", direction="advantages", parameters={"count": 2})
            return {"items": ["one", "two", "three"]}

        self.runner.register("advantages", mutate, version="mutates")
        report = self.run_graph()
        self.assertEqual(report["tasks"]["a"]["status"], "failed")
        self.assertIn("Granularity changed", report["tasks"]["a"]["error"])
        self.assertEqual(report["tasks"]["c"]["status"], "blocked")

    def test_dependency_change_inside_consumer_is_detected(self):
        def mutate_upstream(request):
            self.project.set_granularity(self.modules["a"], actor="human", direction="advantages", parameters={"count": 5})
            return {"text": "must not deliver"}

        self.runner.register("explanation", mutate_upstream, version="mutates")
        report = self.run_graph()
        self.assertFalse(report["complete"])
        self.assertEqual(report["tasks"]["c"]["status"], "failed")
        self.assertEqual(report["tasks"]["a"]["status"], "stale")
        self.assertNotIn("a", report["outputs"])
        self.assertNotIn("c", report["outputs"])
        self.assertIn("b", report["outputs"])

    def test_invalid_graph_missing_handler_and_cross_session_are_preflight_errors(self):
        self.tasks[0] = Task("a", self.modules["a"], "unknown")
        with self.assertRaisesRegex(GranuleError, "No handler"):
            self.run_graph()
        other = self.project.create_session("other")
        foreign = self.project.add_module(other, "foreign")
        with self.assertRaisesRegex(GranuleError, "supplied session"):
            self.runner.run(self.session, [Task("x", foreign, "advantages")])
        with self.assertRaisesRegex(GranuleError, "cycle"):
            self.runner.run(self.session, [Task("a", self.modules["a"], "advantages", ["a"])])
        self.assertEqual(self.calls, {"a": 0, "b": 0, "c": 0})

    def test_handler_cannot_mutate_upstream_outputs_or_authoritative_plan(self):
        def mutate_request(request):
            request["inputs"]["a"]["items"].clear()
            request["plan"]["parameters"]["count"] = 99
            return {"text": "independent result"}

        self.runner.register("explanation", mutate_request, version="mutation-test")
        report = self.run_graph()
        self.assertTrue(report["complete"])
        self.assertEqual(len(report["tasks"]["a"]["output"]["items"]), 3)
        self.assertNotIn("count", report["tasks"]["c"]["plan"]["parameters"])

    def test_non_json_handler_output_does_not_abort_unrelated_tasks(self):
        self.runner.register("advantages", lambda request: {"items": {"set"}}, version="invalid-json")
        report = self.run_graph()
        self.assertEqual(report["tasks"]["a"]["status"], "failed")
        self.assertEqual(report["tasks"]["b"]["status"], "completed")
        self.assertEqual(report["tasks"]["a"]["output"], {"unrecordable": "dict"})

    def test_cache_survives_database_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "execution.sqlite3"
            calls = []

            def handler(request):
                calls.append(request)
                return {"text": "persistent output"}

            with Project(database) as project:
                session = project.create_session("persistent")
                module = project.add_module(session, "analysis")
                task = Task("analysis", module, "analysis")
                runner = AlgorithmRunner(project)
                runner.register("analysis", handler, version="1")
                self.assertTrue(runner.run(session, [task])["complete"])
            with Project(database) as project:
                runner = AlgorithmRunner(project)
                runner.register("analysis", handler, version="1")
                report = runner.run(session, [task])
                self.assertEqual(report["tasks"]["analysis"]["status"], "cached")
            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
