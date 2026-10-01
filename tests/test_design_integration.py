"""Real CLI/MCP design approval and allocation journeys using isolated databases."""

import asyncio
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]


def proposed_graph():
    return {
        "summary": "将材料分成分类、依赖总结和独立校验三个模块。",
        "modules": [
            {"id": "a", "name": "材料分类", "description": "根据提供的材料分类",
             "expected_output": "两个分类项", "basis": "输入材料中的两个主题",
             "directions": ["classification"], "parent_id": None, "depends_on": []},
            {"id": "b", "name": "分类总结", "description": "解释材料分类结果",
             "expected_output": "分类结果的说明", "basis": "依赖材料分类的输出",
             "directions": ["explanation"], "parent_id": "a", "depends_on": ["a"]},
            {"id": "c", "name": "独立校验", "description": "校验来源材料",
             "expected_output": "来源材料的校验说明", "basis": "直接使用来源材料",
             "directions": ["explanation"], "parent_id": None, "depends_on": []},
        ],
        "context": {"facts": ["第一主题", "第二主题"]},
    }


def allocation_choices(snapshot, classification_effort=0.37):
    return [
        {"module_id": control["module_id"], "direction": control["direction"],
         "design_effort": classification_effort if control["direction"] == "classification" else 0.01}
        for control in snapshot["controls"]
    ]


class DesignIntegrationTests(unittest.TestCase):
    def test_real_cli_approval_allocation_and_invalid_batches(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "cli-design.sqlite3")

            def call(operation, expect_error=False, **arguments):
                process = subprocess.run(
                    [sys.executable, "-m", "agentgranule", "--database", database,
                     operation, json.dumps(arguments, ensure_ascii=False)],
                    capture_output=True, text=True, encoding="utf-8", timeout=15,
                    cwd=ROOT, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
                if expect_error:
                    self.assertEqual(process.returncode, 2, process.stdout + process.stderr)
                    self.assertIn("error", json.loads(process.stderr))
                    self.assertFalse(process.stdout.strip())
                    return None
                self.assertEqual(process.returncode, 0, process.stderr)
                return json.loads(process.stdout)["result"]

            session = call("create_session", title="CLI模块设计测试")
            source_root = Path(directory) / "source"
            source_root.mkdir()
            (source_root / "alpha.py").write_text("from beta import describe\ndef classify(value):\n    return describe(value)\n", encoding="utf-8")
            (source_root / "beta.py").write_text("def describe(value):\n    return str(value)\n", encoding="utf-8")
            scanned = call("analyze_framework", root_path=str(source_root))
            self.assertEqual(scanned["source_root"], str(source_root.resolve()))
            self.assertGreaterEqual(scanned["module_count"], 2)
            self.assertEqual(scanned["module_count"], len(scanned["analysis"]["modules"]))
            proposal = call("propose_analysis", session_id=session, analysis=proposed_graph())
            analysis_id = proposal["analysis_id"]
            self.assertEqual(proposal["state"], "proposed")
            call("allocation_snapshot", analysis_id=analysis_id, expect_error=True)
            call("save_allocation", snapshot={"analysis_id": analysis_id, "revision": proposal["revision"],
                 "workflow_id": None, "controls": []}, choices=[], actor="test-human", expect_error=True)

            invalid = proposed_graph()
            invalid["modules"][0]["depends_on"] = ["b"]
            call("update_analysis", analysis_id=analysis_id, expected_revision=proposal["revision"],
                 analysis=invalid, expect_error=True)
            self.assertEqual(call("get_analysis", analysis_id=analysis_id), proposal)

            edited = proposed_graph()
            edited["modules"][1]["name"] = "人工修改后的分类总结"
            updated = call("update_analysis", analysis_id=analysis_id,
                           expected_revision=proposal["revision"], analysis=edited)
            self.assertGreater(updated["revision"], proposal["revision"])
            call("approve_analysis", analysis_id=analysis_id, expected_revision=proposal["revision"],
                 actor="test-human", expect_error=True)
            approved = call("approve_analysis", analysis_id=analysis_id,
                            expected_revision=updated["revision"], actor="test-human")
            self.assertEqual(approved["state"], "approved")
            self.assertEqual(len(approved["module_ids"]), 3)
            self.assertIsNone(approved["workflow_id"])

            snapshot = call("allocation_snapshot", analysis_id=analysis_id)
            self.assertEqual(len(snapshot["controls"]), 3)
            invalid_snapshot = copy.deepcopy(snapshot)
            invalid_snapshot["revision"] -= 1
            call("save_allocation", snapshot=invalid_snapshot, choices=allocation_choices(snapshot),
                 actor="test-human", expect_error=True)
            self.assertEqual(call("allocation_snapshot", analysis_id=analysis_id), snapshot)
            invalid_choices = allocation_choices(snapshot)
            invalid_choices[0]["design_effort"] = 0.375
            call("save_allocation", snapshot=snapshot, choices=invalid_choices,
                 actor="test-human", expect_error=True)
            self.assertEqual(call("allocation_snapshot", analysis_id=analysis_id), snapshot)

            saved = call("save_allocation", snapshot=snapshot, choices=allocation_choices(snapshot),
                         actor="test-human")
            self.assertEqual(saved["status"], "saved")
            workflow_id = saved["workflow_id"]
            self.assertTrue(workflow_id)
            for _ in range(3):
                request = call("next_task", workflow_id=workflow_id)["request"]
                self.assertIsNotNone(request)
                output = {"items": ["第一类", "第二类", "其他类"]} if request["task"]["direction"] == "classification" else {"text": "已核对材料。"}
                call("submit_task", workflow_id=workflow_id, request_id=request["request_id"], output=output)
            report = call("workflow_status", workflow_id=workflow_id)
            self.assertTrue(report["complete"])
            self.assertEqual(len(report["outputs"]), 3)
            self.assertIsNone(call("next_task", workflow_id=workflow_id)["request"])

    def test_real_mcp_stdio_restart_and_partial_recalculation(self):
        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(self._mcp_scenario(str(Path(directory) / "mcp-design.sqlite3")))

    async def _mcp_scenario(self, database):
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "agentgranule.mcp_server", "--database", database],
            env={**os.environ, "PYTHONIOENCODING": "utf-8"})

        async def call(client, name, expect_error=False, **arguments):
            result = await client.call_tool(name, arguments)
            if expect_error:
                self.assertTrue(result.isError, result.content)
                return None
            self.assertFalse(result.isError, result.content)
            self.assertIsInstance(result.structuredContent, dict)
            return result.structuredContent

        async with asyncio.timeout(75):
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as client:
                    await client.initialize()
                    tools = (await client.list_tools()).tools
                    self.assertEqual(len(tools), 19)
                    self.assertTrue(all(tool.outputSchema for tool in tools))
                    self.assertTrue({"propose_analysis", "update_analysis", "get_analysis", "approve_analysis",
                                     "allocation_snapshot", "save_allocation"}.issubset({tool.name for tool in tools}))
                    session = (await call(client, "create_session", title="MCP模块设计测试"))["result"]
                    await call(client, "record_message", session_id=session, role="user", content="请分析材料的模块，我再确认设计力度。")
                    proposal = await call(client, "propose_analysis", session_id=session, analysis=proposed_graph())
                    analysis_id = proposal["analysis_id"]
                    await call(client, "allocation_snapshot", analysis_id=analysis_id, expect_error=True)

                    invalid = proposed_graph()
                    invalid["modules"][0]["parent_id"] = "b"
                    await call(client, "update_analysis", analysis_id=analysis_id, expected_revision=proposal["revision"],
                               analysis=invalid, expect_error=True)
                    self.assertEqual(await call(client, "get_analysis", analysis_id=analysis_id), proposal)
                    edited = proposed_graph()
                    edited["summary"] = "操作者已修订模块分析说明。"
                    updated = await call(client, "update_analysis", analysis_id=analysis_id,
                                         expected_revision=proposal["revision"], analysis=edited)
                    approved = await call(client, "approve_analysis", analysis_id=analysis_id,
                                          expected_revision=updated["revision"], actor="test-human")
                    module_a = approved["module_ids"]["a"]
                    module_b = approved["module_ids"]["b"]
                    module_c = approved["module_ids"]["c"]
                    await call(client, "set_granularity", problem_id=module_a, direction="classification",
                               parameters={"count": 2, "detail_level": "brief", "max_depth": 2}, actor="test-human")
                    snapshot = await call(client, "allocation_snapshot", analysis_id=analysis_id)
                    # Defaults still supply the unallocated explanation controls.
                    await call(client, "set_default_granularity", session_id=session, direction="explanation",
                               parameters={"detail_level": "detailed"}, actor="test-human")
                    await call(client, "save_allocation", snapshot=snapshot,
                               choices=allocation_choices(snapshot), actor="test-human", expect_error=True)
                    self.assertNotIn("design_effort", (await call(client, "get_granularity", problem_id=module_a,
                                                                  direction="classification"))["parameters"])
                    snapshot = await call(client, "allocation_snapshot", analysis_id=analysis_id)
                    saved = await call(client, "save_allocation", snapshot=snapshot,
                                       choices=allocation_choices(snapshot), actor="test-human")
                    workflow_id = saved["workflow_id"]
                    self.assertEqual(saved["status"], "saved")
                    state = await call(client, "get_granularity", problem_id=module_a, direction="classification")
                    self.assertEqual(state["parameters"], {"count": 2, "detail_level": "brief", "max_depth": 2, "design_effort": 0.37})
                    task_for_module = {}
                    for _ in range(3):
                        request = (await call(client, "next_task", workflow_id=workflow_id))["request"]
                        self.assertIsNotNone(request)
                        task_for_module[request["task"]["module_id"]] = request["task"]["id"]
                        if request["task"]["module_id"] == module_a:
                            self.assertEqual(request["constraints"]["design_effort"], 0.37)
                            self.assertEqual(request["constraints"]["item_count"], 2)
                            output = {"items": ["第一主题", "第二主题"]}
                        else:
                            self.assertEqual(request["constraints"]["design_effort"], 0.01)
                            if request["task"]["module_id"] == module_b:
                                self.assertEqual(len(request["inputs"]), 1)
                                self.assertEqual(next(iter(request["inputs"].values()))["items"], ["第一主题", "第二主题"])
                            output = {"text": "基于材料的说明。"}
                        await call(client, "submit_task", workflow_id=workflow_id,
                                   request_id=request["request_id"], output=output)
                    completed = await call(client, "workflow_status", workflow_id=workflow_id)
                    self.assertTrue(completed["complete"])

            # A separate process reads the accepted graph and its workflow/receipts.
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as client:
                    await client.initialize()
                    resumed = await call(client, "get_analysis", analysis_id=analysis_id)
                    self.assertEqual(resumed["state"], "approved")
                    self.assertEqual(resumed["analysis"], edited)
                    self.assertEqual(resumed["workflow_id"], workflow_id)
                    self.assertEqual(await call(client, "workflow_status", workflow_id=workflow_id), completed)
                    snapshot = await call(client, "allocation_snapshot", analysis_id=analysis_id)
                    saved = await call(client, "save_allocation", snapshot=snapshot,
                                       choices=allocation_choices(snapshot, classification_effort=0.38), actor="test-human")
                    self.assertEqual(saved["workflow_id"], workflow_id)
                    self.assertEqual(set(saved["affected_task_ids"]), {task_for_module[module_a], task_for_module[module_b]})
                    self.assertEqual(saved["report"]["outputs"], {task_for_module[module_c]: completed["outputs"][task_for_module[module_c]]})
                    self.assertEqual(saved["report"]["statuses"][task_for_module[module_c]], "completed")
                    self.assertFalse(saved["report"]["complete"])
                    fresh = await call(client, "allocation_snapshot", analysis_id=analysis_id)
                    await call(client, "save_allocation", snapshot=snapshot,
                               choices=allocation_choices(snapshot, classification_effort=0.99), actor="test-human", expect_error=True)
                    self.assertEqual(await call(client, "allocation_snapshot", analysis_id=analysis_id), fresh)

                    # One changed control rejects the whole batch, including otherwise-fresh choices.
                    c_control = await call(client, "get_granularity", problem_id=module_c, direction="explanation")
                    await call(client, "set_granularity", problem_id=module_c, direction="explanation",
                               parameters={**c_control["parameters"], "design_effort": 0.02}, actor="test-human")
                    await call(client, "save_allocation", snapshot=fresh,
                               choices=allocation_choices(fresh, classification_effort=0.99), actor="test-human", expect_error=True)
                    self.assertEqual((await call(client, "get_granularity", problem_id=module_a,
                                                 direction="classification"))["parameters"]["design_effort"], 0.38)
                    before = await call(client, "allocation_snapshot", analysis_id=analysis_id)
                    duplicate = allocation_choices(before)
                    duplicate[1] = dict(duplicate[0])
                    await call(client, "save_allocation", snapshot=before, choices=duplicate,
                               actor="test-human", expect_error=True)
                    self.assertEqual(await call(client, "allocation_snapshot", analysis_id=analysis_id), before)
                    events = (await call(client, "history", session_id=session))["result"]
                    self.assertTrue(any(event["kind"] == "workflow.invalidated" for event in events))
                    self.assertIn("请分析材料的模块，我再确认设计力度。",
                                  [event["payload"]["content"] for event in events if event["kind"] == "conversation.message"])

    def test_cancel_and_gui_failure_do_not_approve_proposal(self):
        from agentgranule.core import Project
        from agentgranule.design import Design
        from agentgranule.design_view import main

        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "ui-design.sqlite3")
            with Project(database) as project:
                session = project.create_session("取消与失败的隔离测试")
                proposal = Design(project).propose_analysis(session, proposed_graph())
            cancelled_path = Path(directory) / "cancelled.json"
            cancelled_args = ["--database", database, "--analysis-id", proposal["analysis_id"],
                              "--output-file", str(cancelled_path)]
            with patch("agentgranule.design_view.show_design", return_value={"status": "cancelled"}), redirect_stdout(io.StringIO()):
                self.assertEqual(main(cancelled_args), 0)
            self.assertEqual(json.loads(cancelled_path.read_text(encoding="utf-8")), {"status": "cancelled"})
            failed_path = Path(directory) / "failed.json"
            with patch("agentgranule.design_view.show_design", side_effect=RuntimeError("GUI unavailable")), redirect_stdout(io.StringIO()) as stdout, redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(main(["--database", database, "--analysis-id", proposal["analysis_id"],
                                       "--output-file", str(failed_path)]), 2)
            self.assertFalse(failed_path.exists())
            self.assertFalse(stdout.getvalue().strip())
            self.assertIn("GUI unavailable", json.loads(stderr.getvalue())["error"])
            with Project(database) as project:
                self.assertEqual(Design(project).get_analysis(proposal["analysis_id"]), proposal)
                self.assertEqual(project._db.execute("SELECT COUNT(*) FROM problems WHERE session_id=?", (session,)).fetchone()[0], 0)
                self.assertEqual(project._db.execute("SELECT COUNT(*) FROM workflows WHERE session_id=?", (session,)).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
