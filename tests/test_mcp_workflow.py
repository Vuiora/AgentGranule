"""Actual official MCP client/server stdio round trips, without model providers."""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]


class MCPWorkflowTests(unittest.TestCase):
    def test_real_stdio_complete_workflow_restart_and_recalculation(self):
        with tempfile.TemporaryDirectory() as directory:
            asyncio.run(self.scenario(str(Path(directory) / "mcp.sqlite3")))

    async def scenario(self, database):
        params = StdioServerParameters(command=sys.executable,
                                      args=["-m", "agentgranule.mcp_server", "--database", database],
                                      env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        async with asyncio.timeout(45):
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as client:
                    await client.initialize()
                    tools = (await client.list_tools()).tools
                    self.assertEqual(len(tools), 19)
                    self.assertTrue(all(tool.outputSchema for tool in tools))
                    async def call(name, **args):
                        result = await client.call_tool(name, args)
                        self.assertFalse(result.isError, result.content)
                        self.assertIsInstance(result.structuredContent, dict)
                        return result.structuredContent
                    session = (await call("create_session", title="评估实际材料"))["result"]
                    await call("record_message", session_id=session, role="user", content="列举两个优点，并做总结。")
                    module = (await call("add_module", session_id=session, description="材料评估"))["result"]
                    question = await call("request_granularity", problem_id=module, direction="advantages")
                    self.assertEqual(question["source"], "builtin_default")
                    await call("record_message", session_id=session, role="assistant", content=question["question"])
                    await call("record_message", session_id=session, role="user", content="优点两项，总结用默认详细程度。")
                    await call("set_granularity", problem_id=module, direction="advantages",
                               parameters={"count": 2, "max_depth": 1, "design_effort": 0.37}, actor="test-human")
                    invalid_effort = await client.call_tool("set_granularity", {
                        "problem_id": module, "direction": "advantages", "parameters": {"design_effort": 0.375}, "actor": "test-human"})
                    self.assertTrue(invalid_effort.isError)
                    self.assertEqual((await call("get_granularity", problem_id=module, direction="advantages"))["parameters"]["design_effort"], 0.37)
                    created = await call("create_workflow", session_id=session, context={"facts": ["速度快", "便于审计"]},
                                         tasks=[{"id": "a", "module_id": module, "direction": "advantages"},
                                                {"id": "z", "module_id": module, "direction": "explanation", "depends_on": ["a"]}])
                    wid = created["workflow_id"]
                    request = (await call("next_task", workflow_id=wid))["request"]
                    self.assertEqual(request["constraints"]["item_count"], 2)
                    self.assertEqual(request["constraints"]["design_effort"], 0.37)
                    bad = await client.call_tool("submit_task", {"workflow_id": wid, "request_id": request["request_id"], "output": {"items": ["缺少一项"]}})
                    self.assertTrue(bad.isError)
                    bad_depth = await client.call_tool("submit_task", {"workflow_id": wid, "request_id": request["request_id"],
                        "output": {"items": ["速度快", "便于审计"], "details": [{"text": "根", "children": [{"text": "过深"}]}]}})
                    self.assertTrue(bad_depth.isError)
                    await call("submit_task", workflow_id=wid, request_id=request["request_id"], output={"items": ["速度快", "便于审计"]})
                    summary = (await call("next_task", workflow_id=wid))["request"]
                    self.assertEqual(summary["inputs"]["a"]["items"], ["速度快", "便于审计"])
            # A new server process recovers both completed and dispatched tasks.
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as client:
                    await client.initialize()
                    async def call(name, **args):
                        result = await client.call_tool(name, args)
                        self.assertFalse(result.isError, result.content)
                        return result.structuredContent
                    self.assertEqual((await call("next_task", workflow_id=wid))["request"], summary)
                    result = await call("submit_task", workflow_id=wid, request_id=summary["request_id"], output={"text": "材料显示速度与审计优势。"})
                    self.assertTrue(result["complete"])
                    self.assertIsNone((await call("next_task", workflow_id=wid))["request"])
                    await call("set_granularity", problem_id=module, direction="advantages", parameters={"count": 1, "design_effort": 0.38}, actor="test-human")
                    self.assertFalse((await call("workflow_status", workflow_id=wid))["complete"])
                    stale = await client.call_tool("submit_task", {"workflow_id": wid, "request_id": summary["request_id"], "output": {"text": "旧结果"}})
                    self.assertTrue(stale.isError)
                    request = (await call("next_task", workflow_id=wid))["request"]
                    self.assertEqual(request["constraints"]["design_effort"], 0.38)
                    await call("submit_task", workflow_id=wid, request_id=request["request_id"], output={"items": ["便于审计"]})
                    summary = (await call("next_task", workflow_id=wid))["request"]
                    self.assertEqual(summary["inputs"]["a"]["items"], ["便于审计"])
                    self.assertTrue((await call("submit_task", workflow_id=wid, request_id=summary["request_id"], output={"text": "聚焦审计优势。"}))["complete"])
                    events = (await call("history", session_id=session))["result"]
                    self.assertEqual([e["payload"]["content"] for e in events if e["kind"] == "conversation.message"],
                                     ["列举两个优点，并做总结。", question["question"], "优点两项，总结用默认详细程度。"])
                    self.assertTrue(any(e["kind"] == "workflow.invalidated" for e in events))

    def test_cli_fallback_complete_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            db = str(Path(directory) / "cli.sqlite3")
            def call(operation, **args):
                process = subprocess.run([sys.executable, "-m", "agentgranule", "--database", db,
                                          operation, json.dumps(args, ensure_ascii=False)],
                                         capture_output=True, text=True, encoding="utf-8", timeout=15,
                                         cwd=ROOT, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
                self.assertEqual(process.returncode, 0, process.stderr)
                return json.loads(process.stdout)["result"]
            session = call("create_session", title="CLI整体任务")
            module = call("add_module", session_id=session, description="说明材料")
            call("set_granularity", problem_id=module, direction="explanation", parameters={"design_effort": 0.37}, actor="test-human")
            wid = call("create_workflow", session_id=session, tasks=[{"id": "a", "module_id": module, "direction": "explanation"}])["workflow_id"]
            request = call("next_task", workflow_id=wid)["request"]
            self.assertEqual(request["constraints"]["design_effort"], 0.37)
            self.assertTrue(call("submit_task", workflow_id=wid, request_id=request["request_id"], output={"text": "材料说明"})["complete"])
            self.assertEqual(call("workflow_status", workflow_id=wid)["outputs"], {"a": {"text": "材料说明"}})

    def test_memory_database_rejected(self):
        from agentgranule.mcp_server import create_server
        with self.assertRaisesRegex(ValueError, "file database"):
            create_server(":memory:")


if __name__ == "__main__":
    unittest.main()
