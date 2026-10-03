"""Module-scoped processing detail, defaults, plans and visible conversation events."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4


class GranuleError(ValueError):
    """A request violates the public domain contract."""


def _text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GranuleError(f"{field} must be a non-empty string")
    return value


def _design_effort_units(value) -> int:
    """Validate a JSON number on the 0.00–1.00 hundredth grid, without rounding."""
    if type(value) not in (int, float):
        raise GranuleError("design_effort must be a number from 0.00 to 1.00 in steps of 0.01")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise GranuleError("design_effort must be a finite number") from exc
    if not decimal.is_finite() or not 0 <= decimal <= 1:
        raise GranuleError("design_effort must be from 0.00 to 1.00")
    # Integer ratios stay exact even if the host lowers its Decimal context precision.
    numerator, denominator = decimal.as_integer_ratio()
    units, remainder = divmod(numerator * 100, denominator)
    if remainder:
        raise GranuleError("design_effort must use steps of 0.01; no automatic rounding")
    return units


def _parameters(value: dict) -> dict:
    if not isinstance(value, dict) or not value or any(not isinstance(k, str) or not k.strip() for k in value):
        raise GranuleError("parameters must be a non-empty object with non-empty string keys")
    for key in ("count", "max_depth"):
        if key in value and (type(value[key]) is not int or value[key] < 1):
            raise GranuleError(f"{key} must be a positive integer")
    if "detail_level" in value:
        _text(value["detail_level"], "detail_level")
    if "design_effort" in value:
        value = {**value, "design_effort": _design_effort_units(value["design_effort"]) / 100}
    try:
        # Copy caller-owned structures and reject non-JSON data / non-finite numbers.
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, RecursionError) as exc:
        raise GranuleError("parameters must be finite JSON data") from exc


class Project:
    """Single local project database. Use one instance per thread/connection.

    Granularity is processing detail; category counts are just one parameter.
    All successful mutations and rejected domain results are recorded.
    Hosts must explicitly forward messages; this is not an external chat recorder.
    """

    def __init__(self, database: str | Path = ".agentgranule/project.sqlite3"):
        if str(database) != ":memory:":
            Path(database).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(database))
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, title TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS problems (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                parent_id TEXT REFERENCES problems(id), description TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS module_controls (
                problem_id TEXT NOT NULL REFERENCES problems(id), direction TEXT NOT NULL,
                parameters TEXT NOT NULL, revision INTEGER NOT NULL,
                PRIMARY KEY(problem_id, direction)
            );
            CREATE TABLE IF NOT EXISTS granularity_defaults (
                direction TEXT PRIMARY KEY, parameters TEXT NOT NULL, revision INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS plans (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES sessions(id),
                timestamp TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS events_session_sequence
                ON events(session_id, sequence);
        """)
        # Preserve v0.1 count-only databases and historical events/plans in place.
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            if self._db.execute("PRAGMA user_version").fetchone()[0] < 2:
                legacy = self._db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='controls'").fetchone()
                if legacy:
                    for row in self._db.execute("SELECT * FROM controls").fetchall():
                        self._db.execute("INSERT OR IGNORE INTO module_controls VALUES (?, ?, ?, ?)",
                                         (row["problem_id"], "classification", json.dumps({"count": row["count"]}), row["revision"]))
                self._db.execute("PRAGMA user_version = 2")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self._db.close()

    def _event(self, session_id: str, kind: str, payload: dict):
        self._db.execute(
            "INSERT INTO events(session_id, timestamp, kind, payload) VALUES (?, ?, ?, ?)",
            (session_id, datetime.now(timezone.utc).isoformat(), kind,
             json.dumps(payload, ensure_ascii=False, allow_nan=False)),
        )

    def _session(self, session_id: str):
        _text(session_id, "session_id")
        if not self._db.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone():
            raise GranuleError(f"Unknown session: {session_id}")

    def _problem(self, problem_id: str):
        _text(problem_id, "problem_id")
        problem = self._db.execute("SELECT * FROM problems WHERE id = ?", (problem_id,)).fetchone()
        if problem is None:
            raise GranuleError(f"Unknown problem: {problem_id}")
        return problem

    def create_session(self, title: str) -> str:
        _text(title, "title")
        session_id = uuid4().hex
        with self._db:
            self._db.execute("INSERT INTO sessions VALUES (?, ?)", (session_id, title))
            self._event(session_id, "session.created", {"title": title})
        return session_id

    def add_problem(self, session_id: str, description: str, parent_id: str | None = None) -> str:
        self._session(session_id)
        _text(description, "description")
        if parent_id is not None and self._problem(parent_id)["session_id"] != session_id:
            raise GranuleError("Parent problem must belong to the same session")
        problem_id = uuid4().hex
        with self._db:
            self._db.execute("INSERT INTO problems VALUES (?, ?, ?, ?)",
                             (problem_id, session_id, parent_id, description))
            self._event(session_id, "problem.created", {
                "problem_id": problem_id, "parent_id": parent_id, "description": description,
            })
        return problem_id

    def record_message(self, session_id: str, role: str, content: str) -> None:
        self._session(session_id)
        if not isinstance(role, str) or role not in {"user", "assistant", "tool", "system"}:
            raise GranuleError("role must be user, assistant, tool or system")
        if not isinstance(content, str):
            raise GranuleError("content must be a string")
        with self._db:
            self._event(session_id, "conversation.message", {"role": role, "content": content})

    def add_module(self, session_id: str, description: str, parent_id: str | None = None) -> str:
        """A module is a independently controlled part of a problem; nesting is optional."""
        return self.add_problem(session_id, description, parent_id)

    @staticmethod
    def _control_state(problem_id: str, direction: str, row) -> dict:
        if row["module_parameters"] is not None:
            parameters = json.loads(row["module_parameters"])
            source, revision = "module", row["module_revision"]
        elif row["default_parameters"] is not None:
            parameters = json.loads(row["default_parameters"])
            source, revision = "project_default", row["default_revision"]
        else:
            # Product defaults are proposals, not values explicitly chosen by a user.
            parameters = {"detail_level": "standard"}
            if direction in {"classification", "enumeration", "advantages", "disadvantages"}:
                parameters["count"] = 3
            source, revision = "builtin_default", 0
        return {"problem_id": problem_id, "direction": direction, "parameters": parameters,
                "count": parameters.get("count"), "source": source, "revision": revision}

    def _effective(self, problem_id: str, direction: str) -> dict:
        _text(direction, "direction")
        row = self._db.execute("""SELECT
            c.parameters AS module_parameters, c.revision AS module_revision,
            d.parameters AS default_parameters, d.revision AS default_revision
            FROM (SELECT ? AS problem_id, ? AS direction) AS requested
            LEFT JOIN module_controls AS c ON c.problem_id=requested.problem_id
                AND c.direction=requested.direction
            LEFT JOIN granularity_defaults AS d ON d.direction=requested.direction
        """, (problem_id, direction)).fetchone()
        return self._control_state(problem_id, direction, row)

    def _granularity_batch(self, pairs):
        """Yield fresh module/control pairs in caller order, without a state cache.

        Callers hold their domain transaction. Each chunk uses at most 900
        bindings, including on SQLite builds with a 999-variable limit.
        Decode each row separately so inherited nested parameters never alias.
        """
        pairs = list(pairs)
        for start in range(0, len(pairs), 450):
            chunk = pairs[start:start + 450]
            arguments = []
            for problem_id, direction in chunk:
                arguments.extend((_text(problem_id, "problem_id"), _text(direction, "direction")))
            requested = ",".join(f"({ordinal},?,?)" for ordinal in range(len(chunk)))
            rows = self._db.execute(f"""WITH requested(ordinal, problem_id, direction) AS
                (VALUES {requested})
                SELECT r.problem_id, r.direction, p.id AS actual_id,
                    p.session_id, p.parent_id, p.description,
                    c.parameters AS module_parameters, c.revision AS module_revision,
                    d.parameters AS default_parameters, d.revision AS default_revision
                FROM requested AS r
                LEFT JOIN problems AS p ON p.id=r.problem_id
                LEFT JOIN module_controls AS c ON c.problem_id=r.problem_id AND c.direction=r.direction
                LEFT JOIN granularity_defaults AS d ON d.direction=r.direction
                ORDER BY r.ordinal
            """, arguments).fetchall()
            for row in rows:
                if row["actual_id"] is None:
                    raise GranuleError(f"Unknown problem: {row['problem_id']}")
                problem = {"id": row["actual_id"], "session_id": row["session_id"],
                           "parent_id": row["parent_id"], "description": row["description"]}
                yield problem, self._control_state(row["problem_id"], row["direction"], row)

    def get_granularity(self, problem_id: str, direction: str = "classification") -> dict:
        self._problem(problem_id)
        return self._effective(problem_id, direction)

    def request_granularity(self, problem_id: str, direction: str = "classification") -> dict:
        """Create a question for the host to present; never fabricate a human answer."""
        problem = self._problem(problem_id)
        current = self._effective(problem_id, direction)
        question = (f"模块“{problem['description']}”的 {direction} 方向希望列举多少项？"
                    if current["count"] is not None else
                    f"模块“{problem['description']}”的 {direction} 方向需要怎样的详细程度？")
        request = {**current, "question": question + " 当前建议参数：" + json.dumps(current["parameters"], ensure_ascii=False)}
        with self._db:
            self._event(problem["session_id"], "granularity.requested", request)
        return request

    def set_default_granularity(self, session_id: str, direction: str, parameters: dict, actor: str) -> dict:
        """Set this project's default for a direction; session_id locates the audit record."""
        self._session(session_id)
        _text(direction, "direction")
        _text(actor, "actor")
        parameters = _parameters(parameters)
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            old = self._db.execute("SELECT * FROM granularity_defaults WHERE direction=?", (direction,)).fetchone()
            revision = old["revision"] + 1 if old else 1
            self._db.execute("""INSERT INTO granularity_defaults VALUES (?, ?, ?)
                ON CONFLICT(direction) DO UPDATE SET parameters=excluded.parameters, revision=excluded.revision
            """, (direction, json.dumps(parameters, ensure_ascii=False), revision))
            result = {"direction": direction, "parameters": parameters, "revision": revision, "actor": actor,
                      "previous_parameters": json.loads(old["parameters"]) if old else None}
            self._event(session_id, "granularity.default_changed", result)
        return result

    def set_granularity(self, problem_id: str, count: int | None = None, actor: str | None = None,
                        direction: str = "classification", parameters: dict | None = None) -> dict:
        """Replace this module/direction's human override. count is a legacy shorthand."""
        problem = self._problem(problem_id)
        _text(direction, "direction")
        if count is not None and parameters is not None:
            raise GranuleError("Use either count shorthand or parameters, not both")
        parameters = _parameters({"count": count} if parameters is None else parameters)
        _text(actor, "actor")
        with self._db:
            # Serialize read/modify/write so concurrent callers cannot reuse a revision.
            self._db.execute("BEGIN IMMEDIATE")
            return self._set_granularity_locked(problem_id, direction, parameters, actor)

    def _set_granularity_locked(self, problem_id: str, direction: str, parameters: dict, actor: str) -> dict:
        """Apply validated parameters while the caller holds BEGIN IMMEDIATE."""
        problem = self._problem(problem_id)
        old = self._db.execute("SELECT * FROM module_controls WHERE problem_id=? AND direction=?",
                               (problem_id, direction)).fetchone()
        revision = old["revision"] + 1 if old else 1
        self._db.execute("""
            INSERT INTO module_controls VALUES (?, ?, ?, ?)
            ON CONFLICT(problem_id, direction) DO UPDATE SET parameters=excluded.parameters, revision=excluded.revision
        """, (problem_id, direction, json.dumps(parameters, ensure_ascii=False), revision))
        previous = json.loads(old["parameters"]) if old else None
        control = {**self._effective(problem_id, direction), "actor": actor,
                   "previous_parameters": previous, "previous_count": previous.get("count") if previous else None}
        self._event(problem["session_id"], "granularity.changed", control)
        return control

    def prepare_plan(self, problem_id: str, direction: str = "classification") -> dict:
        problem = self._problem(problem_id)
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            control = self._effective(problem_id, direction)
            instruction = f"Process module {problem['description']!r} in direction {direction!r}. Granularity: "
            instruction += json.dumps(control["parameters"], ensure_ascii=False)
            if control["count"] is not None:
                instruction += f". Return exactly {control['count']} items."
            plan = {**control, "plan_id": uuid4().hex, "session_id": problem["session_id"],
                    "parent_id": problem["parent_id"], "description": problem["description"],
                    "uses_default": control["source"] != "module", "instruction": instruction}
            self._db.execute("INSERT INTO plans VALUES (?, ?, ?)",
                             (plan["plan_id"], plan["session_id"], json.dumps(plan, ensure_ascii=False)))
            self._event(plan["session_id"], "plan.prepared", plan)
        return plan

    def submit_result(self, plan_id: str, categories: list[str] | None = None, output: dict | None = None) -> dict:
        _text(plan_id, "plan_id")
        row = self._db.execute("SELECT payload FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if row is None:
            raise GranuleError(f"Unknown plan: {plan_id}")
        plan = json.loads(row["payload"])
        if categories is not None and output is not None:
            raise GranuleError("Use categories shorthand or output, not both")
        submitted = {"items": categories} if output is None else output
        # Preserve the submitted result even when domain validation rejects it.
        try:
            json.dumps(submitted, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise GranuleError("output must be JSON-serializable") from exc
        with self._db:
            # A human update cannot interleave revision validation and result acceptance.
            self._db.execute("BEGIN IMMEDIATE")
            control = self._effective(plan["problem_id"], plan["direction"])
            parameters = plan.get("parameters", {"count": plan["count"]})
            items = submitted.get("items") if isinstance(submitted, dict) else None
            error = None
            if (control["revision"], control["source"]) != (plan["revision"], plan.get("source", "module")):
                error = "Granularity changed; prepare a new plan"
            elif not isinstance(submitted, dict) or not submitted:
                error = "output must be a non-empty object"
            elif "count" in parameters:
                if not isinstance(items, list) or any(not isinstance(c, str) or not c.strip() for c in items):
                    error = "items must be a list of non-empty strings"
                elif len(items) != parameters["count"]:
                    error = f"Expected exactly {parameters['count']} items"
            elif not isinstance(submitted.get("text"), str) or not submitted["text"].strip():
                error = "Non-enumeration output must contain non-empty text"
            result = {"plan_id": plan_id, "categories": categories, "output": submitted,
                      "accepted": error is None, "error": error}
            self._event(plan["session_id"], "result.submitted", result)
        if error:
            raise GranuleError(error)
        return result

    def history(self, session_id: str) -> list[dict]:
        self._session(session_id)
        return [{"sequence": row["sequence"], "timestamp": row["timestamp"],
                 "kind": row["kind"], "payload": json.loads(row["payload"])}
                for row in self._db.execute("SELECT * FROM events WHERE session_id = ? ORDER BY sequence", (session_id,))]
