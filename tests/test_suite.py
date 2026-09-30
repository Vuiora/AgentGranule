import copy
import io
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

from agentgranule.api import GranuleError, Project, SuiteRunner, load_yaml
from agentgranule.cli import main


class SuiteTests(unittest.TestCase):
    def setUp(self):
        self.project = Project(":memory:")
        self.runner = SuiteRunner(self.project)
        self.suite = load_yaml((Path(__file__).parents[1] / "examples/classification.yaml").read_text(encoding="utf-8"))

    def tearDown(self):
        self.project.close()

    def test_example_runs_and_preserves_messages(self):
        results = self.runner.run(self.suite)
        self.assertTrue(results["result"]["accepted"])
        self.assertEqual(results["plan"]["parent_id"], results["a"])
        messages = [e for e in results["history"] if e["kind"] == "conversation.message"]
        self.assertEqual([e["payload"]["role"] for e in messages], ["user", "assistant"])

    def test_module_dict_without_yaml_loading(self):
        results = self.runner.run({"version": 1, "steps": [
            {"id": "session", "operation": "create_session", "arguments": {"title": "module"}},
        ]})
        self.assertEqual(self.project.history(results["session"])[0]["payload"]["title"], "module")

    def test_unknown_operation_preflight_has_no_effects(self):
        self.suite["steps"][-1]["operation"] = "close"
        with self.assertRaisesRegex(GranuleError, "Unsupported operation"):
            self.runner.run(self.suite)
        self.assertEqual(self.project._db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)

    def test_invalid_arguments_preflight(self):
        self.suite["steps"][0]["arguments"] = {"unknown": "value"}
        with self.assertRaisesRegex(GranuleError, "Invalid arguments"):
            self.runner.run(self.suite)

    def test_references_and_ids_preflight(self):
        for ref in ["missing", "plan", "session.bad", "plan.plan_id.extra"]:
            suite = copy.deepcopy(self.suite)
            suite["steps"][1]["arguments"]["session_id"] = {"$ref": ref}
            with self.subTest(ref=ref), self.assertRaises(GranuleError):
                self.runner.validate(suite)
        self.suite["steps"][1]["id"] = "session"
        with self.assertRaises(GranuleError):
            self.runner.validate(self.suite)

    def test_yaml_duplicate_keys_and_unsafe_tags(self):
        for text in ["version: 1\nversion: 2\nsteps: []", "!!python/object/apply:os.system ['echo bad']"]:
            with self.subTest(text=text), self.assertRaises(GranuleError):
                load_yaml(text)

    def test_yaml_recursive_alias_is_rejected(self):
        with self.assertRaises(GranuleError):
            load_yaml('version: 1\nsteps:\n- id: s\n  operation: create_session\n  arguments: &args\n    title: *args\n')
        suite = copy.deepcopy(self.suite)
        arguments = suite["steps"][0]["arguments"]
        arguments["title"] = arguments
        with self.assertRaisesRegex(GranuleError, "cyclic"):
            self.runner.validate(suite)

    def test_runtime_failure_keeps_prior_steps_and_rejected_result(self):
        self.suite["steps"][-2]["arguments"]["categories"] = ["only one"]
        with self.assertRaisesRegex(GranuleError, "prior steps remain committed"):
            self.runner.run(self.suite)
        row = self.project._db.execute("SELECT id FROM sessions").fetchone()
        self.assertFalse(self.project.history(row[0])[-1]["payload"]["accepted"])

    def test_cli_yaml_success_and_missing_file_error(self):
        path = str(Path(__file__).parents[1] / "examples/classification.yaml")
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(["--database", ":memory:", "run-suite", path]), 0)
        self.assertIn('"accepted": true', output.getvalue())
        with redirect_stderr(io.StringIO()) as error:
            self.assertEqual(main(["run-suite", "does-not-exist.yaml"]), 2)
        self.assertIn('"error"', error.getvalue())

    def test_versions_and_unknown_keys(self):
        for value in [True, 2, "1"]:
            self.suite["version"] = value
            with self.assertRaises(GranuleError):
                self.runner.validate(self.suite)


if __name__ == "__main__":
    unittest.main()
