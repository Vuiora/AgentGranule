"""Isolated source-boundary and MCP transport regressions; no account access."""

import asyncio
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agentgranule import GranuleError
from agentgranule.framework import analyze_framework
from agentgranule.mcp_server import create_server


class SourceBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = tempfile.TemporaryDirectory()
        self.addCleanup(self.fixture.cleanup)
        self.base = Path(self.fixture.name)
        self.allowed = self.base / "allowed"
        self.outside = self.base / "allowed-neighbour"
        self.allowed.mkdir()
        self.outside.mkdir()
        (self.outside / "private.py").write_text("def private(): pass\n", encoding="utf-8")

    def symlink(self, link, target, *, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError) as exc:
            error = getattr(exc, "winerror", getattr(exc, "errno", type(exc).__name__))
            self.skipTest(f"Creating test symlinks is unavailable (error {error})")
        self.addCleanup(link.unlink)

    def junction(self, link, target):
        if os.name != "nt":
            self.skipTest("Windows junction regression")
        process = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, timeout=10,
        )
        if process.returncode != 0:
            self.skipTest("Creating a fixture Windows junction is unavailable")
        self.assertTrue(link.is_junction())
        # Remove only the fixture link; never traverse or delete its target.
        self.addCleanup(link.rmdir)

    def source(self):
        source = self.allowed / "src"
        package = source / "kit"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("from .core import Thing\n", encoding="utf-8")
        (package / "core.py").write_text(
            '"""Actual source."""\nraise RuntimeError("must never execute")\nclass Thing: pass\n',
            encoding="utf-8",
        )
        return source

    def test_regular_root_and_subroot_match_default_static_inventory(self):
        source = self.source()
        self.assertEqual(analyze_framework(str(self.allowed)),
                         analyze_framework(str(self.allowed), allowed_root=self.allowed))
        self.assertEqual(analyze_framework(str(source / "kit")),
                         analyze_framework(str(source / "kit"), allowed_root=self.allowed))

    def test_root_outside_boundary_rejected_before_traversal(self):
        with patch.object(Path, "iterdir", side_effect=AssertionError("must not traverse")):
            with self.assertRaisesRegex(GranuleError, "allowed_root"):
                analyze_framework(str(self.outside), allowed_root=self.allowed)
        # Omitting the opt-in boundary retains the original public behavior.
        self.assertEqual(analyze_framework(str(self.outside))["module_count"], 1)

    def test_root_parent_traversal_and_boundary_must_exist(self):
        with self.assertRaisesRegex(GranuleError, "allowed_root"):
            analyze_framework(str(self.allowed / ".." / self.outside.name), allowed_root=self.allowed)
        with self.assertRaisesRegex(GranuleError, "existing directory"):
            analyze_framework(str(self.allowed), allowed_root=self.base / "missing")
        with self.assertRaisesRegex(GranuleError, "existing directory"):
            analyze_framework(str(self.allowed), allowed_root=self.outside / "private.py")

    def test_python_file_link_outside_boundary_is_never_opened(self):
        source = self.source()
        self.symlink(source / "escape.py", self.outside / "private.py")
        with patch("agentgranule.framework.tokenize.open", side_effect=AssertionError("must not open")):
            with self.assertRaisesRegex(GranuleError, "allowed_root"):
                analyze_framework(str(self.allowed), allowed_root=self.allowed)

    def test_outside_file_resolution_rejected_without_symlink_privileges(self):
        source = self.source()
        logical = source / "escape.py"
        logical.write_text("def placeholder(): pass\n", encoding="utf-8")
        original = Path.resolve

        def resolved(path, *args, **kwargs):
            # Exercise the boundary decision independently of OS link privileges.
            if path == logical:
                return self.outside / "private.py"
            return original(path, *args, **kwargs)

        with patch.object(Path, "resolve", resolved):
            with patch("agentgranule.framework.tokenize.open", side_effect=AssertionError("must not open")):
                with self.assertRaisesRegex(GranuleError, "allowed_root"):
                    analyze_framework(str(self.allowed), allowed_root=self.allowed)

    def assert_directory_escape_not_traversed(self):
        original = Path.iterdir

        def guarded(directory):
            self.assertNotEqual(directory.resolve(), self.outside.resolve(), "outside directory was traversed")
            return original(directory)

        with patch.object(Path, "iterdir", guarded):
            with self.assertRaisesRegex(GranuleError, "allowed_root"):
                analyze_framework(str(self.allowed), allowed_root=self.allowed)

    def test_directory_link_outside_boundary_rejected_before_descent(self):
        source = self.source()
        self.symlink(source / "escape", self.outside, directory=True)
        self.assert_directory_escape_not_traversed()

    def test_windows_junction_outside_boundary_rejected_before_descent(self):
        source = self.source()
        self.junction(source / "escape", self.outside)
        self.assert_directory_escape_not_traversed()

    def test_windows_source_junction_escape_rejected_before_descent(self):
        self.junction(self.allowed / "src", self.outside)
        with patch.object(Path, "iterdir", side_effect=AssertionError("must not traverse")):
            with self.assertRaisesRegex(GranuleError, "allowed_root"):
                analyze_framework(str(self.allowed), allowed_root=self.allowed)

    def test_link_inside_boundary_but_outside_selected_source_rejected(self):
        source = self.source()
        sibling = self.allowed / "elsewhere"
        sibling.mkdir()
        (sibling / "extra.py").write_text("def extra(): pass\n", encoding="utf-8")
        self.symlink(source / "escape", sibling, directory=True)
        with self.assertRaisesRegex(GranuleError, "source directory"):
            analyze_framework(str(self.allowed), allowed_root=self.allowed)

    def test_internal_file_and_directory_links_preserve_logical_module_ids(self):
        source = self.source()
        self.symlink(source / "alias.py", source / "kit" / "core.py")
        self.symlink(source / "copy", source / "kit", directory=True)
        result = analyze_framework(str(self.allowed), allowed_root=self.allowed)
        identifiers = {module["id"] for module in result["analysis"]["modules"]}
        self.assertEqual(identifiers, {"alias.py", "kit/__init__.py", "kit/core.py",
                                      "copy/__init__.py", "copy/core.py"})

    def test_windows_internal_junction_is_scanned_and_cycle_rejected(self):
        source = self.source()
        self.junction(source / "copy", source / "kit")
        result = analyze_framework(str(self.allowed), allowed_root=self.allowed)
        self.assertIn("copy/core.py", result["analysis"]["context"]["file_hashes"])
        self.junction(source / "kit" / "cycle", source)
        with self.assertRaisesRegex(GranuleError, "Cyclic"):
            analyze_framework(str(self.allowed), allowed_root=self.allowed)

    def test_cyclic_directory_link_fails_without_infinite_recursion(self):
        source = self.source()
        self.symlink(source / "kit" / "cycle", source, directory=True)
        with self.assertRaisesRegex(GranuleError, "Cyclic"):
            analyze_framework(str(self.allowed), allowed_root=self.allowed)

    def test_ignored_directory_link_does_not_expose_ignored_materials(self):
        source = self.source()
        self.symlink(source / ".agentgranule", self.outside, directory=True)
        self.assertEqual(analyze_framework(str(self.allowed), allowed_root=self.allowed)["module_count"], 2)


class MCPSourceBoundaryTests(unittest.TestCase):
    def test_invalid_source_root_does_not_create_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "isolated.sqlite3"
            with self.assertRaisesRegex(GranuleError, "source_root"):
                create_server(str(database), source_root=str(Path(directory) / "missing"))
            with self.assertRaisesRegex(GranuleError, "non-empty string"):
                create_server(str(database), source_root=" ")
            self.assertFalse(database.exists())

    def test_real_stdio_boundary_tool_schemas_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            allowed = root / "allowed"
            outside = root / "outside"
            allowed.mkdir()
            outside.mkdir()
            (allowed / "inside.py").write_text("def inside(): pass\n", encoding="utf-8")
            (outside / "outside.py").write_text("def outside(): pass\n", encoding="utf-8")
            asyncio.run(self.stdio_scenario(root / "isolated.sqlite3", allowed, outside))

    async def stdio_scenario(self, database, allowed, outside):
        expected_tools = {
            "create_session", "add_module", "record_message", "request_granularity", "get_granularity",
            "set_granularity", "set_default_granularity", "create_workflow", "next_task", "submit_task",
            "workflow_status", "history", "analyze_framework", "propose_analysis", "update_analysis",
            "get_analysis", "approve_analysis", "allocation_snapshot", "save_allocation",
        }
        async with asyncio.timeout(45):
            for bounded in (True, False):
                args = ["-m", "agentgranule.mcp_server", "--database", str(database)]
                if bounded:
                    args.extend(["--source-root", str(allowed)])
                params = StdioServerParameters(command=sys.executable, args=args,
                                              env={**os.environ, "PYTHONIOENCODING": "utf-8"})
                async with stdio_client(params) as (reader, writer):
                    async with ClientSession(reader, writer) as client:
                        await client.initialize()
                        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
                        self.assertEqual(set(tools), expected_tools)
                        for name, tool in tools.items():
                            self.assertTrue(tool.inputSchema)
                            self.assertTrue(tool.outputSchema)
                            self.assertIsNotNone(tool.annotations)
                            self.assertFalse(tool.annotations.openWorldHint)
                            self.assertEqual(tool.annotations.readOnlyHint, name == "analyze_framework")
                        scanner = tools["analyze_framework"]
                        self.assertEqual(set(scanner.inputSchema["properties"]), {"root_path"})
                        self.assertFalse(scanner.annotations.destructiveHint)
                        self.assertTrue(scanner.annotations.idempotentHint)
                        result = await client.call_tool("analyze_framework", {"root_path": str(allowed)})
                        self.assertFalse(result.isError, result.content)
                        self.assertEqual(result.structuredContent["module_count"], 1)
                        result = await client.call_tool("analyze_framework", {"root_path": str(outside)})
                        self.assertEqual(bool(result.isError), bounded)
                        if not bounded:
                            self.assertEqual(result.structuredContent["module_count"], 1)
                        # A source-only call must not initialize or disclose a database.
                        self.assertFalse(database.exists())


if __name__ == "__main__":
    unittest.main()
