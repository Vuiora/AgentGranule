import json
import os
import sys
import tempfile
import unittest
from contextlib import AsyncExitStack
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPTests(unittest.TestCase):
    def run_client(self, scenario):
        async def run():
            with tempfile.TemporaryDirectory() as directory:
                parameters = StdioServerParameters(
                    command=sys.executable,
                    args=["-m", "agentgranule.mcp_server", "--database", str(Path(directory) / "test.sqlite3")],
                    env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
                )
                with anyio.fail_after(30):
                    async with AsyncExitStack() as stack:
                        read, write = await stack.enter_async_context(stdio_client(parameters))
                        client = await stack.enter_async_context(ClientSession(read, write))
                        await client.initialize()
                        await scenario(client)
        anyio.run(run)

    async def call(self, client, tool, **arguments):
        result = await client.call_tool(tool, arguments)
        self.assertFalse(result.isError, result.content)
        return result.structuredContent

    def test_real_stdio_classification_and_resource(self):
        async def scenario(client):
            names = {tool.name for tool in (await client.list_tools()).tools}
            self.assertEqual(names, {"create_session", "add_problem", "record_message", "set_granularity",
                                     "prepare_plan", "submit_result", "history"})
            session = (await self.call(client, "create_session", title="问题 A"))["session_id"]
            await self.call(client, "record_message", session_id=session, role="user", content="B 分类数目设为 3。")
            a = (await self.call(client, "add_problem", session_id=session, description="A"))["problem_id"]
            b = (await self.call(client, "add_problem", session_id=session, description="B", parent_id=a))["problem_id"]
            await self.call(client, "set_granularity", problem_id=b, count=3, actor="human")
            plan = await self.call(client, "prepare_plan", problem_id=b)
            self.assertEqual(plan["count"], 3)
            await self.call(client, "record_message", session_id=session, role="assistant", content="甲、乙、丙。")
            result = await self.call(client, "submit_result", plan_id=plan["plan_id"], categories=["甲", "乙", "丙"])
            self.assertTrue(result["accepted"])
            events = (await self.call(client, "history", session_id=session))["events"]
            self.assertEqual(len([e for e in events if e["kind"] == "conversation.message"]), 2)
            resource = await client.read_resource(f"agentgranule://sessions/{session}/history")
            self.assertEqual(json.loads(resource.contents[0].text), events)
        self.run_client(scenario)

    def test_tool_errors_and_stale_plan_remain_visible(self):
        async def scenario(client):
            session = (await self.call(client, "create_session", title="errors"))["session_id"]
            problem = (await self.call(client, "add_problem", session_id=session, description="B"))["problem_id"]
            for count in [0, True, "3", 2.5]:
                result = await client.call_tool("set_granularity", {"problem_id": problem, "count": count, "actor": "human"})
                self.assertTrue(result.isError)
            await self.call(client, "set_granularity", problem_id=problem, count=3, actor="human")
            plan = await self.call(client, "prepare_plan", problem_id=problem)
            rejected = await client.call_tool("submit_result", {"plan_id": plan["plan_id"], "categories": ["one"]})
            self.assertTrue(rejected.isError)
            await self.call(client, "set_granularity", problem_id=problem, count=5, actor="human")
            stale = await client.call_tool("submit_result", {"plan_id": plan["plan_id"], "categories": ["a", "b", "c"]})
            self.assertTrue(stale.isError)
            events = (await self.call(client, "history", session_id=session))["events"]
            rejected_events = [e for e in events if e["kind"] == "result.submitted"]
            self.assertEqual(len(rejected_events), 2)
            self.assertTrue(all(not e["payload"]["accepted"] for e in rejected_events))
            self.assertEqual((await self.call(client, "prepare_plan", problem_id=problem))["count"], 5)
        self.run_client(scenario)


if __name__ == "__main__":
    unittest.main()
