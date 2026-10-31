"""Native tool calling; MockModel is explicitly a deterministic test double."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request


class ModelError(Exception):
    pass


@dataclass
class Decision:
    name: str
    arguments: dict
    call_id: str = ""

    def message(self):
        return {"role": "assistant", "content": None, "tool_calls": [{
            "id": self.call_id, "type": "function", "function": {
                "name": self.name, "arguments": json.dumps(self.arguments, ensure_ascii=False)}}]}


def load_env(path=".env"):
    """Minimal dotenv loader. Existing process environment takes priority."""
    if not Path(path).is_file():
        return
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError("invalid .env line; use KEY=value")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key.isidentifier():
            raise ValueError("invalid .env key")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


class DeepSeekModel:
    kind = "real"

    def __init__(self):
        self.key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not self.key:
            raise ValueError("DEEPSEEK_API_KEY is required for --model deepseek; use --model mock offline")
        self.base = os.getenv("MODEL_BASE_URL", "https://api.deepseek.com").rstrip("/")
        parsed = urllib.parse.urlparse(self.base)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("MODEL_BASE_URL must be HTTPS without embedded credentials")
        self.name = os.getenv("MODEL_NAME", "deepseek-flash")
        self.timeout = float(os.getenv("MODEL_TIMEOUT_SECONDS", "45"))
        if not 1 <= self.timeout <= 120:
            raise ValueError("MODEL_TIMEOUT_SECONDS must be 1..120")
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}

    def decide(self, messages, schemas):
        body = {"model": self.name, "messages": messages, "tools": schemas,
                "tool_choice": "required",
                "thinking": {"type": "disabled"}, "temperature": 0,
                "max_tokens": 1024, "stream": False}
        req = urllib.request.Request(self.base + "/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.key})
        # Do not follow redirects carrying authentication to another host.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        try:
            with urllib.request.build_opener(NoRedirect()).open(req, timeout=self.timeout) as resp:
                raw = resp.read(1_000_001)
                if len(raw) > 1_000_000:
                    raise ModelError("model_response_too_large")
            data = json.loads(raw)
            choice = data["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ModelError("model_response_truncated")
            calls = choice["message"].get("tool_calls", [])
            if len(calls) != 1:
                raise ModelError("model_must_return_exactly_one_tool_call")
            call = calls[0]
            args = json.loads(call["function"]["arguments"])
            if not isinstance(args, dict) or not isinstance(call.get("id"), str):
                raise ModelError("invalid_model_tool_call")
            for key in self.usage:
                self.usage[key] += int(data.get("usage", {}).get(key, 0))
            self.resolved_name = data.get("model", self.name)
            return Decision(call["function"]["name"], args, call["id"])
        except urllib.error.HTTPError as exc:
            # Never emit provider response bodies: they may echo credentials or private input.
            raise ModelError(f"model_http_{exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ModelError("model_network_or_timeout_error") from None
        except (ValueError, KeyError, IndexError, TypeError):
            raise ModelError("invalid_model_response") from None


class MockModel:
    """Deterministic test policy; never real model decision evidence."""
    kind = "mock"
    name = "offline-evidence-policy-v1"

    def decide(self, messages, schemas):
        history = []
        for index, message in enumerate(messages):
            if message["role"] == "tool":
                call = messages[index - 1]["tool_calls"][0]["function"]
                history.append((call["name"], json.loads(call["arguments"]), json.loads(message["content"])))
        def choose(name, reason, **args):
            return Decision(name, {**args, "reason": reason}, "mock_" + str(len(history) + 1))
        if not history:
            return choose("inspect_question", "先明确当前问题与必要用户上下文。")
        name, args, observation = history[-1]
        context = next((o for n, _, o in history if n == "inspect_question" and o.get("ok")), None)
        if context is None:
            raise ModelError("mock_missing_context")
        intent = context["intent"]
        query = intent["suggested_query"]
        if not observation.get("ok"):
            if observation.get("error") in {"search_failed", "read_failed"}:
                return choose("stop", "工具失败，不能编造缺失的检索或原文结果。", outcome="tool_failure")
            raise ModelError("mock_unhandled_tool_error")
        if name == "inspect_question":
            if intent["needs_topic_clarification"]:
                return choose("request_clarification", "问题主题不足，先确认具体制度或问题。", kind="topic")
            return choose("search_documents", "先按当前问题检索原始资料。", query=intent["resolved_question"], strategy="keyword", limit=10)
        successful_searches = [(a, o) for n, a, o in history if n == "search_documents" and o.get("ok")]
        expanded = any(a["strategy"] == "expanded" for a, _ in successful_searches)
        read_pairs = {(o["source"]["doc_id"], o["source"]["chunk_id"]) for n, _, o in history if n == "read_snippet" and o.get("ok")}
        discovered = {h["doc_id"]: h for _, o in successful_searches for h in o["hits"]}
        if name == "search_documents" and not observation["hits"] and not expanded:
            return choose("search_documents", "首次无结果，改写为制度术语并扩展检索。", query=query, strategy="expanded", limit=10)
        if name == "check_evidence":
            assessment = observation["assessment"]
            status = assessment["status"]
            if status == "supported":
                return choose("answer_question", "已读原文支持同一结论，按核验后的引用作答。", claim_key=assessment["claim_key"], source_ids=assessment["supporting_source_ids"])
            if status in {"conflict", "invalid_selection", "needs_scope", "needs_topic"}:
                kind = "scope" if status == "needs_scope" else "topic" if status == "needs_topic" else "applicable_document"
                return choose("request_clarification", "资料或适用条件未统一，列出依据并请求确认。", kind=kind)
            if status == "need_more":
                for missing in assessment["missing_snippets"]:
                    if missing["doc_id"] not in discovered:
                        return choose("search_documents", "仍有相关来源未发现，扩大检索范围后继续查证。", query=query, strategy="expanded", limit=10)
                    if (missing["doc_id"], missing["chunk_id"]) not in read_pairs:
                        return choose("read_snippet", "相关原文未读全，继续读取以排查冲突。", **missing)
            if not expanded:
                return choose("search_documents", "当前证据不足，再进行一次扩展检索。", query=query, strategy="expanded", limit=10)
            return choose("stop", "已补充检索并核对，资料仍不足，明确拒绝猜测。", outcome="insufficient_evidence")
        # Read actual full snippets, not only previews. The engine itself does not pick this action.
        for doc in discovered.values():
            for chunk in doc["snippets"]:
                if (doc["doc_id"], chunk["chunk_id"]) not in read_pairs:
                    return choose("read_snippet", "读取检索命中的原文，建立可核验引用。", doc_id=doc["doc_id"], chunk_id=chunk["chunk_id"])
        return choose("check_evidence", "原文已读取，核对覆盖、适用范围和结论冲突。")


