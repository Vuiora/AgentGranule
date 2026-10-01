"""Dependency planning, granularity compilation and incremental local execution.

Handlers are explicitly registered Python callables. No provider, dynamic import,
or natural-language problem decomposition is assumed by this algorithm layer.
"""

from __future__ import annotations

import copy
import hashlib
import heapq
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .core import GranuleError, Project, _parameters, _text


ALGORITHM_VERSION = "round-1-v2"


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise GranuleError("Execution data must be finite, acyclic JSON") from exc


@dataclass(frozen=True)
class Task:
    """One module/direction operation; dependency edges are explicit task ids."""

    id: str
    module_id: str
    direction: str
    depends_on: tuple[str, ...] = ()

    def __post_init__(self):
        for name in ("id", "module_id", "direction"):
            _text(getattr(self, name), name)
        if not isinstance(self.depends_on, (list, tuple)):
            raise GranuleError("depends_on must be a list or tuple of task ids")
        for name in self.depends_on:
            _text(name, "dependency")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise GranuleError("Duplicate dependencies are not allowed")
        object.__setattr__(self, "depends_on", tuple(sorted(self.depends_on)))


def topological_order(tasks: Iterable[Task]) -> list[Task]:
    """Kahn's algorithm, with lexicographic ties for reproducible schedules."""
    tasks = list(tasks)
    if not tasks or any(not isinstance(task, Task) for task in tasks):
        raise GranuleError("A workflow requires one or more Task objects")
    by_id = {task.id: task for task in tasks}
    if len(by_id) != len(tasks):
        raise GranuleError("Task ids must be unique")
    incoming = {task.id: len(task.depends_on) for task in tasks}
    successors = {task.id: [] for task in tasks}
    for task in tasks:
        for dependency in task.depends_on:
            if dependency not in by_id:
                raise GranuleError(f"Unknown dependency {dependency!r} for {task.id!r}")
            successors[dependency].append(task.id)
    ready = [name for name, count in incoming.items() if count == 0]
    heapq.heapify(ready)
    ordered = []
    while ready:
        name = heapq.heappop(ready)
        ordered.append(by_id[name])
        for successor in successors[name]:
            incoming[successor] -= 1
            if incoming[successor] == 0:
                heapq.heappush(ready, successor)
    if len(ordered) != len(tasks):
        unresolved = sorted(name for name, count in incoming.items() if count)
        raise GranuleError(f"Dependency cycle; unresolved tasks: {unresolved}")
    return ordered


def compile_constraints(parameters: dict) -> dict:
    """Compile concrete constraints without equating granularity with a number.

    Semantic detail labels, design effort and custom parameters remain explicit handler inputs.
    item_count and max_depth have mechanical output checks in this iteration.
    """
    parameters = _parameters(parameters)
    return {
        "item_count": parameters.get("count"),
        "detail_level": parameters.get("detail_level"),
        "max_depth": parameters.get("max_depth"),
        "design_effort": parameters.get("design_effort"),
        "output_kind": "items" if "count" in parameters else "text",
        "custom": {key: value for key, value in parameters.items()
                   if key not in {"count", "detail_level", "max_depth", "design_effort"}},
    }


def validate_output(output: dict, constraints: dict) -> None:
    """Check output cardinality and optional explicit detail-tree depth.

    details is a list of {text, children?} nodes. Roots have depth 1. max_depth
    is an upper bound, not a request to invent levels or additional content.
    """
    _json(output)
    if not isinstance(output, dict) or not output:
        raise GranuleError("output must be a non-empty object")
    if constraints["item_count"] is not None:
        items = output.get("items")
        if not isinstance(items, list) or any(not isinstance(item, str) or not item.strip() for item in items):
            raise GranuleError("items must be a list of non-empty strings")
        if len(items) != constraints["item_count"]:
            raise GranuleError(f"Expected exactly {constraints['item_count']} items")
    elif not isinstance(output.get("text"), str) or not output["text"].strip():
        raise GranuleError("Non-enumeration output must contain non-empty text")
    if "details" not in output:
        return
    if not isinstance(output["details"], list):
        raise GranuleError("details must be a list of detail nodes")
    pending = [(node, 1) for node in output["details"]]
    while pending:
        node, depth = pending.pop()
        if not isinstance(node, dict) or set(node) - {"text", "children"}:
            raise GranuleError("Detail nodes require text and optional children only")
        _text(node.get("text"), "detail text")
        children = node.get("children", [])
        if not isinstance(children, list):
            raise GranuleError("Detail children must be a list")
        if constraints["max_depth"] is not None and depth > constraints["max_depth"]:
            raise GranuleError(f"Detail depth exceeds max_depth={constraints['max_depth']}")
        pending.extend((child, depth + 1) for child in children)


class AlgorithmRunner:
    """Sequential dependency scheduler with durable, provenance-aware caching.

    Handler signature: handler({task, plan, constraints, inputs, context}) -> output dict.
    Registry versions are supplied by the host and must change when behavior does.
    Successful tasks are cached; failures block dependents but not unrelated tasks.
    """

    def __init__(self, project: Project):
        self.project = project
        self._handlers: dict[str, tuple[Callable, str]] = {}
        self.project._db.execute("""CREATE TABLE IF NOT EXISTS algorithm_cache (
            fingerprint TEXT PRIMARY KEY, payload TEXT NOT NULL
        )""")
        self.project._db.commit()

    def register(self, direction: str, handler: Callable, *, version: str) -> None:
        _text(direction, "direction")
        _text(version, "handler version")
        if not callable(handler):
            raise GranuleError("handler must be callable")
        old = self._handlers.get(direction)
        if old and old[0] is not handler and old[1] == version:
            raise GranuleError("A replacement handler requires a new version")
        self._handlers[direction] = handler, version

    def _record(self, session_id: str, kind: str, payload: dict) -> None:
        with self.project._db:
            self.project._event(session_id, kind, payload)

    def _state(self, task: Task) -> dict:
        return self.project.get_granularity(task.module_id, task.direction)

    def _fingerprint(self, task: Task, state: dict, results: dict, context: dict) -> str:
        payload = {"algorithm": ALGORITHM_VERSION, "task": vars(task), "state": state,
                   "handler_version": self._handlers[task.direction][1], "context": context,
                   "dependencies": {name: results[name]["fingerprint"] for name in task.depends_on}}
        return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()

    def _fresh(self, name: str, by_id: dict, results: dict, visited=None) -> bool:
        # Includes transitive dependencies so late human changes cannot feed stale inputs downstream.
        visited = set() if visited is None else visited
        pending = [name]
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            result = results[current]
            if result["status"] not in {"completed", "cached"}:
                return False
            task = by_id[current]
            state = self._state(task)
            if state != result["state"] or self._handlers[task.direction][1] != result["handler_version"]:
                return False
            pending.extend(task.depends_on)
        return True

    def run(self, session_id: str, tasks: Iterable[Task], *, context: dict | None = None) -> dict:
        """Execute a validated DAG; returned complete is false if any task failed/blocked/staled."""
        self.project._session(session_id)
        if context is not None and not isinstance(context, dict):
            raise GranuleError("context must be an object")
        context = json.loads(_json({} if context is None else context))
        ordered = topological_order(tasks)
        by_id = {task.id: task for task in ordered}
        # Resolve every module and handler before invoking anything or writing plans.
        for task in ordered:
            if self.project._problem(task.module_id)["session_id"] != session_id:
                raise GranuleError("All workflow modules must belong to the supplied session")
            if task.direction not in self._handlers:
                raise GranuleError(f"No handler registered for direction {task.direction!r}")
        from uuid import uuid4

        run_id = uuid4().hex
        results = {}
        self._record(session_id, "algorithm.run_started", {"run_id": run_id, "order": [task.id for task in ordered], "context": context})
        for task in ordered:
            stale_dependencies = [name for name in task.depends_on if not self._fresh(name, by_id, results)]
            if stale_dependencies:
                results[task.id] = {"status": "blocked", "error": f"Unavailable or stale dependencies: {stale_dependencies}"}
                self._record(session_id, "algorithm.task_blocked", {"run_id": run_id, "task_id": task.id, **results[task.id]})
                continue
            state = self._state(task)
            handler, version = self._handlers[task.direction]
            fingerprint = self._fingerprint(task, state, results, context)
            row = self.project._db.execute("SELECT payload FROM algorithm_cache WHERE fingerprint=?", (fingerprint,)).fetchone()
            if row:
                cached = json.loads(row["payload"])
                results[task.id] = {**cached, "status": "cached"}
                self._record(session_id, "algorithm.task_cached", {"run_id": run_id, "task_id": task.id, **cached})
                continue
            # The plan is the authoritative snapshot; compile from it rather than a prior read.
            plan = self.project.prepare_plan(task.module_id, task.direction)
            state = {key: plan[key] for key in state}
            fingerprint = self._fingerprint(task, state, results, context)
            request = {"task": vars(task), "plan": plan, "context": context, "constraints": compile_constraints(plan["parameters"]),
                       "inputs": {name: results[name]["output"] for name in task.depends_on}}
            self._record(session_id, "algorithm.task_started", {"run_id": run_id, "task_id": task.id, "request": request})
            output = None
            try:
                output = handler(copy.deepcopy(request))
                # Freeze output against caller mutation and ensure its original value can be recorded.
                output = json.loads(_json(output))
                validate_output(output, request["constraints"])
                if any(not self._fresh(name, by_id, results) for name in task.depends_on):
                    raise GranuleError("Dependency granularity changed during execution")
                self.project.submit_result(plan["plan_id"], output=output)
            except Exception as exc:
                # An unrecordable output gets a marker rather than breaking failure propagation.
                try:
                    _json(output)
                except GranuleError:
                    output = {"unrecordable": type(output).__name__}
                failed = {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "output": output, "plan": plan}
                results[task.id] = failed
                self._record(session_id, "algorithm.task_failed", {"run_id": run_id, "task_id": task.id, **failed})
                continue
            completed = {"status": "completed", "output": output, "plan": plan, "state": state,
                         "fingerprint": fingerprint, "handler_version": version}
            with self.project._db:
                self.project._db.execute("INSERT OR REPLACE INTO algorithm_cache VALUES (?, ?)", (fingerprint, _json(completed)))
                self.project._event(session_id, "algorithm.task_completed", {"run_id": run_id, "task_id": task.id, **completed})
            results[task.id] = completed
        # Re-check every output at delivery time, including leaves without downstream tasks.
        for task in ordered:
            if results[task.id]["status"] in {"completed", "cached"} and not self._fresh(task.id, by_id, results):
                results[task.id]["status"] = "stale"
                results[task.id]["error"] = "Effective granularity or dependency changed before delivery"
                self._record(session_id, "algorithm.task_stale", {"run_id": run_id, "task_id": task.id})
        report = {"run_id": run_id, "order": [task.id for task in ordered], "tasks": results,
                  "outputs": {name: result["output"] for name, result in results.items()
                              if result["status"] in {"completed", "cached"}},
                  "complete": all(result["status"] in {"completed", "cached"} for result in results.values())}
        self._record(session_id, "algorithm.run_finished", {"run_id": run_id, "complete": report["complete"],
                     "statuses": {name: result["status"] for name, result in results.items()}})
        return report
