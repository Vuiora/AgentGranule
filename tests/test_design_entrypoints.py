import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from agentgranule.design_view import main, show_design


class NativeDesignEntrypointTests(unittest.TestCase):
    def test_core_import_does_not_require_or_load_graphics_packages(self):
        completed = subprocess.run([sys.executable, "-c",
            "import sys, agentgranule; assert not any(m.startswith('PySide6') for m in sys.modules)"],
            capture_output=True, text=True, timeout=20)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_default_window_loads_native_backend_without_silent_fallback(self):
        native = types.ModuleType("agentgranule.qt_design_view")
        native.show_design_qt = lambda service, analysis: (_ for _ in ()).throw(RuntimeError("GL unavailable"))
        with patch.dict(sys.modules, {"agentgranule.qt_design_view": native}), \
                patch("agentgranule.design_view.show_design_tk") as compatibility:
            with self.assertRaisesRegex(RuntimeError, "GL unavailable"):
                show_design(object(), "isolated-analysis")
            compatibility.assert_not_called()

    def test_default_and_explicit_compatibility_cancel_use_unique_results(self):
        with tempfile.TemporaryDirectory() as directory:
            for renderer in ("opengl", "tk"):
                output = Path(directory) / f"{renderer}.json"
                args = ["--database", str(Path(directory) / "unused.sqlite"),
                        "--analysis-id", "isolated-analysis", "--output-file", str(output)]
                if renderer == "tk":
                    args += ["--renderer", "tk"]
                entry = "show_design" if renderer == "opengl" else "show_design_tk"
                with patch(f"agentgranule.design_view.{entry}", return_value={"status": "cancelled"}) as window, \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(args), 0)
                    self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"status": "cancelled"})
                    with contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(main(args), 2)
                    self.assertEqual(window.call_count, 1)

    def test_failed_or_nonfinal_native_result_never_creates_result_file(self):
        with tempfile.TemporaryDirectory() as directory:
            for result in ({"status": "preview"}, RuntimeError("Shader compilation failed")):
                output = Path(directory) / "not-approved.json"
                options = {"side_effect": result} if isinstance(result, Exception) else {"return_value": result}
                with patch("agentgranule.design_view.show_design", **options), \
                        contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(["--database", str(Path(directory) / "unused.sqlite"),
                                           "--analysis-id", "isolated-analysis", "--output-file", str(output)]), 2)
                self.assertFalse(output.exists())

    def test_missing_result_directory_does_not_open_human_approval_window(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "not-created" / "result.json"
            with patch("agentgranule.design_view.show_design") as window, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--database", str(Path(directory) / "unused.sqlite"),
                                       "--analysis-id", "isolated-analysis", "--output-file", str(output)]), 2)
            window.assert_not_called()
            self.assertFalse(output.exists())

    def test_framework_example_refuses_existing_result_before_creating_session(self):
        path = Path(__file__).resolve().parents[1] / "examples/design_framework.py"
        spec = importlib.util.spec_from_file_location("isolated_design_example", path)
        example = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(example)
        with tempfile.TemporaryDirectory() as directory:
            output, database = Path(directory) / "existing.json", Path(directory) / "not-created.sqlite"
            output.write_text('original result', encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(example.main(["--database", str(database), "--output-file", str(output)]), 2)
            self.assertFalse(database.exists())
            self.assertEqual(output.read_text(encoding="utf-8"), 'original result')

    def test_framework_example_initial_load_error_is_nonapproval_json_failure(self):
        path = Path(__file__).resolve().parents[1] / "examples/design_framework.py"
        spec = importlib.util.spec_from_file_location("isolated_design_example", path)
        example = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(example)
        with tempfile.TemporaryDirectory() as directory:
            output, database = Path(directory) / "not-written.json", Path(directory) / "isolated.sqlite"
            error = io.StringIO()
            with contextlib.redirect_stderr(error), patch.object(example, "design_main") as window:
                self.assertEqual(example.main(["--database", str(database), "--analysis-id", "unknown",
                                               "--output-file", str(output)]), 2)
            self.assertIn("error", json.loads(error.getvalue()))
            self.assertFalse(output.exists())
            window.assert_not_called()
