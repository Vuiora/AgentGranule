"""Local stdio MCP facade. Each call uses the same durable project database."""

import argparse
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .core import GranuleError, Project, _text
from .workflow import Workflow
from .design import Design
from .framework import analyze_framework as scan_framework


def create_server(database: str, *, source_root: str | None = None) -> FastMCP:
    _text(database, "database")
    if database == ":memory:":
        raise ValueError("MCP requires a file database for persistence across calls")
    allowed_root = None
    if source_root is not None:
        _text(source_root, "source_root")
        allowed_root = Path(source_root).resolve()
        if not allowed_root.is_dir():
            raise GranuleError("source_root must be an existing directory")
    server = FastMCP("AgentGranule", instructions=(
        "Host-driven task processing with human granularity controls. Forward all visible messages "
        "through record_message. Default parameters are proposals, not human answers. "
        "Use create_workflow, next_task, submit_task and workflow_status for complete workflows."))

    def core(operation, **arguments):
        with Project(database) as project:
            result = getattr(project, operation)(**arguments)
            return result if isinstance(result, dict) else {"result": result}

    def workflow(operation, **arguments):
        with Project(database) as project:
            return getattr(Workflow(project), operation)(**arguments)

    def design(operation, **arguments):
        with Project(database) as project:
            return getattr(Design(project), operation)(**arguments)

    # Project initialization can create or migrate the database even for reads.
    database_annotations = ToolAnnotations(readOnlyHint=False, openWorldHint=False)

    @server.tool(annotations=database_annotations)
    def create_session(title: str) -> dict[str, Any]:
        """Create an audit session; result is its session_id."""
        return core("create_session", title=title)

    @server.tool(annotations=database_annotations)
    def add_module(session_id: str, description: str, parent_id: str | None = None) -> dict[str, Any]:
        """Create a independently controlled module; result is its module_id."""
        return core("add_module", session_id=session_id, description=description, parent_id=parent_id)

    @server.tool(annotations=database_annotations)
    def record_message(session_id: str, role: str, content: str) -> dict[str, Any]:
        """Record actual visible user/assistant/tool/system content, excluding credentials."""
        return core("record_message", session_id=session_id, role=role, content=content)

    @server.tool(annotations=database_annotations)
    def request_granularity(problem_id: str, direction: str = "classification") -> dict[str, Any]:
        """Return a human question and current suggested parameters; does not record an answer."""
        return core("request_granularity", problem_id=problem_id, direction=direction)

    @server.tool(annotations=database_annotations)
    def get_granularity(problem_id: str, direction: str = "classification") -> dict[str, Any]:
        """Read module override, project default or built-in proposal and revision."""
        return core("get_granularity", problem_id=problem_id, direction=direction)

    @server.tool(annotations=database_annotations)
    def set_granularity(problem_id: str, direction: str, parameters: dict, actor: str) -> dict[str, Any]:
        """Replace a module direction's parameters after an actual human choice."""
        return core("set_granularity", problem_id=problem_id, direction=direction, parameters=parameters, actor=actor)

    @server.tool(annotations=database_annotations)
    def set_default_granularity(session_id: str, direction: str, parameters: dict, actor: str) -> dict[str, Any]:
        """Replace project-wide defaults, recording the actual actor and change."""
        return core("set_default_granularity", session_id=session_id, direction=direction, parameters=parameters, actor=actor)

    @server.tool(annotations=database_annotations)
    def create_workflow(session_id: str, tasks: list[dict], context: dict | None = None) -> dict[str, Any]:
        """Validate and persist a task DAG: id, module_id, direction, depends_on."""
        return workflow("create_workflow", session_id=session_id, tasks=tasks, context=context)

    @server.tool(annotations=database_annotations)
    def next_task(workflow_id: str) -> dict[str, Any]:
        """Get a stable request for the next ready task, or null when complete."""
        return workflow("next_task", workflow_id=workflow_id)

    @server.tool(annotations=database_annotations)
    def submit_task(workflow_id: str, request_id: str, output: dict) -> dict[str, Any]:
        """Accept a fresh result after count/depth validation; reject stale/duplicate requests."""
        return workflow("submit_task", workflow_id=workflow_id, request_id=request_id, output=output)

    @server.tool(annotations=database_annotations)
    def workflow_status(workflow_id: str) -> dict[str, Any]:
        """Return current valid outputs; changes invalidate affected tasks and descendants."""
        return workflow("workflow_status", workflow_id=workflow_id)

    @server.tool(annotations=database_annotations)
    def history(session_id: str) -> dict[str, Any]:
        """Read ordered conversation, granularity, dispatch and acceptance events."""
        return core("history", session_id=session_id)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                             idempotentHint=True, openWorldHint=False))
    def analyze_framework(root_path: str) -> dict[str, Any]:
        """Statically inspect local Python modules into an unapproved, reviewable proposal; never runs source."""
        return scan_framework(root_path, allowed_root=allowed_root)

    @server.tool(annotations=database_annotations)
    def propose_analysis(session_id: str, analysis: dict, previous_analysis_id: str | None = None) -> dict[str, Any]:
        """Validate and record a proposed module graph, without human approval or task execution."""
        return design("propose_analysis", session_id=session_id, analysis=analysis, previous_analysis_id=previous_analysis_id)

    @server.tool(annotations=database_annotations)
    def update_analysis(analysis_id: str, expected_revision: int, analysis: dict) -> dict[str, Any]:
        """Revise an unapproved graph if its revision is current; approved graphs remain immutable."""
        return design("update_analysis", analysis_id=analysis_id, expected_revision=expected_revision, analysis=analysis)

    @server.tool(annotations=database_annotations)
    def get_analysis(analysis_id: str) -> dict[str, Any]:
        """Read the durable module graph, approval revision and workflow association."""
        return design("get_analysis", analysis_id=analysis_id)

    @server.tool(annotations=database_annotations)
    def approve_analysis(analysis_id: str, expected_revision: int, actor: str) -> dict[str, Any]:
        """Record the actual caller's explicit graph confirmation; does not allocate effort automatically."""
        return design("approve_analysis", analysis_id=analysis_id, expected_revision=expected_revision, actor=actor)

    @server.tool(annotations=database_annotations)
    def allocation_snapshot(analysis_id: str) -> dict[str, Any]:
        """Read every approved module/direction's current parameters and revision for atomic allocation."""
        return design("allocation_snapshot", analysis_id=analysis_id)

    @server.tool(annotations=database_annotations)
    def save_allocation(snapshot: dict, choices: list[dict], actor: str) -> dict[str, Any]:
        """Save the actual human's complete hundredth-precision allocation atomically; reject any stale item."""
        return design("save_allocation", snapshot=snapshot, choices=choices, actor=actor)

    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description="AgentGranule MCP stdio server")
    parser.add_argument("--database", default=".agentgranule/project.sqlite3")
    parser.add_argument("--source-root", help="Optional boundary for local source analysis")
    args = parser.parse_args(argv)
    create_server(args.database, source_root=args.source_root).run(transport="stdio")


if __name__ == "__main__":
    main()
