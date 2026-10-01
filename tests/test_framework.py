import tempfile
import unittest
from pathlib import Path

from agentgranule import GranuleError
from agentgranule.framework import analyze_framework


class FrameworkTests(unittest.TestCase):
    def test_static_inventory_has_stable_ids_and_real_symbols_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "src" / "kit"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text('"""Kit exports."""\nfrom .core import Thing\n', encoding="utf-8")
            (package / "core.py").write_text('"""Real core responsibility."""\nraise RuntimeError("must not execute")\nclass Thing: pass\n', encoding="utf-8")
            (package / "flow.py").write_text('from .core import Thing\nasync def process(): pass\n', encoding="utf-8")
            (root / "tests").mkdir()
            (root / "tests" / "test_fake.py").write_text('raise RuntimeError("not runtime source")', encoding="utf-8")
            result = analyze_framework(str(root))
            modules = {m["id"]: m for m in result["analysis"]["modules"]}
            self.assertEqual(set(modules), {"kit/__init__.py", "kit/core.py", "kit/flow.py"})
            self.assertEqual(modules["kit/core.py"]["description"], "Real core responsibility.")
            self.assertIn("Thing", modules["kit/core.py"]["basis"])
            self.assertIn("process", modules["kit/flow.py"]["basis"])
            self.assertEqual(modules["kit/flow.py"]["depends_on"], ["kit/core.py"])
            self.assertEqual(result, analyze_framework(str(root)))

    def test_relative_nested_import_and_parent_structure_are_not_conflated(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "kit"
            (package / "sub").mkdir(parents=True)
            (package / "core.py").write_text("def work(): pass", encoding="utf-8")
            (package / "sub" / "view.py").write_text("from ..core import work", encoding="utf-8")
            result = analyze_framework(directory)
            view = next(m for m in result["analysis"]["modules"] if m["id"] == "kit/sub/view.py")
            self.assertEqual(view["depends_on"], ["kit/core.py"])
            self.assertIsNone(view["parent_id"])

    def test_cyclic_imports_preserved_as_materials_for_human_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.py").write_text("import b", encoding="utf-8")
            (root / "b.py").write_text("import a", encoding="utf-8")
            result = analyze_framework(directory)
            self.assertTrue(result["warnings"])
            self.assertTrue(all(not m["depends_on"] for m in result["analysis"]["modules"]))
            self.assertEqual(len(result["analysis"]["context"]["import_relationships"]), 2)

    def test_direct_package_root_preserves_relative_imports_and_python_encoding(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "kit"
            package.mkdir()
            (package / "__init__.py").write_text("from .core import Thing", encoding="utf-8")
            (package / "core.py").write_bytes('# coding: latin-1\n"""Responsabilité."""\nclass Thing: pass'.encode("latin-1"))
            result = analyze_framework(str(package))
            modules = {m["id"]: m for m in result["analysis"]["modules"]}
            self.assertEqual(modules["__init__.py"]["name"], "kit")
            self.assertEqual(modules["core.py"]["name"], "kit.core")
            self.assertEqual(modules["__init__.py"]["depends_on"], ["core.py"])
            self.assertEqual(modules["core.py"]["description"], "Responsabilité.")
            self.assertEqual(set(result["analysis"]["context"]["file_hashes"]), set(modules))

    def test_missing_source_and_syntax_error_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(GranuleError):
                analyze_framework(directory)
            (Path(directory) / "bad.py").write_text("def :", encoding="utf-8")
            with self.assertRaisesRegex(GranuleError, "bad.py"):
                analyze_framework(directory)
        with self.assertRaises(GranuleError):
            analyze_framework(directory)
