"""Analyze a real Python project and open its native review/allocation window.

No graph or effort is approved by this script; those decisions belong to the
caller in the window. Restart with --analysis-id to resume the same proposal.
"""

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

from agentgranule import Design, Project, analyze_framework
from agentgranule.design_view import main as design_main


def main(argv=None):
    parser = argparse.ArgumentParser(description="Review and allocate effort for a Python framework")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--database", default=".agentgranule/project.sqlite3")
    parser.add_argument("--analysis-id", help="Resume an existing proposal without creating another session")
    parser.add_argument("--output-file")
    parser.add_argument("--renderer", choices=("opengl", "tk"), default="opengl")
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


def _run(args):
    database = str(Path(args.database).resolve())
    output = Path(args.output_file) if args.output_file else Path(database).parent / f"design-choice-{uuid4().hex}.json"
    if output.exists():
        print(json.dumps({"error": "Result path already exists; use a unique new output file"}), file=sys.stderr)
        return 2
    if args.output_file and not output.parent.is_dir():
        print(json.dumps({"error": "Result directory does not exist; create it before opening the window"}), file=sys.stderr)
        return 2
    with Project(database) as project:
        design = Design(project)
        if args.analysis_id:
            analysis_id = design.get_analysis(args.analysis_id)["analysis_id"]
        else:
            result = analyze_framework(str(Path(args.root).resolve()))
            session = project.create_session("框架模块分析与设计力度")
            project.record_message(session, "tool", "执行摘要：静态分析实际 Python 框架生成待审核模块清单；尚无人工批准。")
            proposal = design.propose_analysis(session, result["analysis"])
            analysis_id = proposal["analysis_id"]
        print(json.dumps({"database": database, "analysis_id": analysis_id}, ensure_ascii=False), flush=True)
    return design_main(["--database", database, "--analysis-id", analysis_id,
                        "--output-file", str(output), "--renderer", args.renderer])


if __name__ == "__main__":
    raise SystemExit(main())
