import argparse
import json
from pathlib import Path
import sys
from .agent import run_agent
from .domain import Request, validate_corpus
from .model import DeepSeekModel, MockModel, load_env


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"): stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="企业知识问答 Agent：原文引用、冲突查证、补充检索")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--data", type=Path, default=Path("data/corpus.json"))
    parser.add_argument("--model", choices=["mock", "deepseek"], default="mock")
    parser.add_argument("--max-steps", type=int, default=24)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        load_env()
        request = Request.parse(json.loads(args.input.read_text(encoding="utf-8-sig")))
        corpus = validate_corpus(json.loads(args.data.read_text(encoding="utf-8-sig")))
        model = MockModel() if args.model == "mock" else DeepSeekModel()
        result = run_agent(request, corpus, model, args.max_steps)
        text = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n", encoding="utf-8")
        print(text)
        return 0 if result["status"] in {"answered", "needs_clarification", "insufficient_evidence"} else 2
    except (ValueError, OSError, KeyError, TypeError, OverflowError):
        print(json.dumps({"status": "error", "error": "configuration_input_or_corpus_error",
                          "hint": "Check input schema, corpus path and model environment; no Mock fallback."}), file=sys.stderr)
        return 1


if __name__ == "__main__": raise SystemExit(main())
