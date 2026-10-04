"""Real stdio script entrypoint for tunnel-client's Python preflight."""

import sys


def main() -> None:
    # The tunnel preflight expects Python's first argument to be a script path.
    # Configure Unicode here instead of passing interpreter flags before it.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    from agentgranule.mcp_server import main as server_main

    server_main()


if __name__ == "__main__":
    main()
