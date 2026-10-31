from dataclasses import dataclass, field
from datetime import date
import hashlib
import json
import re


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


TOPICS = {
    "reimbursement_deadline": {"label": "报销提交期限", "query": "报销 期限", "domain": "报销"},
    "reimbursement_materials": {"label": "报销所需材料", "query": "报销 材料", "domain": "报销"},
    "annual_leave": {"label": "年度带薪年假", "query": "年假 天数", "domain": "年假"},
    "export_resolution": {"label": "产品导出分辨率", "query": "导出 分辨率", "domain": "导出"},
}


@dataclass(frozen=True)
class Request:
    request_id: str
    question: str
    history: tuple = field(default_factory=tuple)
    applicable_doc_id: str | None = None
    scope: str | None = None

    @classmethod
    def parse(cls, raw):
        if not isinstance(raw, dict) or set(raw) - set(cls.__dataclass_fields__):
            raise ValueError("input contains unknown fields")
        for key, limit in (("request_id", 128), ("question", 2000)):
            if not isinstance(raw.get(key), str) or not raw[key].strip() or len(raw[key]) > limit:
                raise ValueError(f"invalid {key}")
        history = raw.get("history", [])
        if not isinstance(history, list) or len(history) > 8:
            raise ValueError("history must have at most 8 messages")
        for message in history:
            if not isinstance(message, dict) or set(message) != {"role", "content"} or message["role"] not in {"user", "assistant"}:
                raise ValueError("history only accepts user/assistant messages")
            if not isinstance(message["content"], str) or not message["content"].strip() or len(message["content"]) > 2000:
                raise ValueError("invalid history content")
        selected, scope = raw.get("applicable_doc_id"), raw.get("scope")
        if selected is not None and (not isinstance(selected, str) or not re.fullmatch(r"[A-Z][A-Z0-9_-]{0,31}", selected)):
            raise ValueError("invalid applicable document ID")
        if scope is not None and (not isinstance(scope, str) or not scope.strip() or len(scope) > 50):
            raise ValueError("invalid scope")
        return cls(raw["request_id"], raw["question"], tuple(history), selected, scope)

    def as_dict(self):
        return {"request_id": self.request_id, "question": self.question, "history": list(self.history),
                "applicable_doc_id": self.applicable_doc_id, "scope": self.scope}


def domains(text):
    found = []
    if re.search(r"报销|报账|报帐|差旅|发票", text): found.append("报销")
    if re.search(r"年假|年度休假|带薪休假", text): found.append("年假")
    if re.search(r"导出|分辨率|画质|视频输出", text, re.I): found.append("导出")
    return found


def resolve_question(request):
    text = request.question
    current = domains(text)
    inferred = False
    if not current:
        # Conversation is only a topic hint. Assistant claims never become evidence.
        for message in reversed(request.history):
            if message["role"] == "user" and domains(message["content"]):
                if re.search(r"那|这些|这个|它|还|材料|多久|几天|什么时候", text):
                    current, inferred = domains(message["content"]), True
                break
    keys = []
    if "报销" in current:
        if re.search(r"材料|凭证|发票|准备|什么资料", text): keys.append("reimbursement_materials")
        if re.search(r"期限|时限|时效|最迟|最晚|多久|几天|多长|什么时候|赶在", text) and not re.search(r"审批|审核|到账|打款|付款|进度|处理|退款", text):
            keys.append("reimbursement_deadline")
    if "年假" in current and re.search(r"几天|多少天|天数|多久|多长", text) and not re.search(r"材料|申请|工资|薪资|补贴|流程|审批|生效|折现", text):
        keys.append("annual_leave")
    if "导出" in current and re.search(r"分辨率|画质|清晰度|1080[pP]|720[pP]|4[kK]|8[kK]", text):
        keys.append("export_resolution")
    generic = not current and bool(re.fullmatch(r"(?:那|这个|请问)?(?:需要|要)?(?:多久|几天|多长时间|哪些材料|准备哪些材料|准备什么|什么政策)[呢吗？?。!！ ]*", text))
    ambiguous = len(keys) > 1 or generic or bool(current and not keys)
    key = keys[0] if len(keys) == 1 and not ambiguous else None
    return {"claim_key": key, "topic_candidates": keys, "needs_topic_clarification": ambiguous,
            "used_user_history": inferred,
            "resolved_question": ("关于" + "、".join(current) + "：" + text) if inferred else text,
            "suggested_query": TOPICS[key]["query"] if key else text}


def validate_corpus(data):
    if not isinstance(data, dict) or not isinstance(data.get("version"), str) or not isinstance(data.get("documents"), list):
        raise ValueError("invalid corpus")
    seen = set()
    for doc in data["documents"]:
        if not re.fullmatch(r"[A-Z][A-Z0-9_-]{0,31}", doc["id"]) or doc["id"] in seen:
            raise ValueError("invalid/duplicate document ID")
        seen.add(doc["id"])
        date.fromisoformat(doc["version_date"])
        if not isinstance(doc["title"], str) or not doc["title"].strip():
            raise ValueError("document title required")
        if not isinstance(doc.get("topics"), list) or any(key not in TOPICS for key in doc["topics"]):
            raise ValueError("invalid topics")
        chunks = doc["chunks"]
        if not isinstance(chunks, list) or not chunks or len(chunks) > 20:
            raise ValueError("document chunks required")
        chunk_ids = set()
        for chunk in chunks:
            if not isinstance(chunk["id"], str) or not chunk["id"].strip() or chunk["id"] in chunk_ids:
                raise ValueError("invalid/duplicate chunk ID")
            if not isinstance(chunk["text"], str) or not chunk["text"].strip() or len(chunk["text"]) > 5000:
                raise ValueError("invalid chunk text")
            chunk_ids.add(chunk["id"])
        if type(doc.get("active", True)) is not bool:
            raise ValueError("active must be boolean")
        if doc.get("scope") is not None and not isinstance(doc["scope"], str):
            raise ValueError("scope must be string or null")
    for rule in data.get("failures", {}).values():
        if type(rule.get("times")) is not int or rule["times"] < 0:
            raise ValueError("failure times must be a nonnegative integer")
    return data
