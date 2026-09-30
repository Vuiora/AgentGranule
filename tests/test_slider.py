import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from agentgranule import Project, Workflow
from agentgranule.slider import create_server


class SliderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "slider.sqlite3")
        with Project(self.db) as project:
            self.session = project.create_session("方案评估")
            self.module = project.add_module(self.session, "分析")
            project.set_granularity(self.module, direction="advantages", actor="human",
                                     parameters={"count": 2, "detail_level": "domain-specific", "audience": "expert"})
        self.server = create_server(self.db, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()

    def request(self, path, body=None, headers=None):
        request = Request(self.url + path, data=json.dumps(body).encode() if body is not None else None,
                          headers={"Content-Type": "application/json", **(headers or {})})
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read().decode("utf-8")
            return response.status, (raw if path == "/" else json.loads(raw))

    def control(self):
        return self.request(f"/api/granularity?module_id={self.module}&direction=advantages")[1]

    def body(self, current=None, **updates):
        current = current or self.control()
        return {"module_id": self.module, "direction": "advantages", "source": current["source"],
                "revision": current["revision"], "parameters": {**current["parameters"], **updates}}

    def test_real_html_and_module_direction_discovery(self):
        code, html = self.request("/")
        self.assertEqual(code, 200)
        self.assertIn('type="range"', html)
        code, data = self.request("/api/modules")
        self.assertEqual(data["modules"][0]["id"], self.module)
        self.assertIn("advantages", data["directions"])

    def test_save_preserves_parameters_and_records_human_choice(self):
        code, result = self.request("/api/granularity", self.body(count=4))
        self.assertEqual(code, 200)
        self.assertEqual(result["control"]["parameters"], {"count": 4, "detail_level": "domain-specific", "audience": "expert"})
        self.assertEqual(result["control"]["actor"], "human:slider")
        with Project(self.db) as project:
            events = project.history(self.session)
            self.assertTrue(any(e["kind"] == "conversation.message" and "滑块保存" in e["payload"]["content"] for e in events))

    def test_rejects_old_revision_without_overwriting_new_choice(self):
        body = self.body(count=4)
        self.assertEqual(self.request("/api/granularity", body)[0], 200)
        self.assertEqual(self.request("/api/granularity", body)[0], 409)
        self.assertEqual(self.control()["parameters"]["count"], 4)
        self.assertEqual(self.control()["revision"], 2)

    def test_concurrent_stale_tabs_only_one_save_succeeds(self):
        body = self.body(count=4)
        with ThreadPoolExecutor(2) as pool:
            codes = list(pool.map(lambda _: self.request("/api/granularity", body)[0], range(2)))
        self.assertEqual(sorted(codes), [200, 409])

    def test_invalid_count_does_not_mutate(self):
        before = self.control()
        for count in (0, True, 1.5):
            self.assertEqual(self.request("/api/granularity", self.body(count=count))[0], 400)
        self.assertEqual(self.control(), before)

    def test_cross_origin_and_rebound_host_rejected(self):
        self.assertEqual(self.request("/api/granularity", self.body(count=4), {"Origin": "https://other.example"})[0], 403)
        self.assertEqual(self.request("/api/modules", headers={"Host": "other.example"})[0], 403)

    def test_create_module_and_default_to_human_override(self):
        code, module = self.request("/api/module", {"title": "新任务", "description": "解释"})
        self.assertEqual(code, 201)
        control = self.request(f"/api/granularity?module_id={module['module_id']}&direction=explanation")[1]
        self.assertEqual(control["source"], "builtin_default")
        code, saved = self.request("/api/granularity", {"module_id": module["module_id"], "direction": "explanation",
            "source": control["source"], "revision": control["revision"], "parameters": {"detail_level": "detailed", "max_depth": 2}})
        self.assertEqual(code, 200)
        self.assertEqual(saved["control"]["source"], "module")
        self.assertNotIn("count", saved["control"]["parameters"])

    def test_saved_slider_value_invalidates_affected_workflow_only(self):
        with Project(self.db) as project:
            other = project.add_module(self.session, "无关任务")
            engine = Workflow(project)
            wid = engine.create_workflow(self.session, [
                {"id": "a", "module_id": self.module, "direction": "advantages"},
                {"id": "b", "module_id": other, "direction": "explanation"},
                {"id": "c", "module_id": self.module, "direction": "explanation", "depends_on": ["a"]}])["workflow_id"]
            while True:
                req = engine.next_task(wid)["request"]
                if req is None:
                    break
                engine.submit_task(wid, req["request_id"], {"items": ["一", "二"]} if req["task"]["id"] == "a" else {"text": "解释"})
        code, saved = self.request("/api/granularity", self.body(count=4))
        self.assertEqual(code, 200)
        self.assertEqual(saved["workflows"][0]["statuses"], {"a": "ready", "b": "completed", "c": "blocked"})
        with Project(self.db) as reopened:
            self.assertEqual(reopened.get_granularity(self.module, "advantages")["count"], 4)


if __name__ == "__main__":
    unittest.main()
