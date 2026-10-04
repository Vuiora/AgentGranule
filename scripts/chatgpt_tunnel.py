"""Prepare a private ChatGPT tunnel without storing credentials in the project."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / ".agentgranule" / "chatgpt"
PROFILE = "agentgranule-personal"


def command_string(arguments: list[str]) -> str:
    # tunnel-client parses this itself, without a shell. Forward slashes avoid
    # its backslash escape rules on Windows; double quotes preserve spaces.
    return " ".join('"' + argument.replace('"', '\\"') + '"' for argument in arguments)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "configure", "doctor", "run"))
    parser.add_argument("--tunnel-id", help="Actual ID from Platform tunnel settings")
    parser.add_argument("--client", type=Path, help="Verified official tunnel-client executable")
    parser.add_argument("--python", type=Path, help="configure only; defaults to this Python interpreter")
    parser.add_argument("--database", type=Path, help="configure only; defaults to the independent personal database")
    args = parser.parse_args()
    if args.action != "configure" and (args.python is not None or args.database is not None):
        parser.error("--python and --database are configure-only; doctor and run use the existing profile.")
    python = (args.python or Path(sys.executable)).resolve()
    database = (args.database or ROOT / ".agentgranule" / "chatgpt-personal.sqlite3").resolve()
    entrypoint = ROOT / "scripts" / "chatgpt_mcp.py"
    client = args.client
    if client is None:
        receipt = LOCAL / "client.json"
        if not receipt.is_file():
            parser.error("Pass --client after downloading and verifying the official Windows client. See docs/chatgpt-personal.md.")
        client = Path(json.loads(receipt.read_text(encoding="utf-8"))["executable"])
    client = client.resolve()
    for label, path in (("Python", python), ("tunnel-client", client), ("MCP entrypoint", entrypoint)):
        if not path.is_file():
            parser.error(f"{label} executable is missing: {path}")
    if not database.is_relative_to(ROOT):
        parser.error("Use a database inside this project; outside-project access needs a separately reviewed configuration.")
    mcp_command = command_string([
        python.as_posix(), entrypoint.as_posix(),
        "--database", database.as_posix(),
        "--source-root", ROOT.as_posix(),
    ])
    profiles = LOCAL / "profiles"
    profile_file = profiles / f"{PROFILE}.yaml"
    if args.action == "check":
        subprocess.run([str(python), "-X", "utf8", "-c", "import agentgranule.mcp_server; import mcp"],
                       cwd=ROOT, check=True, capture_output=True)
        version = subprocess.run([str(client), "--version"], check=True, capture_output=True,
                                 text=True, encoding="utf-8").stdout.strip()
        print(json.dumps({"local_imports": "ok", "client_version": version,
                          "default_mcp_command": mcp_command, "default_database": str(database),
                          "source_root": str(ROOT),
                          "profile_exists": profile_file.is_file(),
                          "runtime_configuration": "doctor and run use the existing profile, not these configure defaults",
                          "chatgpt_connection_verified": False}, ensure_ascii=False, indent=2))
        return 0
    if args.action == "configure":
        if not args.tunnel_id or not re.fullmatch(r"tunnel_[A-Za-z0-9]+", args.tunnel_id):
            parser.error("configure requires --tunnel-id with the actual tunnel_... ID from your account.")
        if args.tunnel_id == "tunnel_0123456789abcdef0123456789abcdef":
            parser.error("The documentation example is not an actual account tunnel ID.")
        if profile_file.exists():
            parser.error(f"Profile already exists; it was not overwritten: {profile_file}")
        profiles.mkdir(parents=True, exist_ok=True)
        subprocess.run([str(client), "init", "--profile-dir", str(profiles), "--profile", PROFILE,
                        "--sample", "sample_mcp_stdio_local", "--tunnel-id", args.tunnel_id,
                        "--mcp-command", mcp_command, "--health-listen-addr", "127.0.0.1:0",
                        "--control-plane-api-key-ref", "env:CONTROL_PLANE_API_KEY"], check=True)
        return 0
    if not profile_file.is_file():
        parser.error("No configured profile. Run configure with a real tunnel ID first.")
    if not os.environ.get("CONTROL_PLANE_API_KEY"):
        parser.error("Set CONTROL_PLANE_API_KEY in your local environment; do not paste it into chat or write it to the repository.")
    command = [str(client), args.action, "--profile-file", str(profile_file)]
    if args.action == "doctor":
        command.append("--explain")
    else:
        command.extend(["--health.url-file", str(LOCAL / "health.url"),
                        "--pid.file", str(LOCAL / "tunnel.pid")])
    return subprocess.call(command, cwd=ROOT)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"Local command failed with exit code {exc.returncode}; connection is not verified.", file=sys.stderr)
        raise SystemExit(2)
    except KeyboardInterrupt:
        raise SystemExit(130)
