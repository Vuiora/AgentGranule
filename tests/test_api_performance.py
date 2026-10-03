"""Query-count regressions for public APIs, with their consistency guarantees.

All data is an isolated synthetic fixture. Query budgets catch N+1 regressions
without tying correctness or performance to the speed of a particular machine.
"""

import hashlib
import json
import sqlite3
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from agentgranule import GranuleError, Project
from agentgranule.design import Design
from agentgranule.workflow import Workflow, WORKFLOW_VERSION


class ApiPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.database = Path(self.tmp.name) / "isolated-api.sqlite3"
        self.project = Project(self.database)
        self.addCleanup(self.project.close)
        self.session = self.project.create_session("Synthetic performance test")

    def measured(self, operation):
        statements = []
        self.project._db.set_trace_callback(statements.append)
        try:
            result = operation()
        finally:
            self.project._db.set_trace_callback(None)
        counts = Counter()
        for statement in statements:
            keyword = statement.lstrip().split(None, 1)[0].upper()
            # Batch readers may express their input table as a CTE.
            counts["SELECT" if keyword == "WITH" else keyword] += 1
        return result, counts

    def analysis(self, count=3, directions=("explanation", "classification")):
        design = Design(self.project)
        proposal = design.propose_analysis(self.session, {
            "summary": "Synthetic module graph for API consistency testing",
            "modules": [{"id": f"m{i:04d}", "name": f"Module {i}",
                         "description": "Synthetic input", "expected_output": "Synthetic output",
                         "basis": "Isolated test fixture", "directions": list(directions)}
                        for i in range(count)],
        })
        approved = design.approve_analysis(proposal["analysis_id"], proposal["revision"], "human:test-fixture")
        return design, approved

    @staticmethod
    def choices(snapshot, effort=0.37):
        return [{"module_id": control["module_id"], "direction": control["direction"],
                 "design_effort": effort} for control in snapshot["controls"]]

    def workflow(self, count=3):
        modules = [self.project.add_module(self.session, f"Synthetic module {i}") for i in range(count)]
        engine = Workflow(self.project)
        tasks = [{"id": f"t{i:04d}", "module_id": module, "direction": "explanation"}
                 for i, module in enumerate(modules)]
        return engine, engine.create_workflow(self.session, tasks)["workflow_id"], modules

    def test_large_multidirection_snapshot_batches_reads_and_preserves_values(self):
        # Exercise the older SQLite binding ceiling even on modern builds.
        self.project._db.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
        self.assertEqual(self.project._db.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER), 999)
        design, approved = self.analysis(551, ("classification", "explanation", "design"))
        self.project.set_default_granularity(self.session, "explanation", {
            "detail_level": "brief", "audience": {"segments": ["technical"]}}, "human:test-fixture")
        selected = approved["module_ids"]["m0000"]
        self.project.set_granularity(selected, direction="design", parameters={"design_effort": 0.01},
                                     actor="human:test-fixture")
        snapshot, counts = self.measured(lambda: design.allocation_snapshot(approved["analysis_id"]))
        self.assertEqual(len(snapshot["controls"]), 1653)
        self.assertLessEqual(counts["SELECT"], 8, "Snapshot reads must grow by bounded batches, not module count")
        expected = []
        for module in sorted(approved["analysis"]["modules"], key=lambda module: module["id"]):
            module_id = approved["module_ids"][module["id"]]
            for direction in sorted(module["directions"]):
                expected.append({**self.project.get_granularity(module_id, direction), "module_id": module_id})
        self.assertEqual(snapshot["controls"], expected)
        inherited = [control for control in snapshot["controls"] if control["source"] == "project_default"]
        inherited[0]["parameters"]["audience"]["segments"].append("changed only in the returned object")
        self.assertEqual(inherited[1]["parameters"]["audience"]["segments"], ["technical"])
        self.assertEqual(self.project.get_granularity(inherited[0]["module_id"], "explanation")
                         ["parameters"]["audience"]["segments"], ["technical"])

    def test_large_workflow_polling_batches_reads_and_does_not_rewrite_payload(self):
        engine, workflow_id, _ = self.workflow(1001)
        status, status_counts = self.measured(lambda: engine.workflow_status(workflow_id))
        self.assertEqual(len(status["statuses"]), 1001)
        self.assertTrue(all(state == "ready" for state in status["statuses"].values()))
        self.assertLessEqual(status_counts["SELECT"], 6)
        self.assertEqual(status_counts["UPDATE"], 0)
        dispatched, dispatch_counts = self.measured(lambda: engine.next_task(workflow_id))
        self.assertEqual(dispatch_counts["UPDATE"], 1)
        history = self.project.history(self.session)
        repeated, repeated_counts = self.measured(lambda: engine.next_task(workflow_id))
        self.assertEqual(repeated["request"], dispatched["request"])
        self.assertLessEqual(repeated_counts["SELECT"], 6)
        self.assertEqual(repeated_counts["UPDATE"], 0)
        self.assertEqual(self.project.history(self.session), history)
        with Project(self.database) as reopened:
            self.assertEqual(Workflow(reopened).next_task(workflow_id)["request"], dispatched["request"])

    def test_cross_connection_defaults_reject_stale_allocation_without_partial_writes(self):
        design, approved = self.analysis()
        analysis_id = approved["analysis_id"]
        stale = design.allocation_snapshot(analysis_id)
        with Project(self.database) as writer:
            writer.set_default_granularity(self.session, "explanation", {"design_effort": 0.63}, "human:test-fixture")
        history = self.project.history(self.session)
        with self.assertRaisesRegex(GranuleError, "snapshot changed"):
            design.save_allocation(stale, self.choices(stale), "human:test-fixture")
        current = design.allocation_snapshot(analysis_id)
        inherited = [control for control in current["controls"] if control["direction"] == "explanation"]
        self.assertTrue(all(control["source"] == "project_default" and control["revision"] == 1
                            and control["parameters"]["design_effort"] == 0.63 for control in inherited))
        self.assertEqual(self.project.history(self.session), history)
        self.assertEqual(self.project._db.execute("SELECT count(*) FROM module_controls").fetchone()[0], 0)
        self.assertEqual(self.project._db.execute("SELECT count(*) FROM workflows").fetchone()[0], 0)
        # Identical-valued writes still advance the default revision.
        with Project(self.database) as writer:
            writer.set_default_granularity(self.session, "explanation", {"design_effort": 0.63}, "human:test-fixture")
        with self.assertRaisesRegex(GranuleError, "snapshot changed"):
            design.save_allocation(current, self.choices(current), "human:test-fixture")
        newest = design.allocation_snapshot(analysis_id)
        saved = design.save_allocation(newest, self.choices(newest), "human:test-fixture")
        with Project(self.database) as writer:
            writer.set_default_granularity(self.session, "explanation", {"design_effort": 0.91}, "human:test-fixture")
        pinned = design.allocation_snapshot(analysis_id)
        self.assertTrue(all(control["source"] == "module" and control["parameters"]["design_effort"] == 0.37
                            for control in pinned["controls"]))
        self.assertEqual(set(saved["report"]["statuses"].values()), {"ready"})

    def test_cross_connection_override_of_last_pair_rejects_whole_batch(self):
        design, approved = self.analysis(12)
        snapshot = design.allocation_snapshot(approved["analysis_id"])
        last = snapshot["controls"][-1]
        with Project(self.database) as writer:
            writer.set_granularity(last["module_id"], direction=last["direction"],
                                   parameters={"design_effort": 0.52}, actor="human:test-fixture")
        current = design.allocation_snapshot(approved["analysis_id"])
        history = self.project.history(self.session)
        with self.assertRaisesRegex(GranuleError, "snapshot changed"):
            design.save_allocation(snapshot, self.choices(snapshot), "human:test-fixture")
        self.assertEqual(design.allocation_snapshot(approved["analysis_id"]), current)
        self.assertEqual(self.project.history(self.session), history)
        self.assertEqual(self.project._db.execute("SELECT count(*) FROM module_controls").fetchone()[0], 1)
        self.assertIsNone(design.get_analysis(approved["analysis_id"])["workflow_id"])

    def test_unchanged_poll_is_read_only_but_invalidations_and_dispatch_are_durable(self):
        engine, workflow_id, modules = self.workflow(2)
        request = engine.next_task(workflow_id)["request"]
        _, unchanged = self.measured(lambda: engine.workflow_status(workflow_id))
        self.assertEqual(unchanged["UPDATE"], 0)
        with Project(self.database) as writer:
            writer.set_granularity(modules[0], direction="explanation", parameters={"design_effort": 0.01},
                                   actor="human:test-fixture")
        history_count = len(self.project.history(self.session))
        status, changed = self.measured(lambda: engine.workflow_status(workflow_id))
        self.assertEqual(status["statuses"]["t0000"], "ready")
        self.assertEqual(changed["UPDATE"], 1)
        events = self.project.history(self.session)[history_count:]
        self.assertEqual([event["kind"] for event in events], ["workflow.invalidated"])
        self.assertEqual(events[0]["payload"]["kind"], "requests")
        with Project(self.database) as reopened:
            stored = json.loads(reopened._db.execute("SELECT payload FROM workflows WHERE id=?", (workflow_id,)).fetchone()[0])
            self.assertEqual(stored["requests"], {})
        next_request, dispatched = self.measured(lambda: engine.next_task(workflow_id))
        self.assertEqual(dispatched["UPDATE"], 1)
        self.assertNotEqual(next_request["request"]["request_id"], request["request_id"])
        with Project(self.database) as reopened:
            self.assertEqual(Workflow(reopened).next_task(workflow_id)["request"], next_request["request"])

    def test_submit_reuses_transaction_state_and_recomputes_dependency_fingerprint(self):
        a = self.project.add_module(self.session, "Upstream synthetic fixture")
        b = self.project.add_module(self.session, "Dependent synthetic fixture")
        engine = Workflow(self.project)
        workflow_id = engine.create_workflow(self.session, [
            {"id": "a", "module_id": a, "direction": "explanation"},
            {"id": "b", "module_id": b, "direction": "explanation", "depends_on": ["a"]},
        ], {"source": "synthetic fixture"})["workflow_id"]
        request = engine.next_task(workflow_id)["request"]
        history_count = len(self.project.history(self.session))
        report, counts = self.measured(lambda: engine.submit_task(workflow_id, request["request_id"], {"text": "Accepted fixture"}))
        self.assertLessEqual(counts["SELECT"], 4)
        self.assertEqual(report["statuses"], {"a": "completed", "b": "ready"})
        self.assertEqual([event["kind"] for event in self.project.history(self.session)[history_count:]],
                         ["workflow.result_submitted"])
        dependent = engine.next_task(workflow_id)["request"]
        provenance = {"workflow_version": WORKFLOW_VERSION, "task": dependent["task"],
                      "state": self.project.get_granularity(b, "explanation"), "context": dependent["context"],
                      "dependencies": {"a": request["request_id"]}}
        fingerprint = hashlib.sha256(json.dumps(provenance, ensure_ascii=False, sort_keys=True, allow_nan=False)
                                     .encode("utf-8")).hexdigest()
        self.assertEqual(dependent["fingerprint"], fingerprint)
        self.assertEqual(dependent["inputs"], {"a": {"text": "Accepted fixture"}})
        with Project(self.database) as reopened:
            self.assertEqual(Workflow(reopened).next_task(workflow_id)["request"], dependent)

    def test_stale_submission_keeps_invalidation_and_rejection_event_order(self):
        modules = {name: self.project.add_module(self.session, f"Synthetic {name}") for name in ("a", "x", "z")}
        engine = Workflow(self.project)
        workflow_id = engine.create_workflow(self.session, [
            {"id": name, "module_id": module, "direction": "explanation",
             "depends_on": ["a"] if name == "z" else []} for name, module in modules.items()
        ])["workflow_id"]
        for expected in ("a", "x"):
            request = engine.next_task(workflow_id)["request"]
            self.assertEqual(request["task"]["id"], expected)
            engine.submit_task(workflow_id, request["request_id"], {"text": f"Synthetic {expected} result"})
        request = engine.next_task(workflow_id)["request"]
        self.assertEqual(request["task"]["id"], "z")
        with Project(self.database) as writer:
            writer.set_granularity(modules["a"], direction="explanation", parameters={"design_effort": 0.52},
                                   actor="human:test-fixture")
        history_count = len(self.project.history(self.session))
        with self.assertRaisesRegex(GranuleError, "stale request"):
            engine.submit_task(workflow_id, request["request_id"], {"text": "Stale dependent output"})
        events = self.project.history(self.session)[history_count:]
        self.assertEqual([event["kind"] for event in events],
                         ["workflow.invalidated", "workflow.invalidated", "workflow.result_submitted"])
        self.assertEqual([event["payload"]["task_id"] for event in events[:2]], ["a", "z"])
        self.assertFalse(events[-1]["payload"]["accepted"])
        with Project(self.database) as reopened:
            report = Workflow(reopened).workflow_status(workflow_id)
            self.assertEqual(report["outputs"], {"x": {"text": "Synthetic x result"}})

    def test_batch_save_storage_failure_rolls_back_controls_workflow_and_events(self):
        design, approved = self.analysis(16)
        snapshot = design.allocation_snapshot(approved["analysis_id"])
        history = self.project.history(self.session)
        record = self.project._event

        def fail_after_all_control_and_workflow_writes(session_id, kind, payload):
            if kind == "design.allocation_confirmed":
                raise RuntimeError("Synthetic storage failure")
            record(session_id, kind, payload)

        with patch.object(self.project, "_event", side_effect=fail_after_all_control_and_workflow_writes):
            with self.assertRaisesRegex(RuntimeError, "Synthetic storage failure"):
                design.save_allocation(snapshot, self.choices(snapshot), "human:test-fixture")
        self.assertEqual(design.allocation_snapshot(approved["analysis_id"]), snapshot)
        self.assertEqual(self.project.history(self.session), history)
        self.assertEqual(self.project._db.execute("SELECT count(*) FROM module_controls").fetchone()[0], 0)
        self.assertEqual(self.project._db.execute("SELECT count(*) FROM workflows").fetchone()[0], 0)

    def test_history_index_upgrades_existing_database_without_changing_original_events(self):
        original_messages = ["First visible message\n  preserve whitespace", "第二条原文", ""]
        for message in original_messages:
            self.project.record_message(self.session, "user", message)
            other = self.project.create_session("Unrelated audit session")
            self.project.record_message(other, "tool", "Unrelated fixture result")
        before = self.project.history(self.session)
        # Reproduce an existing version-2 database predating the additive index.
        with self.project._db:
            self.project._db.execute("DROP INDEX events_session_sequence")
        with Project(self.database) as upgraded:
            plan = upgraded._db.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM events WHERE session_id=? ORDER BY sequence",
                (self.session,),
            ).fetchall()
            self.assertTrue(any("events_session_sequence" in row["detail"] and "SEARCH" in row["detail"]
                                for row in plan), "History must use its session index rather than scan all sessions")
            self.assertFalse(any("TEMP B-TREE" in row["detail"] for row in plan))
            self.assertEqual(upgraded.history(self.session), before)
            self.assertEqual([event["payload"]["content"] for event in upgraded.history(self.session)
                              if event["kind"] == "conversation.message"], original_messages)
            self.assertEqual(upgraded._db.execute("PRAGMA user_version").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
