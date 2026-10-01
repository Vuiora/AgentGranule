"""Analyze a real Python project and open its native review/allocation window.

No graph or effort is approved by this script; those decisions belong to the
caller in the window. Restart with --analysis-id to resume the same proposal.
"""

import argparse
import json
from pathlib import Path
from uuid import uuid4

from agentgranule import Design, Project, analyze_framework
from agentgranule.design_view import show_design, DesignControl


def main():
    parser = argparse.ArgumentParser(description="Review and allocate effort for a Python framework")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--database", default=".agentgranule/project.sqlite3")
    parser.add_argument("--analysis-id", help="Resume an existing proposal without creating another session")
    parser.add_argument("--output-file")
    args = parser.parse_args()
    database = str(Path(args.database).resolve())
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
    result = show_design(DesignControl(database), analysis_id)
    output = Path(args.output_file) if args.output_file else Path(database).parent / f"design-choice-{uuid4().hex}.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"result": result, "output_file": str(output.resolve())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
