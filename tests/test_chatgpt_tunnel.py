"""Offline tunnel preflight and real script-stdio tests using isolated data."""

import asyncio
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "scripts" / "chatgpt_mcp.py"
OFFICIAL_CLIENT = ROOT / ".agentgranule" / "chatgpt" / "tools" / "v0.0.15" / "client" / "tunnel-client.exe"
spec = importlib.util.spec_from_file_location("chatgpt_tunnel_test_helper", ROOT / "scripts" / "chatgpt_tunnel.py")
tunnel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tunnel)


def isolated_environment():
    environment = os.environ.copy()
    # Test subprocesses never receive runtime control-plane or model credentials.
    for name in ("CONTROL_PLANE_API_KEY", "OPENAI_API_KEY", "AGENTGRANULE_TEST_CONTROL_PLANE_API_KEY"):
        environment.pop(name, None)
    return environment


class ChatGPTTunnelTests(unittest.TestCase):
    def fixture(self, directory):
        root = Path(directory) / "接入 fixture with spaces"
        (root / "scripts").mkdir(parents=True)
        # Windows TEMP can use an 8.3 alias; match the canonical production ROOT.
        root = root.resolve()
        shutil.copyfile(ENTRYPOINT, root / "scripts" / ENTRYPOINT.name)
        local = root / ".agentgranule" / "chatgpt"
        return root, local

    @unittest.skipUnless(OFFICIAL_CLIENT.is_file(), "Verified optional v0.0.15 Windows client is not installed")
    def test_real_official_init_preflight_accepts_script_entrypoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root, local = self.fixture(directory)
            actual_run = subprocess.run
            commands = []

            def offline_run(command, **kwargs):
                command = list(command)
                commands.append(command)
                # init is local; this endpoint also prevents an accidental cloud request.
                command.extend(["--control-plane-base-url", "http://127.0.0.1:1"])
                kwargs.update(capture_output=True, text=True, encoding="utf-8", timeout=20,
                              env=isolated_environment(), cwd=root)
                return actual_run(command, **kwargs)

            with patch.object(tunnel, "ROOT", root), patch.object(tunnel, "LOCAL", local):
                with patch.object(tunnel.subprocess, "run", offline_run):
                    with patch.object(sys, "argv", ["chatgpt_tunnel.py", "configure",
                                                    "--client", str(OFFICIAL_CLIENT),
                                                    "--tunnel-id", "tunnel_00000000000000000000000000000000"]):
                        self.assertEqual(tunnel.main(), 0)
            self.assertEqual(len(commands), 1)
            command = commands[0]
            mcp_command = command[command.index("--mcp-command") + 1]
            expected = tunnel.command_string([
                Path(sys.executable).resolve().as_posix(), (root / "scripts" / ENTRYPOINT.name).as_posix(),
                "--database", (root / ".agentgranule" / "chatgpt-personal.sqlite3").as_posix(),
                "--source-root", root.as_posix(),
            ])
            self.assertEqual(mcp_command, expected)
            profile = local / "profiles" / "agentgranule-personal.yaml"
            self.assertTrue(profile.is_file())
            self.assertIn("env:CONTROL_PLANE_API_KEY", profile.read_text(encoding="utf-8"))
            self.assertFalse((root / ".agentgranule" / "chatgpt-personal.sqlite3").exists())

    def test_configure_never_overwrites_existing_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root, local = self.fixture(directory)
            profile = local / "profiles" / "agentgranule-personal.yaml"
            profile.parent.mkdir(parents=True)
            profile.write_text("isolated sentinel\n", encoding="utf-8")
            error_output = io.StringIO()
            with patch.object(tunnel, "ROOT", root), patch.object(tunnel, "LOCAL", local):
                with patch.object(tunnel.subprocess, "run", side_effect=AssertionError("must not run client")):
                    with patch.object(sys, "argv", ["chatgpt_tunnel.py", "configure",
                                                    "--client", sys.executable,
                                                    "--tunnel-id", "tunnel_00000000000000000000000000000000"]):
                        with patch.object(sys, "stderr", error_output):
                            with self.assertRaises(SystemExit) as raised:
                                tunnel.main()
                        self.assertEqual(raised.exception.code, 2)
            self.assertIn("was not overwritten", error_output.getvalue())
            self.assertEqual(profile.read_text(encoding="utf-8"), "isolated sentinel\n")

    def test_non_configure_rejects_configuration_arguments_before_file_access(self):
        for action in ("check", "doctor", "run"):
            for option in ("--database", "--python"):
                with self.subTest(action=action, option=option):
                    error_output = io.StringIO()
                    with patch.object(sys, "argv", ["chatgpt_tunnel.py", action, option, "unused-path"]):
                        with patch.object(sys, "stderr", error_output):
                            with patch.object(Path, "resolve", side_effect=AssertionError("must not access configuration")):
                                with self.assertRaises(SystemExit) as raised:
                                    tunnel.main()
                    self.assertEqual(raised.exception.code, 2)
                    self.assertIn("configure-only", error_output.getvalue())
                    self.assertIn("use the existing profile", error_output.getvalue())

    def test_check_reports_defaults_without_reading_existing_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root, local = self.fixture(directory)
            profile = local / "profiles" / "agentgranule-personal.yaml"
            profile.parent.mkdir(parents=True)
            profile.write_text("isolated existing profile with a different database\n", encoding="utf-8")
            output = io.StringIO()
            responses = [subprocess.CompletedProcess([], 0),
                         subprocess.CompletedProcess([], 0, stdout="isolated-client-version\n")]
            with patch.object(tunnel, "ROOT", root), patch.object(tunnel, "LOCAL", local):
                with patch.object(sys, "argv", ["chatgpt_tunnel.py", "check", "--client", sys.executable]):
                    with patch.object(tunnel.subprocess, "run", side_effect=responses) as commands:
                        with patch.object(Path, "read_text", side_effect=AssertionError("must not read profile")):
                            with patch.object(sys, "stdout", output):
                                self.assertEqual(tunnel.main(), 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report["default_database"], str(root / ".agentgranule" / "chatgpt-personal.sqlite3"))
            self.assertTrue(report["profile_exists"])
            self.assertFalse(report["chatgpt_connection_verified"])
            self.assertIn("use the existing profile", report["runtime_configuration"])
            self.assertIn("default_mcp_command", report)
            self.assertNotIn("database", report)
            self.assertNotIn("mcp_command", report)
            self.assertEqual(commands.call_count, 2)
            self.assertFalse((root / ".agentgranule" / "chatgpt-personal.sqlite3").exists())
            self.assertEqual(profile.read_text(encoding="utf-8"),
                             "isolated existing profile with a different database\n")

    def test_configure_preserves_default_and_explicit_configuration_commands(self):
        for explicit in (False, True):
            with self.subTest(explicit=explicit), tempfile.TemporaryDirectory() as directory:
                root, local = self.fixture(directory)
                database = root / ".agentgranule" / ("custom.sqlite3" if explicit else "chatgpt-personal.sqlite3")
                arguments = ["chatgpt_tunnel.py", "configure", "--client", sys.executable,
                             "--tunnel-id", "tunnel_00000000000000000000000000000000"]
                if explicit:
                    arguments.extend(["--python", sys.executable, "--database", str(database)])
                with patch.object(tunnel, "ROOT", root), patch.object(tunnel, "LOCAL", local):
                    with patch.object(sys, "argv", arguments):
                        with patch.object(tunnel.subprocess, "run") as run:
                            self.assertEqual(tunnel.main(), 0)
                command = run.call_args.args[0]
                expected = tunnel.command_string([
                    Path(sys.executable).resolve().as_posix(), (root / "scripts" / ENTRYPOINT.name).as_posix(),
                    "--database", database.as_posix(), "--source-root", root.as_posix(),
                ])
                self.assertEqual(command[command.index("--mcp-command") + 1], expected)
                self.assertEqual(command[command.index("--control-plane-api-key-ref") + 1],
                                 "env:CONTROL_PLANE_API_KEY")
                self.assertFalse(database.exists())

    def test_real_script_stdio_unicode_tools_and_source_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "允许 source"
            outside = base / "outside"
            source.mkdir()
            outside.mkdir()
            (source / "module.py").write_text('"""真实职责。"""\ndef process(): pass\n', encoding="utf-8")
            (outside / "private.py").write_text("def private(): pass\n", encoding="utf-8")
            asyncio.run(self.script_stdio(base / "isolated.sqlite3", source, outside))

    async def script_stdio(self, database, source, outside):
        environment = isolated_environment()
        environment["PYTHONIOENCODING"] = "ascii"
        environment.pop("PYTHONUTF8", None)
        params = StdioServerParameters(command=sys.executable,
                                      args=[str(ENTRYPOINT), "--database", str(database),
                                            "--source-root", str(source)], env=environment)
        async with asyncio.timeout(45):
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as client:
                    await client.initialize()
                    tools = (await client.list_tools()).tools
                    self.assertEqual(len(tools), 19)
                    self.assertTrue(all(tool.inputSchema and tool.outputSchema for tool in tools))
                    result = await client.call_tool("analyze_framework", {"root_path": str(source)})
                    self.assertFalse(result.isError, result.content)
                    self.assertEqual(result.structuredContent["analysis"]["modules"][0]["description"], "真实职责。")
                    result = await client.call_tool("analyze_framework", {"root_path": str(outside)})
                    self.assertTrue(result.isError)
                    self.assertFalse(database.exists())
                    session = await client.call_tool("create_session", {"title": "隔离 UTF-8 传输测试"})
                    self.assertFalse(session.isError, session.content)
                    session_id = session.structuredContent["result"]
                    content = "中文记录与精度 0.37：📦"
                    result = await client.call_tool("record_message", {"session_id": session_id,
                                                                       "role": "user", "content": content})
                    self.assertFalse(result.isError, result.content)
                    result = await client.call_tool("history", {"session_id": session_id})
                    self.assertFalse(result.isError, result.content)
                    messages = [event["payload"]["content"] for event in result.structuredContent["result"]
                                if event["kind"] == "conversation.message"]
                    self.assertEqual(messages, [content])


if __name__ == "__main__":
    unittest.main()
