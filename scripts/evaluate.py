"""Actual runs -> expectations and citation checks -> auditable evidence."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from knowledge_qa.agent import run_agent
from knowledge_qa.domain import Request, digest
from knowledge_qa.model import DeepSeekModel, MockModel, load_env
from knowledge_qa.scenarios import CASES, make_request, scenario_corpus


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def verify(result, corpus, expected):
    values = {**result, "citation_count": len(result["citations"]),
              "citation_values": sorted({c["value"] for c in result["citations"]}),
              "citation_docs": sorted({c["doc_id"] for c in result["citations"]}),
              "supplemental_search": len(result["searches"]) >= 2 and any(s["strategy"] == "expanded" for s in result["searches"]),
              "has_tool_errors": bool(result["tool_errors"])}
    errors = []
    for key, expected_value in expected.items():
        passed = expected_value in result["answer"] if key == "answer_contains" else values[key] == expected_value
        if not passed: errors.append(f"{key}: expected {expected_value!r}")
    docs = {d["id"]: d for d in corpus["documents"]}
    for citation in result["citations"]:
        doc = docs.get(citation["doc_id"])
        chunk = next((c for c in (doc or {}).get("chunks", []) if c["id"] == citation["chunk_id"]), None)
        if not chunk or citation["quote"] != chunk["text"] or citation["content_hash"] != digest(chunk["text"]):
            errors.append("citation text or content hash does not match actual source")
        if citation["supporting_quote"] not in citation["quote"]:
            errors.append("supporting quote absent from original")
    if result["status"] == "answered" and (not result["citations"] or result["conflicts"]):
        errors.append("answered without resolved, cited evidence")
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["mock", "deepseek"], default="mock")
    parser.add_argument("--core", action="store_true")
    parser.add_argument("--case", action="append")
    args = parser.parse_args()
    load_env(ROOT / ".env")
    names = list(CASES)[:6] if args.core else list(CASES)
    if args.case:
        if set(args.case) - set(CASES): parser.error("unknown case")
        names = list(dict.fromkeys(args.case))
    output = ROOT / "examples" / ("mock" if args.model == "mock" else "real")
    output.mkdir(parents=True, exist_ok=True)
    base = json.loads((ROOT / "data/corpus.json").read_text(encoding="utf-8"))
    summary = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "python": platform.python_version(), "execution_mode": args.model, "cases": []}
    start = time.monotonic()
    for name in names:
        raw, data = make_request(name), scenario_corpus(base, name)
        write(output / f"{name}.input.json", raw)
        write(output / f"{name}.data.json", data)
        try:
            model = MockModel() if args.model == "mock" else DeepSeekModel()
        except ValueError:
            print("Configure local .env; real mode never silently becomes Mock.", file=sys.stderr)
            return 1
        result = run_agent(Request.parse(raw), data, model, CASES[name].get("max_steps", 24))
        errors = verify(result, data, CASES[name]["expected"])
        write(output / f"{name}.trace.json", {"execution_mode": result["execution_mode"], "model": result["model"],
              "evidence_boundary": result["evidence_boundary"], "trace": result.pop("trace")})
        write(output / f"{name}.output.json", result)
        summary["cases"].append({"name": name, "label": CASES[name]["label"], "passed": not errors,
            "errors": errors, "expected": CASES[name]["expected"], "actual_status": result["status"],
            "answer": result["answer"], "steps_taken": result["steps_taken"],
            "input": f"{name}.input.json", "data": f"{name}.data.json",
            "output": f"{name}.output.json", "trace": f"{name}.trace.json"})
        print(f"{'PASS' if not errors else 'FAIL'} {name}: {result['status']} ({result['steps_taken']} steps)", flush=True)
    summary.update(passed=sum(c["passed"] for c in summary["cases"]), total=len(names), wall_seconds=round(time.monotonic() - start, 3))
    write(output / "summary.json", summary)
    print(f"{summary['passed']}/{summary['total']} passed; evidence in {output}")
    return 0 if summary["passed"] == summary["total"] else 2


if __name__ == "__main__": raise SystemExit(main())
