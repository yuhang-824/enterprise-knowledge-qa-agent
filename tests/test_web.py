from copy import deepcopy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from knowledge_qa.scenarios import make_request
from knowledge_qa.web import Application, make_server

ROOT = Path(__file__).resolve().parents[1]


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.base = json.loads((ROOT / "data/corpus.json").read_text(encoding="utf-8"))
        self.path = self.root / "corpus.json"
        self.path.write_text(json.dumps(self.base, ensure_ascii=False), encoding="utf-8")
        self.app = Application(self.path, self.root / "state")
        self.server = make_server(self.app, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def http(self, method, path, data=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            conn.request(method, path, body=data, headers=headers or {})
            r = conn.getresponse()
            return r.status, dict(r.getheaders()), r.read()
        finally:
            conn.close()

    def run_case(self, name="document_conflict", **request_changes):
        raw = {"request": {**make_request(name), **request_changes}, "case": name, "max_steps": 24}
        status, _, body = self.http("POST", "/api/run", json.dumps(raw).encode(), {"Content-Type": "application/json"})
        self.assertEqual(status, 200, body)
        return json.loads(body)

    def test_page_assets_and_config(self):
        for path, phrase in (("/", "企业知识问答"), ("/assets/app.js", "renderCitations"), ("/assets/app.css", ".workspace")):
            status, headers, body = self.http("GET", path)
            self.assertEqual(status, 200)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertIn(phrase, body.decode())
        status, _, body = self.http("GET", "/api/config")
        self.assertEqual(json.loads(body)["default_case"], "document_conflict")

    def test_conflict_then_user_confirmation(self):
        first = self.run_case()["result"]
        second = self.run_case(applicable_doc_id="D01")["result"]
        self.assertEqual(first["status"], "needs_clarification")
        self.assertEqual({c["value"] for c in first["citations"]}, {"15", "30"})
        self.assertIn("30天", second["answer"])
        self.assertEqual({c["doc_id"] for c in second["citations"]}, {"D01"})

    def test_changed_source_is_read_as_45_days(self):
        r = self.run_case("changed_source")["result"]
        self.assertIn("45天", r["answer"])
        self.assertIn("45天", r["citations"][0]["quote"])

    def test_paraphrase_and_followup(self):
        r = self.run_case("paraphrase")["result"]
        self.assertEqual(len(r["searches"]), 2)
        r = self.run_case("context_followup")["result"]
        self.assertIn("电子发票和审批记录", r["answer"])
        self.assertTrue(r["intent"]["used_user_history"])

    def test_tool_failure_not_falsely_answered(self):
        r = self.run_case("tool_failure")["result"]
        self.assertEqual(r["status"], "failed")
        self.assertNotIn("为30天", r["answer"])

    def test_history_and_source_snapshot(self):
        saved = self.run_case()
        status, _, body = self.http("GET", "/api/history")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["runs"][0]["run_id"], saved["run_id"])
        status, _, body = self.http("GET", "/api/results/" + saved["run_id"])
        self.assertEqual(json.loads(body), saved)

    def test_corpus_endpoint_matches_selected_case(self):
        status, _, body = self.http("GET", "/api/corpus?case=clear_answer")
        self.assertEqual(status, 200)
        docs = {d["id"]: d for d in json.loads(body)["documents"]}
        self.assertFalse(docs["D02"]["active"])

    def test_updated_file_is_loaded_on_next_request(self):
        first = self.run_case("clear_answer")["result"]
        self.base["documents"][0]["chunks"][0]["text"] = "报销提交期限为37天。"
        self.path.write_text(json.dumps(self.base, ensure_ascii=False), encoding="utf-8")
        second = self.run_case("clear_answer")["result"]
        self.assertIn("37天", second["answer"])
        self.assertNotEqual(first["corpus_digest"], second["corpus_digest"])

    def test_local_only_and_file_boundaries(self):
        status, _, _ = self.http("GET", "/api/config", headers={"Host": "attacker.example"})
        self.assertEqual(status, 421)
        status, _, _ = self.http("POST", "/api/run", b"{}", {"Content-Type": "application/json", "Origin": "https://example.com"})
        self.assertEqual(status, 403)
        for path in ("/.env", "/../.env", "/api/results/../../.env", "/unknown"):
            self.assertEqual(self.http("GET", path)[0], 404)

    def test_invalid_input_and_case(self):
        status, _, _ = self.http("POST", "/api/run", b"{}", {"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        status, _, _ = self.http("POST", "/api/run", b"{}", {"Content-Type": "text/plain"})
        self.assertEqual(status, 415)
        self.assertEqual(self.http("GET", "/api/corpus?case=invalid")[0], 400)


if __name__ == "__main__": unittest.main()
