"""Module facade and declarative, allowlisted suite execution."""

import inspect
import math
import re

from .cli import OPERATIONS
from .core import GranuleError, Project

__all__ = ["GranuleError", "Project", "SuiteRunner", "load_yaml"]

_FIELDS = {
    "set_granularity": {"problem_id", "direction", "count", "revision", "actor", "previous_count"},
    "prepare_plan": {"plan_id", "session_id", "problem_id", "parent_id", "description", "direction",
                     "count", "revision", "instruction"},
    "submit_result": {"plan_id", "categories", "accepted", "error"},
}


def load_yaml(text: str) -> dict:
    """Load YAML data only; reject duplicate keys, unsafe tags and large inputs."""
    try:
        import yaml
    except ImportError as exc:
        raise GranuleError('YAML support requires pip install "agentgranule[yaml]"') from exc
    if not isinstance(text, str) or len(text.encode("utf-8")) > 1_000_000:
        raise GranuleError("YAML input must be text of at most 1 MB")

    class Loader(yaml.SafeLoader):
        pass

    def mapping(loader, node):
        loader.flatten_mapping(node)
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node)
            if not isinstance(key, str) or key in result:
                raise GranuleError("YAML keys must be unique strings")
            result[key] = loader.construct_object(value_node)
        return result

    Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    try:
        result = yaml.load(text, Loader=Loader)
    except yaml.YAMLError as exc:
        raise GranuleError(f"Invalid YAML: {exc}") from exc
    if not isinstance(result, dict):
        raise GranuleError("Suite must be an object")
    return result


class SuiteRunner:
    """Validate structure first, then execute sequentially; not a batch transaction.

    Each core mutation is durable. A runtime failure stops the remaining steps;
    callers must inspect history before retrying to avoid duplicate messages.
    """

    def __init__(self, project: Project):
        self.project = project

    def _walk(self, value, seen, depth=0):
        if depth > 64:
            raise GranuleError("Suite nesting exceeds 64 levels or has a cyclic alias")
        if isinstance(value, dict):
            if "$ref" in value:
                if set(value) != {"$ref"} or not isinstance(value["$ref"], str):
                    raise GranuleError("A reference must contain only a string $ref")
                parts = value["$ref"].split(".")
                if len(parts) > 2 or parts[0] not in seen:
                    raise GranuleError("References must point to a previous step")
                if len(parts) == 2 and parts[1] not in _FIELDS.get(seen[parts[0]], set()):
                    raise GranuleError(f"Unknown result field: {value['$ref']}")
            else:
                if any(not isinstance(key, str) for key in value):
                    raise GranuleError("Argument object keys must be strings")
                for item in value.values():
                    self._walk(item, seen, depth + 1)
        elif isinstance(value, list):
            for item in value:
                self._walk(item, seen, depth + 1)
        elif value is not None and type(value) not in {str, int, float, bool}:
            raise GranuleError("Arguments must contain JSON-compatible data")
        elif isinstance(value, float) and not math.isfinite(value):
            raise GranuleError("Non-finite numbers are not supported")

    def validate(self, suite: dict) -> None:
        if not isinstance(suite, dict) or set(suite) != {"version", "steps"}:
            raise GranuleError("Suite requires only version and steps")
        if type(suite["version"]) is not int or suite["version"] != 1:
            raise GranuleError("Unsupported suite version; expected 1")
        steps = suite["steps"]
        if not isinstance(steps, list) or not 1 <= len(steps) <= 1000:
            raise GranuleError("Suite requires between 1 and 1000 steps")
        seen = {}
        for step in steps:
            if not isinstance(step, dict) or set(step) != {"id", "operation", "arguments"}:
                raise GranuleError("Each step requires only id, operation and arguments")
            name, operation, arguments = step["id"], step["operation"], step["arguments"]
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", name) or name in seen:
                raise GranuleError("Step ids must be unique names without dots")
            if not isinstance(operation, str) or operation not in OPERATIONS:
                raise GranuleError(f"Unsupported operation: {operation}")
            if not isinstance(arguments, dict):
                raise GranuleError("arguments must be an object")
            try:
                inspect.signature(getattr(self.project, operation)).bind(**arguments)
            except TypeError as exc:
                raise GranuleError(f"Invalid arguments for {operation}: {exc}") from exc
            self._walk(arguments, seen)
            seen[name] = operation

    def _resolve(self, value, results):
        if isinstance(value, dict):
            if "$ref" in value:
                parts = value["$ref"].split(".")
                result = results[parts[0]]
                return result[parts[1]] if len(parts) == 2 else result
            return {key: self._resolve(item, results) for key, item in value.items()}
        if isinstance(value, list):
            return [self._resolve(item, results) for item in value]
        return value

    def run(self, suite: dict) -> dict:
        self.validate(suite)
        results = {}
        for step in suite["steps"]:
            arguments = self._resolve(step["arguments"], results)
            try:
                results[step["id"]] = getattr(self.project, step["operation"])(**arguments)
            except (GranuleError, TypeError) as exc:
                raise GranuleError(f"Step {step['id']} failed; prior steps remain committed: {exc}") from exc
        return results
