"""Durable host-driven workflows; semantic processing remains with the agent."""

import hashlib
import json
from uuid import uuid4

from .algorithms import Task, _json, compile_constraints, topological_order, validate_output
from .core import GranuleError, Project, _text


WORKFLOW_VERSION = 2  # Explicit design_effort constraints supersede the old request schema.


class Workflow:
    """One connection per caller, with atomic dispatch/acceptance and restart recovery."""

    def __init__(self, project: Project):
        self.project = project
        project._db.execute("""CREATE TABLE IF NOT EXISTS workflows (
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
            payload TEXT NOT NULL
        )""")
        project._db.commit()

    def create_workflow(self, session_id: str, tasks: list[dict], context: dict | None = None) -> dict:
        self.project._session(session_id)
        if not isinstance(tasks, list) or any(not isinstance(t, dict) for t in tasks):
            raise GranuleError("tasks must be a list of task objects")
        try:
            ordered = topological_order(Task(**task) for task in tasks)
        except TypeError as exc:
            raise GranuleError("Tasks require id, module_id, direction and optional depends_on") from exc
        if context is not None and not isinstance(context, dict):
            raise GranuleError("context must be an object")
        context = json.loads(_json({} if context is None else context))
        for task in ordered:
            if self.project._problem(task.module_id)["session_id"] != session_id:
                raise GranuleError("All modules must belong to the workflow session")
        workflow_id = uuid4().hex
        data = {"tasks": [vars(task) for task in ordered], "context": context,
                "results": {}, "requests": {}}
        with self.project._db:
            self.project._db.execute("INSERT INTO workflows VALUES (?, ?, ?)",
                                     (workflow_id, session_id, _json(data)))
            self.project._event(session_id, "workflow.created", {"workflow_id": workflow_id, **data})
        return {"workflow_id": workflow_id, "order": [task.id for task in ordered]}

    def _load(self, workflow_id):
        _text(workflow_id, "workflow_id")
        row = self.project._db.execute("SELECT * FROM workflows WHERE id=?", (workflow_id,)).fetchone()
        if row is None:
            raise GranuleError(f"Unknown workflow: {workflow_id}")
        return row["session_id"], json.loads(row["payload"])

    def _save(self, workflow_id, data):
        self.project._db.execute("UPDATE workflows SET payload=? WHERE id=?", (_json(data), workflow_id))

    def _refresh(self, workflow_id, session_id, data):
        """Invalidate changed tasks and descendants in topological order under the lock."""
        states, fingerprints = {}, {}
        for task in data["tasks"]:
            name = task["id"]
            state = self.project.get_granularity(task["module_id"], task["direction"])
            states[name] = state
            ready = all(dep in data["results"] for dep in task["depends_on"])
            fingerprint = None
            if ready:
                provenance = {"workflow_version": WORKFLOW_VERSION, "task": task, "state": state,
                              "context": data["context"],
                              "dependencies": {dep: data["results"][dep]["receipt"] for dep in task["depends_on"]}}
                fingerprint = hashlib.sha256(_json(provenance).encode("utf-8")).hexdigest()
            fingerprints[name] = fingerprint
            for collection in ("results", "requests"):
                stored = data[collection].get(name)
                if stored and stored["fingerprint"] != fingerprint:
                    del data[collection][name]
                    self.project._event(session_id, "workflow.invalidated", {
                        "workflow_id": workflow_id, "task_id": name, "kind": collection,
                        "previous": stored})
        return states, fingerprints

    def _report(self, workflow_id, data, fingerprints):
        return {"workflow_id": workflow_id, "order": [t["id"] for t in data["tasks"]],
                "complete": len(data["results"]) == len(data["tasks"]),
                "statuses": {t["id"]: ("completed" if t["id"] in data["results"] else
                                        "dispatched" if t["id"] in data["requests"] else
                                        "ready" if fingerprints[t["id"]] else "blocked") for t in data["tasks"]},
                "outputs": {name: result["output"] for name, result in data["results"].items()}}

    def workflow_status(self, workflow_id: str) -> dict:
        with self.project._db:
            self.project._db.execute("BEGIN IMMEDIATE")
            session_id, data = self._load(workflow_id)
            _, fingerprints = self._refresh(workflow_id, session_id, data)
            self._save(workflow_id, data)
            return self._report(workflow_id, data, fingerprints)

    def next_task(self, workflow_id: str) -> dict:
        with self.project._db:
            self.project._db.execute("BEGIN IMMEDIATE")
            session_id, data = self._load(workflow_id)
            states, fingerprints = self._refresh(workflow_id, session_id, data)
            request = None
            for task in data["tasks"]:
                name = task["id"]
                if name in data["results"] or fingerprints[name] is None:
                    continue
                request = data["requests"].get(name)
                if request is None:
                    state = states[name]
                    module = self.project._problem(task["module_id"])
                    request = {"request_id": uuid4().hex, "fingerprint": fingerprints[name],
                               "task": task, "description": module["description"], "granularity": state,
                               "constraints": compile_constraints(state["parameters"]),
                               "inputs": {dep: data["results"][dep]["output"] for dep in task["depends_on"]},
                               "context": data["context"]}
                    data["requests"][name] = request
                    self.project._event(session_id, "workflow.task_dispatched", {
                        "workflow_id": workflow_id, "request": request})
                break
            self._save(workflow_id, data)
            return {**self._report(workflow_id, data, fingerprints), "request": request}

    def submit_task(self, workflow_id: str, request_id: str, output: dict) -> dict:
        _text(request_id, "request_id")
        output = json.loads(_json(output))
        error = None
        with self.project._db:
            self.project._db.execute("BEGIN IMMEDIATE")
            session_id, data = self._load(workflow_id)
            _, fingerprints = self._refresh(workflow_id, session_id, data)
            match = next(((name, req) for name, req in data["requests"].items()
                          if req["request_id"] == request_id), None)
            if match is None:
                error = "Unknown, already consumed or stale request; call next_task again"
            else:
                name, request = match
                try:
                    validate_output(output, request["constraints"])
                except GranuleError as exc:
                    error = str(exc)
                if error is None:
                    data["results"][name] = {"output": output, "fingerprint": fingerprints[name],
                                             "receipt": request_id}
                    del data["requests"][name]
            self.project._event(session_id, "workflow.result_submitted", {
                "workflow_id": workflow_id, "request_id": request_id,
                "output": output, "accepted": error is None, "error": error})
            _, fingerprints = self._refresh(workflow_id, session_id, data)
            self._save(workflow_id, data)
            report = self._report(workflow_id, data, fingerprints)
        if error:
            raise GranuleError(error)
        return report
