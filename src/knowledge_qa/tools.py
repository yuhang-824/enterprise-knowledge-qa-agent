"""Tools enforce provenance, full relevant-source coverage and conflict boundaries."""
from copy import deepcopy
from .domain import TOPICS
from .retrieval import Index


def schema(name, description, extra=None):
    props = {"reason": {"type": "string", "description": "一句基于可见结果的动作依据，不输出内部思维过程。"}}
    props.update(extra or {})
    return {"type": "function", "function": {"name": name, "description": description,
        "parameters": {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}}}


SCHEMAS = [
    schema("inspect_question", "读取问题和可选用户历史，识别需要查证的主题；对话不是知识证据。"),
    schema("search_documents", "检索制度与FAQ，返回文档ID、日期与片段索引；无结果时可改写或扩展检索。",
           {"query": {"type": "string"}, "strategy": {"type": "string", "enum": ["keyword", "expanded"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10}}),
    schema("read_snippet", "读取已检索文档的实际原文及可验证事实。摘要不能直接作为回答依据。",
           {"doc_id": {"type": "string"}, "chunk_id": {"type": "string"}}),
    schema("check_evidence", "核对相关来源是否读全、是否支持问题、是否冲突或缺适用范围；不按日期擅定优先级。"),
    schema("answer_question", "用经核验的事实和原文引用形成回答。只能使用已读取、支持该事实的source_id。",
           {"claim_key": {"type": "string", "enum": list(TOPICS)},
            "source_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 10}}),
    schema("request_clarification", "主题不明或资料冲突时，列明证据及资料缺口，向用户追问并结束本轮。",
           {"kind": {"type": "string", "enum": ["topic", "applicable_document", "scope"]}}),
    schema("stop", "资料不足或工具失败时明确停止，保留已读取证据；不得声称有可靠答案。",
           {"outcome": {"type": "string", "enum": ["insufficient_evidence", "tool_failure"]}}),
]


class Tools:
    def __init__(self, request, corpus):
        self.request, self.corpus = request, deepcopy(corpus)
        self.index = Index(self.corpus, request)
        self.discovered, self.reads, self.searches, self.errors = {}, {}, [], []
        self.fail_counts = {}
        self.assessment = None
        self.terminal = None

    def execute(self, name, args):
        spec = next((s["function"]["parameters"] for s in SCHEMAS if s["function"]["name"] == name), None)
        if spec is None:
            return {"ok": False, "error": "unknown_tool"}
        if not isinstance(args, dict) or set(args) != set(spec["required"]):
            return {"ok": False, "error": "invalid_arguments"}
        for key, value in args.items():
            field = spec["properties"][key]
            if field["type"] == "string" and (not isinstance(value, str) or not value.strip() or len(value) > 2000):
                return {"ok": False, "error": "invalid_arguments"}
            if "enum" in field and value not in field["enum"]:
                return {"ok": False, "error": "invalid_arguments"}
            if field["type"] == "integer" and (type(value) is not int or not field["minimum"] <= value <= field["maximum"]):
                return {"ok": False, "error": "invalid_arguments"}
            if field["type"] == "array" and (not isinstance(value, list) or not 1 <= len(value) <= 10
                or any(not isinstance(v, str) for v in value) or len(set(value)) != len(value)):
                return {"ok": False, "error": "invalid_arguments"}
        if self.terminal:
            return {"ok": False, "error": "task_already_ended"}
        return getattr(self, name)(**{k: v for k, v in args.items() if k != "reason"})

    def _fail(self, name, doc_id=None):
        rule = self.corpus.get("failures", {}).get(name, {"times": 0})
        key = name + ":" + str(doc_id)
        count = self.fail_counts.get(key, 0)
        if count < rule["times"] and (doc_id is None or doc_id in rule.get("doc_ids", [doc_id])):
            self.fail_counts[key] = count + 1
            self.errors.append({"tool": name, "doc_id": doc_id, "error": "injected_tool_failure"})
            return True
        return False

    def inspect_question(self):
        return {"ok": True, "request": self.request.as_dict(), "intent": self.index.intent,
                "supported_topics": TOPICS, "conversation_is_evidence": False}

    def search_documents(self, query, strategy, limit):
        if self._fail("search_documents"):
            return {"ok": False, "error": "search_failed"}
        found = self.index.search(query, strategy, limit)
        for hit in found["hits"]:
            self.discovered[hit["doc_id"]] = deepcopy(hit)
        self.searches.append({"query": query, **deepcopy(found)})
        self.assessment = None
        return {"ok": True, **found}

    def read_snippet(self, doc_id, chunk_id):
        if doc_id not in self.discovered:
            return {"ok": False, "error": "search_document_first"}
        if self._fail("read_snippet", doc_id):
            return {"ok": False, "error": "read_failed", "doc_id": doc_id}
        read = self.index.read(doc_id, chunk_id)
        if read is None:
            return {"ok": False, "error": "snippet_not_found"}
        self.reads[(doc_id, chunk_id)] = read
        self.assessment = None
        return {"ok": True, "source": deepcopy(read), "cached": False}

    def _citations(self, facts):
        citations = []
        seen = set()
        for source, claim in facts:
            identity = (source["source_id"], claim["key"], claim["value"])
            if identity in seen:
                continue
            seen.add(identity)
            citations.append({key: source[key] for key in ("source_id", "doc_id", "chunk_id", "title", "version_date", "scope", "content_hash", "quote")}
                             | {"claim_key": claim["key"], "value": claim["value"], "unit": claim["unit"],
                                "supporting_quote": claim["supporting_quote"]})
        return citations

    def check_evidence(self):
        intent, key = self.index.intent, self.index.intent["claim_key"]
        result = {"status": "insufficient", "claim_key": key, "missing_snippets": [],
                  "citations": [], "conflicts": [], "gaps": [], "supporting_source_ids": []}
        if intent["needs_topic_clarification"]:
            result.update(status="needs_topic", gaps=["问题主题不明确，需要确认具体制度或问题。"])
        elif self.request.applicable_doc_id and not self.index.applicable():
            result.update(status="invalid_selection", gaps=["指定文档不存在、未生效或不支持当前问题，请确认适用制度。"])
        elif key:
            applicable = self.index.applicable()
            missing = [{"doc_id": d, "chunk_id": c} for d, c in self.index.required() if (d, c) not in self.reads]
            if not applicable:
                result["gaps"] = ["当前范围内没有支持该问题的制度资料。"]
            elif missing:
                result.update(status="need_more", missing_snippets=missing,
                              gaps=["相关资料尚未读取完整，不能断言没有冲突。"])
            else:
                all_facts = [(self.reads[(d["id"], c["id"])], fact) for d in applicable for c in d["chunks"]
                             for fact in self.reads[(d["id"], c["id"] )]["claims"] if fact["key"] == key]
                scoped = {d.get("scope") for d in applicable if d.get("scope") is not None}
                superseded = {doc_id for source, fact in all_facts for doc_id in fact["supersedes"] if doc_id != source["doc_id"]}
                surviving = [(source, fact) for source, fact in all_facts if source["doc_id"] not in superseded]
                unsupported = [d["id"] for d in applicable if not any(source["doc_id"] == d["id"] for source, _ in all_facts)]
                if unsupported:
                    result.update(citations=self._citations(all_facts),
                                  gaps=["相关文档" + "、".join(unsupported) + "中没有可核验的明确事实，不能忽略这些资料后直接回答。"])
                elif all_facts and not surviving:
                    result.update(status="conflict", citations=self._citations(all_facts),
                                  conflicts=[{"code": "cyclic_precedence", "documents": sorted({s["doc_id"] for s, _ in all_facts})}],
                                  gaps=["文档互相声明替代，优先关系循环，需确认适用制度。"])
                elif len(scoped) > 1 and self.request.scope is None and self.request.applicable_doc_id is None:
                    result.update(status="needs_scope", citations=self._citations(surviving), gaps=["存在多个适用范围，请先确认员工所属范围。"])
                elif len({(f["value"], f["unit"]) for _, f in surviving}) > 1:
                    result.update(status="conflict", citations=self._citations(surviving),
                        conflicts=[{"claim_key": key, "values": sorted({f["value"] + f["unit"] for _, f in surviving}),
                                    "documents": sorted({s["doc_id"] for s, _ in surviving})}],
                        gaps=["相关文档给出不同结论，且没有足够的适用范围或明确替代关系；不能按日期擅选答案。"])
                elif surviving:
                    citations = self._citations(surviving)
                    result.update(status="supported", citations=citations,
                                  value=surviving[0][1]["value"], unit=surviving[0][1]["unit"],
                                  supporting_source_ids=sorted({c["source_id"] for c in citations}))
                else:
                    result["gaps"] = ["已经读取相关原文，但未找到受支持的明确事实；不根据标题或历史回答猜测。"]
        else:
            result["gaps"] = ["现有知识库没有支持该问题的可核实资料。"]
        self.assessment = result
        return {"ok": True, "assessment": deepcopy(result), "suggested_query": intent["suggested_query"]}

    def answer_question(self, claim_key, source_ids):
        if self.assessment is None:
            return {"ok": False, "error": "check_evidence_first"}
        if self.assessment["status"] != "supported":
            return {"ok": False, "error": "evidence_not_resolved", "assessment": deepcopy(self.assessment)}
        if claim_key != self.index.intent["claim_key"]:
            return {"ok": False, "error": "wrong_question_topic"}
        valid = set(self.assessment["supporting_source_ids"])
        if not source_ids or not set(source_ids) <= valid:
            return {"ok": False, "error": "citation_does_not_support_claim"}
        citations = [c for c in self.assessment["citations"] if c["source_id"] in source_ids]
        value, unit = self.assessment["value"], self.assessment["unit"]
        prefix = f"按您指定的{self.request.applicable_doc_id}，" if self.request.applicable_doc_id else "依据已核验的资料，"
        answer = prefix + TOPICS[claim_key]["label"] + "为" + value + unit + "。"
        self.terminal = {"status": "answered", "answer": answer, "citations": citations, "gaps": [],
                         "conflicts": [], "clarification": None}
        return {"ok": True, **deepcopy(self.terminal)}

    def request_clarification(self, kind):
        if kind == "topic" and self.index.intent["needs_topic_clarification"]:
            question = "请说明具体问题，例如报销期限、报销材料、年假天数或产品导出分辨率。"
            citations, conflicts, gaps = [], [], ["缺少明确的问题主题或必要上下文。"]
        else:
            if self.assessment is None:
                return {"ok": False, "error": "check_evidence_first"}
            expected = {"applicable_document": {"conflict", "invalid_selection"}, "scope": {"needs_scope"}}
            if self.assessment["status"] not in expected.get(kind, set()):
                return {"ok": False, "error": "clarification_requires_evidence"}
            citations, conflicts, gaps = self.assessment["citations"], self.assessment["conflicts"], self.assessment["gaps"]
            documents = "、".join(sorted({c["doc_id"] for c in citations}))
            question = ("请确认应适用哪份制度" + ("（" + documents + "）" if documents else "") + "，或补充适用范围及明确优先关系。" if kind == "applicable_document"
                        else "请确认员工所属范围，再按对应制度查证。")
        answer = "资料存在冲突，暂不能给出唯一答案。" if conflicts else "需要补充信息后才能给出可靠答案。"
        self.terminal = {"status": "needs_clarification", "answer": answer, "citations": deepcopy(citations),
                         "conflicts": deepcopy(conflicts), "gaps": list(gaps), "clarification": question}
        return {"ok": True, **deepcopy(self.terminal)}

    def stop(self, outcome):
        if outcome == "tool_failure":
            if not self.errors:
                return {"ok": False, "error": "tool_failure_requires_evidence"}
            status, answer, gaps = "failed", "检索或读取工具失败，尚不能完成可靠核实。", ["需要恢复失败工具并重新核实资料。"]
        else:
            strategies = {s["strategy"] for s in self.searches}
            if not self.assessment or self.assessment["status"] != "insufficient" or not {"keyword", "expanded"} <= strategies:
                return {"ok": False, "error": "supplement_search_and_check_first"}
            status, answer, gaps = "insufficient_evidence", "现有资料不足以可靠回答该问题。", self.assessment["gaps"]
        citations = self._citations([(source, fact) for source in self.reads.values() for fact in source["claims"]
                                    if fact["key"] == self.index.intent["claim_key"]])
        self.terminal = {"status": status, "answer": answer, "citations": citations, "conflicts": [],
                         "gaps": gaps, "clarification": None}
        return {"ok": True, **deepcopy(self.terminal)}

    def report(self):
        return {"request": self.request.as_dict(), "intent": self.index.intent,
                "searches": self.searches, "sources_read": list(self.reads.values()),
                "assessment": self.assessment, "tool_errors": self.errors,
                **(self.terminal or {"status": "incomplete", "answer": "任务尚未完成，不能确认可靠答案。",
                                    "citations": [], "conflicts": [], "gaps": ["执行在形成可验证结论前停止。"], "clarification": None})}
