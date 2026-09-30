"""Small JSON-in/JSON-out command line facade for the framework."""

import argparse
import json
import sys

from .core import GranuleError, Project


OPERATIONS = {
    "create_session": ("title",),
    "add_problem": ("session_id", "description", "parent_id"),
    "add_module": ("session_id", "description", "parent_id"),
    "record_message": ("session_id", "role", "content"),
    "set_granularity": ("problem_id", "count", "actor", "direction", "parameters"),
    "get_granularity": ("problem_id", "direction"),
    "request_granularity": ("problem_id", "direction"),
    "set_default_granularity": ("session_id", "direction", "parameters", "actor"),
    "prepare_plan": ("problem_id", "direction"),
    "submit_result": ("plan_id", "categories", "output"),
    "history": ("session_id",),
}


def main(argv=None):
    parser = argparse.ArgumentParser(description="AgentGranule local framework")
    parser.add_argument("--database", default=".agentgranule/project.sqlite3")
    parser.add_argument("operation", choices=[*OPERATIONS, "run-suite"])
    parser.add_argument("arguments", help="JSON arguments, or a YAML file path for run-suite")
    args = parser.parse_args(argv)
    try:
        if args.operation == "run-suite":
            from pathlib import Path
            from .api import SuiteRunner, load_yaml

            suite = load_yaml(Path(args.arguments).read_text(encoding="utf-8"))
            with Project(args.database) as project:
                result = SuiteRunner(project).run(suite)
        else:
            arguments = json.loads(args.arguments)
            if not isinstance(arguments, dict) or set(arguments) - set(OPERATIONS[args.operation]):
                raise GranuleError("Invalid operation arguments")
            with Project(args.database) as project:
                result = getattr(project, args.operation)(**arguments)
        print(json.dumps({"result": result}, ensure_ascii=False))
        return 0
    except (GranuleError, TypeError, json.JSONDecodeError, OSError, UnicodeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
