"""Build a personal skill package with optional registered ChatGPT app binding."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / ".agentgranule" / "chatgpt"
TEMPLATE = ROOT / "integrations" / "chatgpt"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-id", help="Actual asdk_app_... ID or plugin_asdk_app_... ID for the default bound package")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--skills-only", action="store_true",
                       help="Package skills without an app reference; select the existing MCP connection separately")
    modes.add_argument("--skill-archive", action="store_true",
                       help="Package root SKILL.md and references for the independent ChatGPT Skills upload")
    parser.add_argument("--output-name", default="agentgranule-personal",
                        help="Output basename, optionally suffixed with hyphen-separated lowercase letters or digits")
    args = parser.parse_args()
    app_id = None
    if args.skills_only or args.skill_archive:
        if args.app_id is not None:
            mode = "--skill-archive" if args.skill_archive else "--skills-only"
            parser.error(f"{mode} does not accept --app-id; it does not bind an app.")
    else:
        if args.app_id is None:
            parser.error("The default bound package requires --app-id from your ChatGPT plugin URL.")
        app_id = args.app_id.removeprefix("plugin_")
        if not re.fullmatch(r"asdk_app_[A-Za-z0-9]+", app_id) or app_id in {"asdk_app_example", "asdk_app_placeholder"}:
            parser.error("Use the actual registered app ID from your ChatGPT plugin URL.")
    if not re.fullmatch(r"agentgranule-personal(?:-[a-z0-9]+)*", args.output_name):
        parser.error("--output-name must be agentgranule-personal, optionally followed by hyphen-separated lowercase letters or digits.")
    output = LOCAL / args.output_name
    archive = LOCAL / f"{args.output_name}.zip"
    for target in (output, archive):
        if target.exists():
            parser.error(f"Package target exists and was not overwritten: {target}")
    LOCAL.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="plugin-build-", dir=LOCAL) as staging:
        staged = Path(staging) / "agentgranule-personal"
        if args.skill_archive:
            skill_source = TEMPLATE / "skills" / "agentgranule-workflow"
            staged.mkdir()
            shutil.copyfile(skill_source / "SKILL.md", staged / "SKILL.md")
            shutil.copytree(skill_source / "references", staged / "references")
        else:
            shutil.copytree(TEMPLATE, staged)
        if args.skills_only:
            manifest_path = staged / ".codex-plugin" / "plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest.pop("apps", None)
            manifest["name"] = "agentgranule-workflow"
            manifest["version"] = "0.1.3"
            manifest["interface"]["displayName"] = "AgentGranule Workflow"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (staged / ".app.json").unlink(missing_ok=True)
        elif not args.skill_archive:
            (staged / ".app.json").write_text(json.dumps({"apps": {"agentgranule": {
                "id": app_id, "required": True}}}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        staged_archive = Path(staging) / archive.name
        with zipfile.ZipFile(staged_archive, "x", compression=zipfile.ZIP_DEFLATED) as package:
            for file in sorted(staged.rglob("*")):
                if file.is_file():
                    package.write(file, file.relative_to(staged).as_posix())
        staged.rename(output)
        staged_archive.rename(archive)
    print(json.dumps({"directory": str(output), "archive": str(archive),
                      "app_id": app_id, "skills_only": args.skills_only,
                      "skill_archive": args.skill_archive, "installed": False,
                      "chatgpt_connection_verified": False}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
