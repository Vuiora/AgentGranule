"""Official SDK stdio adapter; all domain behavior stays in Project."""

import argparse
import json
import os
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .core import Project


def create_server(database: str) -> FastMCP:
    server = FastMCP("AgentGranule")

    @server.tool()
    def create_session(title: str) -> dict[str, str]:
        """Create a persistent session for visible project conversations."""
        with Project(database) as project:
            return {"session_id": project.create_session(title)}

    @server.tool()
    def add_problem(session_id: str, description: str, parent_id: str | None = None) -> dict[str, str]:
        """Create a problem, optionally under a parent in the same session."""
        with Project(database) as project:
            return {"problem_id": project.add_problem(session_id, description, parent_id)}

    @server.tool()
    def record_message(session_id: str, role: str, content: str) -> dict[str, bool]:
        """Record original visible text. Host must forward all messages and remove credentials."""
        with Project(database) as project:
            project.record_message(session_id, role, content)
            return {"recorded": True}

    @server.tool()
    def add_module(session_id: str, description: str, parent_id: str | None = None) -> dict[str, str]:
        """Create an independently controlled processing module; nesting is optional."""
        with Project(database) as project:
            return {"problem_id": project.add_module(session_id, description, parent_id)}

    @server.tool()
    def set_granularity(problem_id: str, actor: str, count: Annotated[int, Field(strict=True, gt=0)] | None = None,
                        direction: str = "classification", parameters: dict[str, object] | None = None) -> dict[str, object]:
        """Apply human-provided module detail parameters or legacy count. Host must confirm intent."""
        with Project(database) as project:
            return project.set_granularity(problem_id, count, actor, direction, parameters)

    @server.tool()
    def get_granularity(problem_id: str, direction: str = "classification") -> dict[str, object]:
        """Read effective detail and its module/default source."""
        with Project(database) as project:
            return project.get_granularity(problem_id, direction)

    @server.tool()
    def request_granularity(problem_id: str, direction: str = "classification") -> dict[str, object]:
        """Get a question to present to the USER with suggested detail. Never invent their answer."""
        with Project(database) as project:
            return project.request_granularity(problem_id, direction)

    @server.tool()
    def set_default_granularity(session_id: str, direction: str, parameters: dict[str, object], actor: str) -> dict[str, object]:
        """Set human-provided project direction defaults; record the change in the given session."""
        with Project(database) as project:
            return project.set_default_granularity(session_id, direction, parameters, actor)

    @server.tool()
    def prepare_plan(problem_id: str, direction: str = "classification") -> dict[str, object]:
        """Prepare a direction plan with effective detail parameters, source and revision."""
        with Project(database) as project:
            return project.prepare_plan(problem_id, direction)

    @server.tool()
    def submit_result(plan_id: str, categories: list[str] | None = None,
                      output: dict[str, object] | None = None) -> dict[str, object]:
        """Record output items/text; reject incorrect counts or superseded effective settings."""
        with Project(database) as project:
            return project.submit_result(plan_id, categories, output)

    @server.tool()
    def history(session_id: str) -> dict[str, object]:
        """Read ordered visible conversation and operation events."""
        with Project(database) as project:
            return {"events": project.history(session_id)}

    @server.resource("agentgranule://sessions/{session_id}/history")
    def conversation_history(session_id: str) -> str:
        """Original visible messages and state transitions for this session."""
        with Project(database) as project:
            return json.dumps(project.history(session_id), ensure_ascii=False)

    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description="AgentGranule MCP stdio server")
    parser.add_argument("--database", default=os.environ.get("AGENTGRANULE_DATABASE", ".agentgranule/project.sqlite3"))
    args = parser.parse_args(argv)
    if args.database == ":memory:":
        parser.error("MCP needs a file database because tools use separate connections")
    create_server(args.database).run(transport="stdio")


if __name__ == "__main__":
    main()
