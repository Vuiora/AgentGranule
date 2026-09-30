import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from agentgranule import GranuleError, Project
from agentgranule.workflow import Workflow


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "project.sqlite3"
        self.project = Project(self.db)
        self.addCleanup(self.project.close)
        self.session = self.project.create_session("整体任务")
        self.modules = {n: self.project.add_module(self.session, n) for n in "abc"}
        self.tasks = [{"id": n, "module_id": self.modules[n],
                       "direction": "explanation" if n == "c" else "advantages",
                       "depends_on": ["a", "b"] if n == "c" else []} for n in "abc"]
        self.engine = Workflow(self.project)
        self.wid = self.engine.create_workflow(self.session, self.tasks, {"fact": "事实"})["workflow_id"]

    def finish(self):
        while True:
            report = self.engine.next_task(self.wid)
            request = report["request"]
            if request is None:
                self.assertTrue(report["complete"])
                return report
            output = ({"items": [str(i) for i in range(request["constraints"]["item_count"])]}
                      if request["constraints"]["item_count"] else {"text": "真实总结"})
            self.engine.submit_task(self.wid, request["request_id"], output)

    def test_full_graph_and_stable_request(self):
        req = self.engine.next_task(self.wid)["request"]
        self.assertEqual(req, self.engine.next_task(self.wid)["request"])
        self.assertEqual(req["context"], {"fact": "事实"})
        report = self.engine.submit_task(self.wid, req["request_id"], {"items": ["1", "2", "3"]})
        self.assertEqual(report["statuses"], {"a": "completed", "b": "ready", "c": "blocked"})
        self.assertEqual(self.finish()["order"], list("abc"))
        self.assertEqual(len(self.engine.workflow_status(self.wid)["outputs"]), 3)

    def test_invalid_output_logged_and_request_retryable(self):
        req = self.engine.next_task(self.wid)["request"]
        with self.assertRaisesRegex(GranuleError, "exactly 3"):
            self.engine.submit_task(self.wid, req["request_id"], {"items": ["1"]})
        event = self.project.history(self.session)[-1]
        self.assertFalse(event["payload"]["accepted"])
        self.assertEqual(event["payload"]["output"], {"items": ["1"]})
        self.assertEqual(req, self.engine.next_task(self.wid)["request"])
        self.finish()

    def test_depth_rejection(self):
        self.project.set_granularity(self.modules["a"], direction="advantages", actor="human",
                                     parameters={"count": 1, "max_depth": 1})
        req = self.engine.next_task(self.wid)["request"]
        with self.assertRaisesRegex(GranuleError, "max_depth"):
            self.engine.submit_task(self.wid, req["request_id"], {
                "items": ["x"], "details": [{"text": "root", "children": [{"text": "child"}]}]})

    def test_change_invalidates_descendants_only(self):
        self.finish()
        self.project.set_granularity(self.modules["a"], direction="advantages", actor="human", parameters={"count": 2})
        report = self.engine.workflow_status(self.wid)
        self.assertEqual(set(report["outputs"]), {"b"})
        self.assertEqual(report["statuses"], {"a": "ready", "b": "completed", "c": "blocked"})
        self.assertEqual(len(self.finish()["outputs"]["a"]["items"]), 2)

    def test_upstream_change_rejects_dispatched_summary(self):
        for _ in range(2):
            req = self.engine.next_task(self.wid)["request"]
            self.engine.submit_task(self.wid, req["request_id"], {"items": ["1", "2", "3"]})
        req = self.engine.next_task(self.wid)["request"]
        self.assertEqual(set(req["inputs"]), {"a", "b"})
        self.project.set_granularity(self.modules["a"], actor="human", direction="advantages", parameters={"count": 2})
        with self.assertRaisesRegex(GranuleError, "stale request"):
            self.engine.submit_task(self.wid, req["request_id"], {"text": "旧总结"})
        self.assertEqual(set(self.engine.workflow_status(self.wid)["outputs"]), {"b"})

    def test_project_default_change_and_override(self):
        self.project.set_granularity(self.modules["a"], actor="human", direction="advantages", parameters={"count": 2})
        self.finish()
        self.project.set_default_granularity(self.session, "advantages", {"count": 4}, "human")
        self.assertEqual(set(self.engine.workflow_status(self.wid)["outputs"]), {"a"})
        self.assertEqual(len(self.finish()["outputs"]["b"]["items"]), 4)

    def test_restart_recovers_dispatch_and_completed_results(self):
        req = self.engine.next_task(self.wid)["request"]
        with Project(self.db) as reopened:
            engine = Workflow(reopened)
            self.assertEqual(engine.next_task(self.wid)["request"], req)
            engine.submit_task(self.wid, req["request_id"], {"items": ["1", "2", "3"]})
        self.assertEqual(self.engine.workflow_status(self.wid)["statuses"]["a"], "completed")

    def test_concurrent_duplicate_acceptance_is_atomic(self):
        req = self.engine.next_task(self.wid)["request"]
        def submit(_):
            with Project(self.db) as project:
                try:
                    Workflow(project).submit_task(self.wid, req["request_id"], {"items": ["1", "2", "3"]})
                    return True
                except GranuleError:
                    return False
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(submit, range(2))), [False, True])

    def test_invalid_graph_and_cross_session_have_no_workflow_events(self):
        before = self.project.history(self.session)
        foreign = self.project.add_module(self.project.create_session("other"), "foreign")
        for tasks in ([], [{"id": "x", "module_id": foreign, "direction": "analysis"}],
                      [{**self.tasks[0], "depends_on": ["missing"]}], [{"unknown": "field"}]):
            with self.subTest(tasks=tasks), self.assertRaises(GranuleError):
                self.engine.create_workflow(self.session, tasks)
        self.assertEqual(before, self.project.history(self.session))


if __name__ == "__main__":
    unittest.main()
