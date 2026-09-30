"""Loopback web controls for the existing human granularity interface."""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import parse_qs, urlsplit

from .core import GranuleError, Project, _text
from .workflow import Workflow


def create_server(database: str, port: int = 8765) -> ThreadingHTTPServer:
    _text(str(database), "database")
    if str(database) == ":memory:":
        raise GranuleError("Slider requires a persistent file database")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, code, data, content_type="application/json; charset=utf-8"):
            body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def local_request(self):
            # Only the loopback UI's own origin may read or mutate project data.
            expected = f"127.0.0.1:{self.server.server_port}"
            return (self.headers.get("Host") == expected and
                    self.headers.get("Origin", f"http://{expected}") == f"http://{expected}" and
                    self.headers.get("Sec-Fetch-Site", "same-origin") not in {"cross-site", "same-site"})

        def do_GET(self):
            if not self.local_request():
                self.reply(403, {"error": "Use the server's loopback URL"})
                return
            path = urlsplit(self.path)
            if path.path == "/":
                self.reply(200, files("agentgranule").joinpath("static/granularity.html").read_bytes(),
                           "text/html; charset=utf-8")
                return
            try:
                with Project(database) as project:
                    if path.path == "/api/modules":
                        modules = [dict(row) for row in project._db.execute(
                            "SELECT p.id, p.description, p.session_id, s.title FROM problems p "
                            "JOIN sessions s ON s.id=p.session_id ORDER BY s.rowid, p.rowid")]
                        directions = sorted({"classification", "enumeration", "advantages", "disadvantages", "explanation"} |
                                            {row[0] for row in project._db.execute(
                                                "SELECT direction FROM module_controls UNION SELECT direction FROM granularity_defaults")})
                        self.reply(200, {"modules": modules, "directions": directions})
                    elif path.path == "/api/granularity":
                        query = parse_qs(path.query)
                        self.reply(200, project.get_granularity(query.get("module_id", [""])[0],
                                                                query.get("direction", [""])[0]))
                    else:
                        self.reply(404, {"error": "Unknown endpoint"})
            except GranuleError as exc:
                self.reply(400, {"error": str(exc)})

        def do_POST(self):
            if not self.local_request():
                self.reply(403, {"error": "Cross-origin writes are not allowed"})
                return
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                self.reply(415, {"error": "Content-Type must be application/json"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 65536:
                    raise GranuleError("JSON request size must be 1..65536 bytes")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise GranuleError("Request must be a JSON object")
                with Project(database) as project:
                    if self.path == "/api/module":
                        if set(data) != {"title", "description"}:
                            raise GranuleError("Require title and description")
                        _text(data["title"], "title")
                        _text(data["description"], "description")
                        session = project.create_session(data["title"])
                        module = project.add_module(session, data["description"])
                        project.record_message(session, "user", json.dumps(data, ensure_ascii=False))
                        self.reply(201, {"module_id": module, "session_id": session})
                    elif self.path == "/api/granularity":
                        if set(data) != {"module_id", "direction", "parameters", "source", "revision"}:
                            raise GranuleError("Require module_id, direction, parameters, source, revision")
                        # Reject a stale UI before replacing the full parameters object.
                        # The check and core update share one write transaction.
                        from .core import _parameters
                        parameters = _parameters(data["parameters"])
                        if type(data["revision"]) is not int:
                            raise GranuleError("revision must be an integer")
                        with project._db:
                            project._db.execute("BEGIN IMMEDIATE")
                            current = project.get_granularity(data["module_id"], data["direction"])
                            if (current["source"], current["revision"]) != (data["source"], data["revision"]):
                                self.reply(409, {"error": "设置已更新，请重新加载后调整"})
                                return
                            # Reuse the same validated update as the public core interface.
                            control = project._set_granularity_locked(data["module_id"], data["direction"], parameters, "human:slider")
                            session = project._problem(data["module_id"])["session_id"]
                            project._event(session, "conversation.message", {"role": "user", "content":
                                "滑块保存：" + json.dumps(data, ensure_ascii=False)})
                        workflows = Workflow(project)
                        reports = [workflows.workflow_status(row[0]) for row in project._db.execute(
                            "SELECT id FROM workflows WHERE session_id=?", (session,)).fetchall()]
                        self.reply(200, {"control": control, "workflows": reports})
                    else:
                        self.reply(404, {"error": "Unknown endpoint"})
            except (GranuleError, ValueError, TypeError, UnicodeDecodeError) as exc:
                self.reply(400, {"error": str(exc)})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description="AgentGranule granularity sliders")
    parser.add_argument("--database", default=".agentgranule/project.sqlite3")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    with create_server(args.database, args.port) as server:
        print(f"粒度滑块：http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
