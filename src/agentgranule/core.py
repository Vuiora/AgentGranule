"""Persistent problems, human controls, execution plans and conversation events."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class GranuleError(ValueError):
    """A request violates the public domain contract."""


def _text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GranuleError(f"{field} must be a non-empty string")
    return value


class Project:
    """Single local project database. Use one instance per thread/connection.

    All successful mutations and rejected classification results are recorded.
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
            CREATE TABLE IF NOT EXISTS controls (
                problem_id TEXT PRIMARY KEY REFERENCES problems(id),
                count INTEGER NOT NULL CHECK(count > 0), revision INTEGER NOT NULL
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
        """)

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
        if not self._db.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone():
            raise GranuleError(f"Unknown session: {session_id}")

    def _problem(self, problem_id: str):
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
        if role not in {"user", "assistant", "tool", "system"}:
            raise GranuleError("role must be user, assistant, tool or system")
        if not isinstance(content, str):
            raise GranuleError("content must be a string")
        with self._db:
            self._event(session_id, "conversation.message", {"role": role, "content": content})

    def set_granularity(self, problem_id: str, count: int, actor: str) -> dict:
        """Record a human-authored classification count; actor is an audit label."""
        problem = self._problem(problem_id)
        if type(count) is not int or count < 1:
            raise GranuleError("count must be a positive integer")
        _text(actor, "actor")
        with self._db:
            # Serialize read/modify/write so concurrent callers cannot reuse a revision.
            self._db.execute("BEGIN IMMEDIATE")
            old = self._db.execute("SELECT * FROM controls WHERE problem_id = ?", (problem_id,)).fetchone()
            revision = old["revision"] + 1 if old else 1
            self._db.execute("""
                INSERT INTO controls VALUES (?, ?, ?)
                ON CONFLICT(problem_id) DO UPDATE SET count=excluded.count, revision=excluded.revision
            """, (problem_id, count, revision))
            control = {"problem_id": problem_id, "direction": "classification", "count": count,
                       "revision": revision, "actor": actor, "previous_count": old["count"] if old else None}
            self._event(problem["session_id"], "granularity.changed", control)
        return control

    def prepare_plan(self, problem_id: str) -> dict:
        problem = self._problem(problem_id)
        control = self._db.execute("SELECT * FROM controls WHERE problem_id = ?", (problem_id,)).fetchone()
        if control is None:
            raise GranuleError("Human granularity must be set before planning")
        plan = {"plan_id": uuid4().hex, "session_id": problem["session_id"],
                "problem_id": problem_id, "parent_id": problem["parent_id"],
                "description": problem["description"], "direction": "classification",
                "count": control["count"], "revision": control["revision"],
                "instruction": f"Classify the specified problem into exactly {control['count']} categories."}
        with self._db:
            self._db.execute("INSERT INTO plans VALUES (?, ?, ?)",
                             (plan["plan_id"], plan["session_id"], json.dumps(plan, ensure_ascii=False)))
            self._event(plan["session_id"], "plan.prepared", plan)
        return plan

    def submit_result(self, plan_id: str, categories: list[str]) -> dict:
        row = self._db.execute("SELECT payload FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if row is None:
            raise GranuleError(f"Unknown plan: {plan_id}")
        plan = json.loads(row["payload"])
        # Preserve the submitted result even when validation rejects it.
        try:
            json.dumps(categories, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise GranuleError("categories must be JSON-serializable") from exc
        with self._db:
            # A human update cannot interleave revision validation and result acceptance.
            self._db.execute("BEGIN IMMEDIATE")
            control = self._db.execute("SELECT revision FROM controls WHERE problem_id = ?",
                                       (plan["problem_id"],)).fetchone()
            error = None
            if control["revision"] != plan["revision"]:
                error = "Granularity changed; prepare a new plan"
            elif not isinstance(categories, list) or any(not isinstance(c, str) or not c.strip() for c in categories):
                error = "categories must be a list of non-empty strings"
            elif len(categories) != plan["count"]:
                error = f"Expected exactly {plan['count']} categories"
            result = {"plan_id": plan_id, "categories": categories, "accepted": error is None, "error": error}
            self._event(plan["session_id"], "result.submitted", result)
        if error:
            raise GranuleError(error)
        return result

    def history(self, session_id: str) -> list[dict]:
        self._session(session_id)
        return [{"sequence": row["sequence"], "timestamp": row["timestamp"],
                 "kind": row["kind"], "payload": json.loads(row["payload"])}
                for row in self._db.execute("SELECT * FROM events WHERE session_id = ? ORDER BY sequence", (session_id,))]
