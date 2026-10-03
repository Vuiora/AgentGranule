"""Versioned module proposals and atomic, explicitly confirmed effort allocation.

The host supplies material-based analysis and actual human confirmations. This
module validates their structure; it does not infer modules or human identity.
"""

from __future__ import annotations

import json
from uuid import uuid4

from .algorithms import Task, _json, topological_order
from .core import GranuleError, Project, _design_effort_units, _parameters, _text
from .workflow import Workflow


def task_id(logical_id: str, direction: str) -> str:
    """Unambiguous, durable task identifiers for module/direction pairs."""
    return json.dumps([logical_id, direction], ensure_ascii=False)


def _revision(value):
    if type(value) is not int or value < 1:
        raise GranuleError("expected_revision must be a positive integer")


def _analysis(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) - {"summary", "modules", "context"}:
        raise GranuleError("analysis requires summary, modules and optional context only")
    _text(value.get("summary"), "summary")
    modules = value.get("modules")
    if not isinstance(modules, list) or not modules:
        raise GranuleError("analysis requires a non-empty modules list")
    required = {"id", "name", "description", "expected_output", "basis", "directions"}
    cleaned, ids = [], set()
    for module in modules:
        if not isinstance(module, dict) or not required <= set(module) or set(module) - required - {"parent_id", "depends_on"}:
            raise GranuleError("Modules require id, name, description, expected_output, basis, directions and optional parent_id/depends_on only")
        for key in required - {"directions"}:
            _text(module[key], key)
        if module["id"] in ids:
            raise GranuleError("Module ids must be unique")
        ids.add(module["id"])
        directions = module["directions"]
        if not isinstance(directions, list) or not directions:
            raise GranuleError("directions must be a non-empty list")
        for direction in directions:
            _text(direction, "direction")
        if len(set(directions)) != len(directions):
            raise GranuleError("directions must be unique within each module")
        parent = module.get("parent_id")
        if parent is not None:
            _text(parent, "parent_id")
        dependencies = module.get("depends_on", [])
        if not isinstance(dependencies, list):
            raise GranuleError("depends_on must be a list")
        for dependency in dependencies:
            _text(dependency, "dependency")
        if len(set(dependencies)) != len(dependencies):
            raise GranuleError("Duplicate dependencies are not allowed")
        cleaned.append({**module, "parent_id": parent, "depends_on": list(dependencies)})
    # Nesting and scheduling express different relationships and are independently validated.
    for field in ("parent_id", "depends_on"):
        graph = [Task(module["id"], module["id"], "analysis",
                      ([module["parent_id"]] if module["parent_id"] else [])
                      if field == "parent_id" else module["depends_on"])
                 for module in cleaned]
        try:
            topological_order(graph)
        except GranuleError as exc:
            raise GranuleError(f"Invalid {field} graph: {exc}") from exc
    context = value.get("context", {})
    if not isinstance(context, dict):
        raise GranuleError("analysis context must be an object")
    return json.loads(_json({"summary": value["summary"], "modules": cleaned, "context": context}))


class Design:
    """Persist proposals, approved immutable graphs and complete effort decisions."""

    def __init__(self, project: Project):
        self.project = project
        # Schema creation happens before any domain transaction; helpers never commit.
        self.workflow = Workflow(project)
        project._db.execute("""CREATE TABLE IF NOT EXISTS design_analyses (
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
            revision INTEGER NOT NULL, state TEXT NOT NULL,
            analysis TEXT NOT NULL, module_ids TEXT NOT NULL,
            workflow_id TEXT REFERENCES workflows(id),
            previous_analysis_id TEXT REFERENCES design_analyses(id)
        )""")
        project._db.commit()

    def _load(self, analysis_id: str) -> dict:
        _text(analysis_id, "analysis_id")
        row = self.project._db.execute("SELECT * FROM design_analyses WHERE id=?", (analysis_id,)).fetchone()
        if row is None:
            raise GranuleError(f"Unknown analysis: {analysis_id}")
        return {"analysis_id": row["id"], "session_id": row["session_id"], "revision": row["revision"],
                "state": row["state"], "analysis": json.loads(row["analysis"]),
                "module_ids": json.loads(row["module_ids"]), "workflow_id": row["workflow_id"],
                "previous_analysis_id": row["previous_analysis_id"]}

    def get_analysis(self, analysis_id: str) -> dict:
        return self._load(analysis_id)

    def propose_analysis(self, session_id: str, analysis: dict, previous_analysis_id: str | None = None) -> dict:
        analysis = _analysis(analysis)
        with self.project._db:
            self.project._db.execute("BEGIN IMMEDIATE")
            self.project._session(session_id)
            if previous_analysis_id is not None:
                previous = self._load(previous_analysis_id)
                if previous["session_id"] != session_id:
                    raise GranuleError("Previous analysis must belong to the same session")
                if previous["state"] != "approved":
                    raise GranuleError("Revise a proposed analysis with update_analysis; previous analysis must be approved")
            analysis_id = uuid4().hex
            self.project._db.execute("INSERT INTO design_analyses VALUES (?, ?, 1, 'proposed', ?, '{}', NULL, ?)",
                                     (analysis_id, session_id, _json(analysis), previous_analysis_id))
            result = self._load(analysis_id)
            self.project._event(session_id, "design.analysis_proposed", result)
            return result

    def update_analysis(self, analysis_id: str, expected_revision: int, analysis: dict) -> dict:
        _revision(expected_revision)
        analysis = _analysis(analysis)
        with self.project._db:
            self.project._db.execute("BEGIN IMMEDIATE")
            previous = self._load(analysis_id)
            self._proposed(previous, expected_revision)
            self.project._db.execute("UPDATE design_analyses SET revision=revision+1, analysis=? WHERE id=?",
                                     (_json(analysis), analysis_id))
            result = self._load(analysis_id)
            self.project._event(result["session_id"], "design.analysis_updated", {"previous": previous, "current": result})
            return result

    @staticmethod
    def _proposed(data: dict, expected_revision: int):
        if data["revision"] != expected_revision:
            raise GranuleError("Analysis revision changed; reload before confirmation")
        if data["state"] != "proposed":
            raise GranuleError("Approved analysis is immutable; propose a new analysis linked to the previous one")

    def approve_analysis(self, analysis_id: str, expected_revision: int, actor: str) -> dict:
        _revision(expected_revision)
        _text(actor, "actor")
        with self.project._db:
            self.project._db.execute("BEGIN IMMEDIATE")
            data = self._load(analysis_id)
            self._proposed(data, expected_revision)
            modules = data["analysis"]["modules"]
            by_id = {module["id"]: module for module in modules}
            ordered = topological_order(Task(module["id"], module["id"], "analysis",
                                            [module["parent_id"]] if module["parent_id"] else []) for module in modules)
            module_ids = {module["id"]: uuid4().hex for module in modules}
            for task in ordered:
                module = by_id[task.id]
                actual_id = module_ids[task.id]
                parent_id = module_ids[module["parent_id"]] if module["parent_id"] else None
                description = f"{module['name']}：{module['description']}\n预期输出：{module['expected_output']}"
                self.project._db.execute("INSERT INTO problems VALUES (?, ?, ?, ?)",
                                         (actual_id, data["session_id"], parent_id, description))
                self.project._event(data["session_id"], "problem.created", {
                    "problem_id": actual_id, "parent_id": parent_id, "description": description,
                    "analysis_id": analysis_id, "logical_id": task.id})
            self.project._db.execute("UPDATE design_analyses SET state='approved', revision=revision+1, module_ids=? WHERE id=?",
                                     (_json(module_ids), analysis_id))
            result = self._load(analysis_id)
            self.project._event(data["session_id"], "design.analysis_approved", {**result, "actor": actor})
            self.project._event(data["session_id"], "conversation.message", {"role": "user", "content": _json({
                "confirmation": "approve_analysis", "analysis_id": analysis_id,
                "revision": result["revision"], "actor": actor})})
            return result

    def _snapshot(self, data: dict) -> dict:
        if data["state"] != "approved":
            raise GranuleError("Approve the module analysis before allocating design effort")
        controls = []
        pairs = [(data["module_ids"][module["id"]], direction)
                 for module in sorted(data["analysis"]["modules"], key=lambda module: module["id"])
                 for direction in sorted(module["directions"])]
        for problem, control in self.project._granularity_batch(pairs):
            if problem["session_id"] != data["session_id"]:
                raise GranuleError("Approved modules must belong to the analysis session")
            controls.append({**control, "module_id": control["problem_id"]})
        return {"analysis_id": data["analysis_id"], "revision": data["revision"],
                "workflow_id": data["workflow_id"], "controls": controls}

    def allocation_snapshot(self, analysis_id: str) -> dict:
        with self.project._db:
            self.project._db.execute("BEGIN IMMEDIATE")
            return self._snapshot(self._load(analysis_id))

    @staticmethod
    def _choices(choices: list[dict]) -> dict:
        if not isinstance(choices, list) or not choices:
            raise GranuleError("choices must contain every module/direction exactly once")
        values = {}
        for choice in choices:
            if not isinstance(choice, dict) or set(choice) != {"module_id", "direction", "design_effort"}:
                raise GranuleError("Each choice requires module_id, direction and design_effort only")
            _text(choice["module_id"], "module_id")
            _text(choice["direction"], "direction")
            pair = choice["module_id"], choice["direction"]
            if pair in values:
                raise GranuleError("Duplicate allocation choices are not allowed")
            values[pair] = _design_effort_units(choice["design_effort"]) / 100
        return values

    @staticmethod
    def _tasks(data: dict) -> list[dict]:
        modules = data["analysis"]["modules"]
        by_id = {module["id"]: module for module in modules}
        return [{"id": task_id(module["id"], direction), "module_id": data["module_ids"][module["id"]],
                 "direction": direction,
                 "depends_on": [task_id(dependency, dep_direction) for dependency in module["depends_on"]
                                for dep_direction in by_id[dependency]["directions"]]}
                for module in modules for direction in module["directions"]]

    def save_allocation(self, snapshot: dict, choices: list[dict], actor: str) -> dict:
        """Compare a complete snapshot and save all actual choices in one transaction."""
        _text(actor, "actor")
        values = self._choices(choices)
        if not isinstance(snapshot, dict):
            raise GranuleError("snapshot must be the complete allocation_snapshot object")
        frozen_snapshot = json.loads(_json(snapshot))
        with self.project._db:
            self.project._db.execute("BEGIN IMMEDIATE")
            data = self._load(frozen_snapshot.get("analysis_id"))
            current = self._snapshot(data)
            # Full JSON comparison also rejects bool/int substitutions and extra/missing fields.
            if _json(frozen_snapshot) != _json(current):
                raise GranuleError("Allocation snapshot changed; reload and confirm the full allocation again")
            expected_pairs = {(control["module_id"], control["direction"]) for control in current["controls"]}
            if set(values) != expected_pairs:
                raise GranuleError("choices must contain every approved module/direction exactly once, without foreign modules")
            changed_pairs = set()
            for control in current["controls"]:
                pair = control["module_id"], control["direction"]
                # Explicit approval pins inherited values too: later default changes
                # must not silently replace the effort the person just confirmed.
                if control["source"] != "module" or control["parameters"].get("design_effort") != values[pair]:
                    parameters = _parameters({**control["parameters"], "design_effort": values[pair]})
                    self.project._set_granularity_locked(*pair, parameters, actor)
                    changed_pairs.add(pair)
            tasks = self._tasks(data)
            first_allocation = data["workflow_id"] is None
            if first_allocation:
                workflow_id = self.workflow._create_workflow_locked(data["session_id"], tasks, data["analysis"]["context"])["workflow_id"]
                self.project._db.execute("UPDATE design_analyses SET workflow_id=? WHERE id=?", (workflow_id, data["analysis_id"]))
            else:
                workflow_id = data["workflow_id"]
            session_id, workflow_data = self.workflow._load(workflow_id)
            if session_id != data["session_id"]:
                raise GranuleError("Design workflow must belong to the analysis session")
            previously_stored = set(workflow_data["results"]) | set(workflow_data["requests"])
            _, fingerprints = self.workflow._refresh(workflow_id, session_id, workflow_data)
            self.workflow._save(workflow_id, workflow_data)
            affected = ({task["id"] for task in tasks} if first_allocation else
                        {task["id"] for task in tasks if (task["module_id"], task["direction"]) in changed_pairs})
            affected.update(previously_stored - set(workflow_data["results"]) - set(workflow_data["requests"]))
            for task in topological_order(Task(**task) for task in tasks):
                if any(dependency in affected for dependency in task.depends_on):
                    affected.add(task.id)
            controls = self._snapshot(self._load(data["analysis_id"]))["controls"]
            confirmation = {"analysis_id": data["analysis_id"], "revision": data["revision"],
                            "workflow_id": workflow_id, "actor": actor,
                            "previous_controls": current["controls"], "controls": controls,
                            "choices": [{"module_id": module_id, "direction": direction, "design_effort": effort}
                                        for (module_id, direction), effort in sorted(values.items())],
                            "affected_task_ids": sorted(affected)}
            self.project._event(session_id, "design.allocation_confirmed", confirmation)
            self.project._event(session_id, "conversation.message", {"role": "user", "content": _json({
                "confirmation": "save_allocation", "analysis_id": data["analysis_id"],
                "revision": data["revision"], "choices": confirmation["choices"], "actor": actor})})
            return {"status": "saved", "analysis_id": data["analysis_id"], "workflow_id": workflow_id,
                    "controls": controls, "report": self.workflow._report(workflow_id, workflow_data, fingerprints),
                    "affected_task_ids": sorted(affected)}
