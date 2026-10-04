"""Personal plugin packages are built only in temporary fixtures."""

import importlib.util
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import zlib


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("chatgpt_plugin_test_helper", ROOT / "scripts" / "prepare_chatgpt_plugin.py")
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)
TEST_APP_ID = "asdk_app_00000000000000000000000000000000"


class ChatGPTPluginTests(unittest.TestCase):
    def run_fixture(self, local, *arguments):
        output = io.StringIO()
        errors = io.StringIO()
        with patch.object(plugin, "LOCAL", local):
            with patch.object(sys, "argv", ["prepare_chatgpt_plugin.py", "--app-id", TEST_APP_ID, *arguments]):
                with patch.object(sys, "stdout", output), patch.object(sys, "stderr", errors):
                    plugin.main()
        return json.loads(output.getvalue())

    def assert_package(self, result):
        directory = Path(result["directory"])
        archive = Path(result["archive"])
        source_files = {path.relative_to(plugin.TEMPLATE).as_posix(): path.read_bytes()
                        for path in plugin.TEMPLATE.rglob("*") if path.is_file()}
        self.assertTrue(source_files)
        with zipfile.ZipFile(archive) as package:
            self.assertEqual(set(package.namelist()), set(source_files) | {".app.json"})
            for name, contents in source_files.items():
                self.assertEqual(package.read(name), contents)
                self.assertEqual((directory / name).read_bytes(), contents)
            app_contents = {"apps": {"agentgranule": {"id": TEST_APP_ID, "required": True}}}
            self.assertEqual(json.loads(package.read(".app.json")), app_contents)
            self.assertEqual(json.loads((directory / ".app.json").read_text(encoding="utf-8")), app_contents)
            self.assertNotIn("plugin.json", package.namelist())
            manifest = json.loads(package.read(".codex-plugin/plugin.json"))
            self.assertEqual(manifest["name"], "agentgranule-personal")
            self.assertEqual(manifest["version"], "0.1.2")
            self.assertNotIn("$schema", manifest)
            self.assertEqual(manifest["skills"], "./skills/")
            self.assertEqual(manifest["apps"], "./.app.json")
            self.assertTrue(manifest["interface"]["longDescription"].strip())
            for field in ("logo", "composerIcon"):
                icon_path = manifest["interface"][field]
                self.assertTrue(icon_path.startswith("./assets/"))
                self.assert_png(package.read(icon_path.removeprefix("./")))
        self.assertIs(result["installed"], False)
        self.assertIs(result["chatgpt_connection_verified"], False)

    def assert_png(self, contents):
        self.assertLessEqual(len(contents), 5 * 1024 * 1024)
        self.assertEqual(contents[:8], b"\x89PNG\r\n\x1a\n")
        offset = 8
        chunks = []
        while offset < len(contents):
            length = struct.unpack("!I", contents[offset:offset + 4])[0]
            kind = contents[offset + 4:offset + 8]
            data = contents[offset + 8:offset + 8 + length]
            crc = struct.unpack("!I", contents[offset + 8 + length:offset + 12 + length])[0]
            self.assertEqual(zlib.crc32(kind + data) & 0xffffffff, crc)
            chunks.append((kind, data))
            offset += length + 12
        self.assertEqual(offset, len(contents))
        self.assertEqual(chunks[0][0], b"IHDR")
        self.assertEqual(chunks[-1], (b"IEND", b""))
        width, height, depth, color, compression, filtering, interlace = struct.unpack("!2I5B", chunks[0][1])
        self.assertEqual(width, height)
        self.assertGreaterEqual(width, 48)
        self.assertLessEqual(width, 4096)
        self.assertEqual((depth, color, compression, filtering, interlace), (8, 2, 0, 0, 0))
        pixels = zlib.decompress(b"".join(data for kind, data in chunks if kind == b"IDAT"))
        self.assertEqual(len(pixels), height * (1 + 3 * width))

    def test_default_output_complete_template_and_app_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "local"
            result = self.run_fixture(local)
            self.assertEqual(Path(result["directory"]), local / "agentgranule-personal")
            self.assertEqual(Path(result["archive"]), local / "agentgranule-personal.zip")
            self.assert_package(result)

    def test_revision_output_preserves_previous_package_and_normalizes_app_url_id(self):
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "local"
            previous = self.run_fixture(local)
            previous_archive = Path(previous["archive"]).read_bytes()
            previous_files = {path.relative_to(Path(previous["directory"])).as_posix(): path.read_bytes()
                              for path in Path(previous["directory"]).rglob("*") if path.is_file()}
            result = self.run_fixture(local, "--app-id", f"plugin_{TEST_APP_ID}",
                                      "--output-name", "agentgranule-personal-r3")
            self.assertEqual(Path(result["directory"]), local / "agentgranule-personal-r3")
            self.assertEqual(result["app_id"], TEST_APP_ID)
            self.assert_package(result)
            self.assertEqual(Path(previous["archive"]).read_bytes(), previous_archive)
            for name, contents in previous_files.items():
                self.assertEqual((Path(previous["directory"]) / name).read_bytes(), contents)

    def test_skills_only_candidate_has_independent_identity_and_no_app_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "local"
            output = io.StringIO()
            original_manifest = (plugin.TEMPLATE / ".codex-plugin" / "plugin.json").read_bytes()
            with patch.object(plugin, "LOCAL", local):
                with patch.object(sys, "argv", ["prepare_chatgpt_plugin.py", "--skills-only",
                                                "--output-name", "agentgranule-personal-r4"]):
                    with patch.object(sys, "stdout", output):
                        plugin.main()
            result = json.loads(output.getvalue())
            self.assertIs(result["skills_only"], True)
            self.assertIsNone(result["app_id"])
            self.assertIs(result["installed"], False)
            self.assertIs(result["chatgpt_connection_verified"], False)
            with zipfile.ZipFile(result["archive"]) as package:
                self.assertNotIn(".app.json", package.namelist())
                self.assertNotIn("plugin.json", package.namelist())
                manifest = json.loads(package.read(".codex-plugin/plugin.json"))
                self.assertNotIn("apps", manifest)
                self.assertNotIn("mcpServers", manifest)
                self.assertEqual(manifest["name"], "agentgranule-workflow")
                self.assertEqual(manifest["version"], "0.1.3")
                self.assertEqual(manifest["interface"]["displayName"], "AgentGranule Workflow")
                self.assertEqual(manifest["skills"], "./skills/")
                for source in plugin.TEMPLATE.rglob("*"):
                    if source.is_file():
                        relative = source.relative_to(plugin.TEMPLATE).as_posix()
                        if relative != ".codex-plugin/plugin.json":
                            self.assertEqual(package.read(relative), source.read_bytes())
                skill = package.read("skills/agentgranule-workflow/SKILL.md").decode("utf-8")
                self.assertIn("同时选择 **AgentGranule Personal** 连接", skill)
                self.assertIn("不能声称已绑定或调用成功", skill)
            self.assertEqual((plugin.TEMPLATE / ".codex-plugin" / "plugin.json").read_bytes(), original_manifest)

    def test_independent_skill_archive_contains_only_complete_skill_and_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "local"
            output = io.StringIO()
            source_files = {path.relative_to(plugin.TEMPLATE).as_posix(): path.read_bytes()
                            for path in plugin.TEMPLATE.rglob("*") if path.is_file()}
            with patch.object(plugin, "LOCAL", local):
                with patch.object(sys, "argv", ["prepare_chatgpt_plugin.py", "--skill-archive",
                                                "--output-name", "agentgranule-personal-skill"]):
                    with patch.object(sys, "stdout", output):
                        plugin.main()
            result = json.loads(output.getvalue())
            self.assertIs(result["skill_archive"], True)
            self.assertIs(result["skills_only"], False)
            self.assertIsNone(result["app_id"])
            self.assertIs(result["installed"], False)
            self.assertIs(result["chatgpt_connection_verified"], False)
            expected = {
                "SKILL.md": source_files["skills/agentgranule-workflow/SKILL.md"],
                "references/remote-contract.md": source_files["skills/agentgranule-workflow/references/remote-contract.md"],
            }
            with zipfile.ZipFile(result["archive"]) as package:
                self.assertEqual(set(package.namelist()), set(expected))
                for name, contents in expected.items():
                    self.assertEqual(package.read(name), contents)
                    self.assertEqual((Path(result["directory"]) / name).read_bytes(), contents)
            files_after = {path.relative_to(plugin.TEMPLATE).as_posix(): path.read_bytes()
                           for path in plugin.TEMPLATE.rglob("*") if path.is_file()}
            self.assertEqual(files_after, source_files)
            archive_before = Path(result["archive"]).read_bytes()
            invalid_arguments = [
                ["--skill-archive", "--skills-only"],
                ["--skill-archive", "--app-id", TEST_APP_ID],
                ["--skill-archive", "--output-name", "../outside"],
                ["--skill-archive", "--output-name", "agentgranule-personal-skill"],
            ]
            for arguments in invalid_arguments:
                with self.subTest(arguments=arguments):
                    with patch.object(plugin, "LOCAL", local), patch.object(sys, "stderr", io.StringIO()):
                        with patch.object(sys, "argv", ["prepare_chatgpt_plugin.py", *arguments]):
                            with self.assertRaises(SystemExit) as raised:
                                plugin.main()
                            self.assertEqual(raised.exception.code, 2)
            self.assertEqual(Path(result["archive"]).read_bytes(), archive_before)

    def test_existing_directory_or_archive_is_never_overwritten(self):
        for existing in ("directory", "archive"):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as directory:
                local = Path(directory) / "local"
                local.mkdir()
                basename = "agentgranule-personal-r2"
                if existing == "directory":
                    target = local / basename
                    target.mkdir()
                    sentinel = target / "sentinel.txt"
                else:
                    sentinel = local / f"{basename}.zip"
                sentinel.write_bytes(b"original isolated content")
                with self.assertRaises(SystemExit) as raised:
                    self.run_fixture(local, "--output-name", basename)
                self.assertEqual(raised.exception.code, 2)
                self.assertEqual(sentinel.read_bytes(), b"original isolated content")
                self.assertFalse(any(path.name.startswith("plugin-build-") for path in local.iterdir()))

    def test_illegal_output_names_fail_before_creating_files(self):
        invalid_names = ["", "other", "agentgranule-personal/child", "agentgranule-personal\\child",
                         "../agentgranule-personal", "agentgranule-personal-../outside", "/agentgranule-personal",
                         "C:\\agentgranule-personal", "agentgranule-personal-R2", "agentgranule-personal_r2",
                         "agentgranule-personal-", "agentgranule-personal--r2", "agentgranule-personal-r2\n"]
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "local"
            for name in invalid_names:
                with self.subTest(name=name), self.assertRaises(SystemExit) as raised:
                    self.run_fixture(local, "--output-name", name)
                self.assertEqual(raised.exception.code, 2)
                self.assertFalse(local.exists())


if __name__ == "__main__":
    unittest.main()
