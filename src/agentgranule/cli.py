"""Small JSON-in/JSON-out command line facade for the framework."""

import argparse
import json
import sys

from .core import GranuleError, Project


OPERATIONS = {
    "create_session": ("title",),
    "add_problem": ("session_id", "description", "parent_id"),
    "record_message": ("session_id", "role", "content"),
    "set_granularity": ("problem_id", "count", "actor"),
    "prepare_plan": ("problem_id",),
    "submit_result": ("plan_id", "categories"),
    "history": ("session_id",),
}


def main(argv=None):
    parser = argparse.ArgumentParser(description="AgentGranule local framework")
    parser.add_argument("--database", default=".agentgranule/project.sqlite3")
    parser.add_argument("operation", choices=OPERATIONS)
    parser.add_argument("arguments", help="JSON object with the operation's named arguments")
    args = parser.parse_args(argv)
    try:
        arguments = json.loads(args.arguments)
        if not isinstance(arguments, dict) or set(arguments) - set(OPERATIONS[args.operation]):
            raise GranuleError("Invalid operation arguments")
        with Project(args.database) as project:
            result = getattr(project, args.operation)(**arguments)
        print(json.dumps({"result": result}, ensure_ascii=False))
        return 0
    except (GranuleError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
