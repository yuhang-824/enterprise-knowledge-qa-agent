"""Transparent lexical retrieval and constrained extraction from actual text."""
import re
from .domain import TOPICS, digest, resolve_question

PATTERNS = {
    "reimbursement_deadline": (r"报销(?:提交)?(?:期限|时限)(?:为|是|：|:)\s*(\d+)\s*天", "天"),
    "reimbursement_materials": (r"报销所需材料(?:为|是|：|:)\s*([^。；;\n]+)", ""),
    "annual_leave": (r"年度带薪年假(?:为|是|：|:)\s*(\d+)\s*天", "天"),
    "export_resolution": (r"(?:最高|最大)?导出分辨率(?:为|是|：|:)\s*(1080p|720p|4K|8K)", ""),
}


def extract(doc, chunk):
    claims = []
    # This deliberately supports affirmative synthetic policy templates only.
    if re.search(r"错误示例|已废止|并非|并不|不再适用", chunk["text"]):
        return claims
    for key, (pattern, unit) in PATTERNS.items():
        for match in re.finditer(pattern, chunk["text"], flags=re.I):
            claims.append({"key": key, "value": match.group(1).strip(), "unit": unit,
                           "supporting_quote": match.group(0),
                           "supersedes": re.findall(r"本条款明确替代([A-Z][A-Z0-9_-]*)", chunk["text"])})
    return claims


class Index:
    def __init__(self, corpus, request):
        self.corpus, self.request = corpus, request
        self.intent = resolve_question(request)
        self.docs = {d["id"]: d for d in corpus["documents"] if d.get("active", True)}

    def applicable(self):
        key = self.intent["claim_key"]
        docs = [d for d in self.docs.values() if key and key in d["topics"]]
        if self.request.scope is not None:
            docs = [d for d in docs if d.get("scope") in (None, self.request.scope)]
        if self.request.applicable_doc_id is not None:
            docs = [d for d in docs if d["id"] == self.request.applicable_doc_id]
        return docs

    def required(self):
        return [(d["id"], c["id"]) for d in self.applicable() for c in d["chunks"]]

    def search(self, query, strategy, limit):
        if strategy == "expanded" and self.intent["claim_key"]:
            query = TOPICS[self.intent["claim_key"]]["query"] + " " + query
        canonical = [t for t in ("报销", "期限", "材料", "年假", "导出", "分辨率") if t in query]
        scored = []
        for doc in self.docs.values():
            text = doc["title"] + " " + " ".join(c["text"] for c in doc["chunks"])
            matched = sum(term in text for term in canonical)
            if canonical and matched == len(canonical):
                scored.append((matched, doc))
        scored.sort(key=lambda item: (-item[0], item[1]["id"]))
        hits = [{"doc_id": d["id"], "title": d["title"], "version_date": d["version_date"],
                 "scope": d.get("scope"), "score": score,
                 "snippets": [{"chunk_id": c["id"], "preview": c["text"][:80]} for c in d["chunks"]]}
                for score, d in scored[:limit]]
        return {"hits": hits, "total": len(scored), "has_more": len(scored) > limit,
                "strategy": strategy, "effective_query": query,
                "suggested_query": self.intent["suggested_query"]}

    def read(self, doc_id, chunk_id):
        doc = self.docs.get(doc_id)
        chunk = next((c for c in (doc or {}).get("chunks", []) if c["id"] == chunk_id), None)
        if not doc or not chunk:
            return None
        return {"source_id": doc_id + ":" + chunk_id + ":" + digest(chunk["text"])[:12],
                "doc_id": doc_id, "chunk_id": chunk_id, "title": doc["title"],
                "version_date": doc["version_date"], "scope": doc.get("scope"),
                "content_hash": digest(chunk["text"]), "quote": chunk["text"],
                "claims": extract(doc, chunk)}
