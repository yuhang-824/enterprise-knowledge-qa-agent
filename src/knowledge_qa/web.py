"""Local verification UI using the same retrieval and citation guards as CLI."""
import argparse
from datetime import datetime, timezone
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import uuid
from urllib.parse import urlparse, parse_qs
from .agent import run_agent
from .domain import Request, validate_corpus
from .model import DeepSeekModel, MockModel, load_env
from .scenarios import CASES, make_request, scenario_corpus

ASSETS = Path(__file__).with_name("web_assets")


class Application:
    def __init__(self, corpus_path, state_dir, model="mock"):
        self.corpus_path = Path(corpus_path)
        self.load_base()
        self.state_dir = Path(state_dir)
        self.results = self.state_dir / "results"
        self.results.mkdir(parents=True, exist_ok=True)
        if model not in {"mock", "deepseek"}: raise ValueError("invalid model")
        self.model = model
        if model == "deepseek": DeepSeekModel()

    def load_base(self):
        return validate_corpus(json.loads(self.corpus_path.read_text(encoding="utf-8-sig")))

    def config(self):
        return {"model": self.model, "default_case": "document_conflict", "max_steps": 24,
                "cases": [{"id": name, "label": case["label"], "request": make_request(name),
                           "max_steps": case.get("max_steps", 24)} for name, case in CASES.items()],
                "corpus_version": self.load_base()["version"]}

    def corpus(self, name):
        data = scenario_corpus(self.load_base(), name)
        return {"version": data["version"], "documents": data["documents"]}

    def run(self, raw):
        if not isinstance(raw, dict) or set(raw) != {"request", "case", "max_steps"}:
            raise ValueError("request/case/max_steps required")
        if not isinstance(raw["case"], str) or raw["case"] not in CASES:
            raise ValueError("unknown case")
        if type(raw["max_steps"]) is not int or not 1 <= raw["max_steps"] <= 200:
            raise ValueError("max_steps must be 1..200")
        request = Request.parse(raw["request"])
        corpus = scenario_corpus(self.load_base(), raw["case"])
        model = MockModel() if self.model == "mock" else DeepSeekModel()
        result = run_agent(request, corpus, model, raw["max_steps"])
        run_id = uuid.uuid4().hex
        envelope = {"run_id": run_id, "case": raw["case"], "result": result}
        (self.results / (run_id + ".json")).write_text(json.dumps(envelope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return envelope

    def history(self):
        rows = []
        for path in sorted(self.results.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                r = saved["result"]
                rows.append({"run_id": saved["run_id"], "question": r["request"]["question"],
                             "status": r["status"], "citation_count": len(r["citations"]),
                             "steps_taken": r["steps_taken"], "generated_at": r["generated_at"], "case": saved["case"]})
            except (ValueError, KeyError, OSError):
                continue
        return rows

class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, app, **kwargs):
        self.app = app
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        # No bodies, credentials, themes or exception details in server logs.
        pass

    def allowed_host(self):
        return self.headers.get("Host") in {
            f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

    def send(self, status, body, content_type="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.allowed_host():
            self.send(421, {"error": "仅接受本机访问。"})
            return
        routes = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/assets/app.css": ("app.css", "text/css; charset=utf-8"),
                  "/assets/app.js": ("app.js", "text/javascript; charset=utf-8")}
        if self.path in routes:
            name, mime = routes[self.path]
            self.send(200, (ASSETS / name).read_bytes(), mime)
        elif self.path == "/api/config":
            self.send(200, self.app.config())
        elif self.path == "/api/history":
            self.send(200, {"runs": self.app.history()})
        elif urlparse(self.path).path == "/api/corpus":
            query = parse_qs(urlparse(self.path).query)
            case = query.get("case", ["document_conflict"])[0]
            if case not in CASES or set(query) - {"case"}:
                self.send(400, {"error": "不支持的资料场景。"})
            else:
                self.send(200, self.app.corpus(case))
        elif re.fullmatch(r"/api/results/[a-f0-9]{32}", self.path):
            path = self.app.results / (self.path.rsplit("/", 1)[1] + ".json")
            if path.is_file():
                self.send(200, path.read_bytes())
            else:
                self.send(404, {"error": "未找到这条运行记录。"})
        else:
            self.send(404, {"error": "页面或接口不存在。"})

    def do_POST(self):
        if not self.allowed_host():
            self.send(421, {"error": "仅接受本机访问。"})
            return
        origin = self.headers.get("Origin")
        if origin is not None and origin != "http://" + self.headers["Host"]:
            self.send(403, {"error": "不接受跨站执行请求。"})
            return
        if self.path != "/api/run":
            self.send(404, {"error": "接口不存在。"})
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
            self.send(415, {"error": "请使用 JSON 请求。"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 1 <= size <= 16384:
                self.send(413, {"error": "请求内容为空或过大。"})
                return
            payload = json.loads(self.rfile.read(size))
            envelope = self.app.run(payload)
            self.send(200, envelope)
        except (ValueError, KeyError, TypeError, OverflowError):
            self.send(400, {"error": "输入无效。请检查问题、对话历史、资料场景和步数上限。"})
        except OSError:
            self.send(500, {"error": "本地存储或服务配置异常，请检查启动目录与写入权限。"})


def make_server(app, port=8766):
    return ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, app=app))


def main(argv=None):
    parser = argparse.ArgumentParser(description="企业知识问答本机验证界面")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--data", type=Path, default=Path("data/corpus.json"))
    parser.add_argument("--state-dir", type=Path, default=Path("runtime/web"))
    parser.add_argument("--model", choices=["mock", "deepseek"], default="mock")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535: parser.error("port must be 1..65535")
    load_env()
    app = Application(args.data, args.state_dir, args.model)
    server = make_server(app, args.port)
    print(f"Knowledge QA: http://127.0.0.1:{server.server_port} ({args.model})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__": main()

