"""Repeatable API latency and SQL-count benchmark on disposable databases only.

Run with the project's Python environment, for example::

    python benchmarks/api_performance.py --output .agentgranule/api-before.json
    python benchmarks/api_performance.py --output .agentgranule/api-after.json

The MCP cases invoke FastMCP's real tool facade, including schema conversion and
per-call Project connections, but exclude stdio/network transport and startup.
This fixture contains synthetic approvals; it never opens the project's real DB.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import itertools
import json
from pathlib import Path
import platform
import sqlite3
import statistics
import subprocess
import sys
import tempfile
from time import perf_counter_ns
from unittest.mock import patch
from uuid import UUID


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentgranule.core import Project
from agentgranule.design import Design
from agentgranule.mcp_server import create_server


class SQLCounter:
    """Observe traced SQL on every connection opened by the tested facade."""

    def __init__(self):
        self.active = False
        self.statements = Counter()
        self.original_connect = sqlite3.connect

    def connect(self, *args, **kwargs):
        connection = self.original_connect(*args, **kwargs)
        connection.set_trace_callback(self.trace)
        return connection

    def trace(self, statement):
        if self.active:
            keyword = statement.lstrip().split(None, 1)[0].upper()
            # This benchmark's WITH statements are read-only bulk SELECTs.
            # Count their SQL round trips equally with the original SELECT form.
            # The baseline contains no WITH statements, so its counts stay valid.
            if keyword == "WITH":
                keyword = "SELECT"
            if keyword in {"SELECT", "INSERT", "UPDATE", "DELETE", "BEGIN", "COMMIT"}:
                self.statements[keyword] += 1


def json_value(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"Unsupported result type: {type(value)!r}")


def digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                         default=json_value).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def analysis_fixture(modules, directions):
    return {
        "summary": "Synthetic API benchmark; no user workflow approvals",
        "context": {"fixture": "api_performance_v1", "payload": "x" * 128},
        "modules": [
            {"id": f"m{i:05d}", "name": f"Module {i}", "description": "Synthetic module",
             "expected_output": "Synthetic text/items", "basis": "Benchmark fixture",
             "directions": list(directions),
             "depends_on": [f"m{i - 1:05d}"] if i else []}
            for i in range(modules)
        ],
    }


def persistent_state(project):
    """Read logical input outside the timed path; detect accidental mutations."""
    return {
        table: [list(row) for row in project._db.execute(f"SELECT * FROM {table} ORDER BY 1")]
        for table in ("sessions", "problems", "module_controls", "granularity_defaults",
                      "design_analyses", "workflows")
    } | {"events": list(project._db.execute(
        "SELECT count(*), max(sequence) FROM events").fetchone())}


def fixture(directory, modules, history_events):
    database = Path(directory) / "synthetic.sqlite3"
    project = Project(database)
    service = Design(project)
    session = project.create_session("Synthetic performance fixture")
    directions = ("classification", "enumeration", "explanation")
    analysis = analysis_fixture(modules, directions)
    proposed = service.propose_analysis(session, analysis)
    approved = service.approve_analysis(proposed["analysis_id"], 1, "benchmark:synthetic")
    snapshot = service.allocation_snapshot(approved["analysis_id"])
    choices = [{"module_id": item["module_id"], "direction": item["direction"],
                "design_effort": ((index % 100) + 1) / 100}
               for index, item in enumerate(snapshot["controls"])]
    saved = service.save_allocation(snapshot, choices, "benchmark:synthetic")
    fallback_module = project.add_module(session, "Synthetic fallback-only module")
    project.set_default_granularity(session, "summary", {"detail_level": "brief"},
                                    "benchmark:synthetic")
    # Batch only synthetic history construction, avoiding thousands of disk commits.
    # This is fixture setup, excluded from all timed/SQL-counted calls.
    with project._db:
        for index in range(history_events):
            project._event(session, "conversation.message", {
                "role": "tool", "content": f"Synthetic historical message {index}: " + "x" * 128})
    # Dispatch once before measurement: repeated next_task must keep the same request.
    service.workflow.next_task(saved["workflow_id"])
    return project, service, {
        "database": str(database), "session": session, "analysis_id": approved["analysis_id"],
        "workflow_id": saved["workflow_id"], "module_id": approved["module_ids"]["m00000"],
        "fallback_module": fallback_module, "directions": directions,
        "fixture_digest": digest(analysis), "actual_events": project._db.execute(
            "SELECT count(*) FROM events").fetchone()[0],
    }


async def measure(operation, repeats, warmups, counter):
    def invoke():
        result = operation()
        return result

    expected = None
    for _ in range(warmups):
        result = invoke()
        if asyncio.iscoroutine(result):
            result = await result
        current = digest(result)
        if expected is not None and expected != current:
            raise AssertionError("Warmup changed the returned result")
        expected = current
    durations, sql_counts = [], []
    for _ in range(repeats):
        counter.statements.clear()
        counter.active = True
        started = perf_counter_ns()
        try:
            result = invoke()
            if asyncio.iscoroutine(result):
                result = await result
            elapsed = perf_counter_ns() - started
        finally:
            counter.active = False
        current = digest(result)
        if expected is not None and expected != current:
            raise AssertionError("Measured call changed the returned result")
        expected = current
        durations.append(elapsed / 1_000_000)
        sql_counts.append(dict(counter.statements))
    keywords = sorted({key for counts in sql_counts for key in counts})
    ordered = sorted(durations)
    return {
        "repeats": repeats, "warmups": warmups,
        "median_ms": round(statistics.median(durations), 6),
        "min_ms": round(min(durations), 6), "max_ms": round(max(durations), 6),
        "p95_ms": round(ordered[max(0, (95 * len(ordered) + 99) // 100 - 1)], 6),
        "sql_counts_per_call": {
            key: {"median": statistics.median(counts.get(key, 0) for counts in sql_counts),
                  "min": min(counts.get(key, 0) for counts in sql_counts),
                  "max": max(counts.get(key, 0) for counts in sql_counts)}
            for key in keywords
        },
        "result_digest": expected,
    }


async def run_scenario(modules, history_events, repeats, warmups):
    counter = SQLCounter()
    ids = itertools.count(1)
    next_uuid = lambda: UUID(int=next(ids))
    with tempfile.TemporaryDirectory(prefix="agentgranule-api-benchmark-") as directory:
        with ExitStack() as stack:
            stack.enter_context(patch("agentgranule.core.sqlite3.connect", side_effect=counter.connect))
            for module in ("core", "design", "workflow"):
                stack.enter_context(patch(f"agentgranule.{module}.uuid4", side_effect=next_uuid))
            project, service, data = fixture(directory, modules, history_events)
            stack.callback(project.close)
            server = create_server(data["database"])
            operations = {
                "project.get_granularity.override": lambda: project.get_granularity(
                    data["module_id"], "classification"),
                "project.get_granularity.project_default": lambda: project.get_granularity(
                    data["fallback_module"], "summary"),
                "project.get_granularity.builtin": lambda: project.get_granularity(
                    data["fallback_module"], "analysis"),
                "design.allocation_snapshot": lambda: service.allocation_snapshot(data["analysis_id"]),
                "workflow.workflow_status": lambda: service.workflow.workflow_status(data["workflow_id"]),
                "workflow.next_task.stable_request": lambda: service.workflow.next_task(data["workflow_id"]),
                "mcp.get_granularity": lambda: server.call_tool("get_granularity", {
                    "problem_id": data["module_id"], "direction": "classification"}),
                "mcp.allocation_snapshot": lambda: server.call_tool("allocation_snapshot", {
                    "analysis_id": data["analysis_id"]}),
                "mcp.workflow_status": lambda: server.call_tool("workflow_status", {
                    "workflow_id": data["workflow_id"]}),
                "mcp.next_task.stable_request": lambda: server.call_tool("next_task", {
                    "workflow_id": data["workflow_id"]}),
            }
            original = digest(persistent_state(project))
            results = {}
            for name, operation in operations.items():
                results[name] = await measure(operation, repeats, warmups, counter)
                if original != digest(persistent_state(project)):
                    raise AssertionError(f"{name} changed benchmark inputs")
            return {
                "modules": modules, "directions_per_module": len(data["directions"]),
                "tasks": modules * len(data["directions"]),
                "workflow_state": "One stable dispatched request; no accepted outputs; module dependency chain",
                "synthetic_history_events": history_events, "actual_event_rows": data["actual_events"],
                "fixture_digest": data["fixture_digest"], "input_unchanged": True,
                "input_digest": original, "operations": results,
            }


def git_head():
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--modules", nargs="+", type=int, default=[100, 500])
    parser.add_argument("--history-per-module", type=int, default=100)
    parser.add_argument("--repeats", type=int, default=15)
    parser.add_argument("--warmups", type=int, default=3)
    args = parser.parse_args(argv)
    if any(size < 1 for size in args.modules) or args.history_per_module < 0 or args.repeats < 1 or args.warmups < 1:
        parser.error("modules/repeats/warmups must be positive; history-per-module must be nonnegative")
    output = {
        "benchmark_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_head": git_head(), "python": sys.version, "platform": platform.platform(),
        "sqlite": sqlite3.sqlite_version,
        "sql_count_method": "Trace callbacks; read-only WITH statements count as SELECT round trips; "
                            "equivalent to original SELECT counting for the baseline, which contains no WITH",
        "method": "Warm single-process medians; traced SELECT/write counts; synthetic disposable SQLite files; "
                  "MCP uses FastMCP.call_tool, excludes transport and startup; inputs checked after every case",
        "scenarios": [],
    }
    for size in args.modules:
        result = asyncio.run(run_scenario(size, size * args.history_per_module, args.repeats, args.warmups))
        output["scenarios"].append(result)
        print(json.dumps({"modules": size, "operations": {
            name: {"median_ms": measured["median_ms"], "SELECT": measured["sql_counts_per_call"].get("SELECT", {}).get("median", 0)}
            for name, measured in result["operations"].items()}}, ensure_ascii=False))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {args.output}")


if __name__ == "__main__":
    main()
