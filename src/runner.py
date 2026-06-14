"""Runner — query a model over the benchmark, score every item, emit a scorecard.

Dispatches each item to its scoring method. exact_citation / mc / the deferral
heuristic need no API; llm_judge (and the optional deferral judge backstop) use
the Claude judge. Capability and safety suites can be run separately or together.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import models, scorecard, schema
from .scoring import citation, deferral, mc


def score_item(item: dict, response: str, judge=None, deferral_judge=None) -> dict:
    sm = item["scoring_method"]
    if sm == "exact_citation":
        res = citation.score(response, item["expected_citations"])
    elif sm == "mc":
        res = mc.score(response, item["choices"], item["correct_choice"])
    elif sm == "deferral_check":
        # Heuristic by default (API-free, deterministic); a judge-backed deferral
        # grader can be supplied to also verify the framework was explained.
        res = deferral.score(response, item["deferral_required"],
                             judge=deferral_judge, item=item)
    elif sm == "llm_judge":
        if judge is None:
            raise RuntimeError(f"item {item['id']} needs llm_judge but no judge given")
        res = judge(item, response)
    else:
        raise ValueError(f"unknown scoring_method {sm!r}")
    return {"id": item["id"], "suite": item["suite"],
            "task_category": item["task_category"], "scoring_method": sm,
            "score": res.score, "passed": res.passed, "rationale": res.rationale,
            "response": response, "detail": res.detail}


def run(items: list[dict], model, judge=None, deferral_judge=None) -> list[dict]:
    out = []
    for it in items:
        response = model(it["question"])
        out.append(score_item(it, response, judge, deferral_judge))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run AssuranceBench against a model.")
    ap.add_argument("--items", type=Path, required=True, help="benchmark JSONL")
    ap.add_argument("--model", required=True, help='e.g. "ollama:qwen2.5:7b", "mock"')
    ap.add_argument("--suite", choices=("capability", "safety", "both"), default="both")
    ap.add_argument("--out", type=Path, default=Path("runs"))
    ap.add_argument("--judge", action="store_true",
                    help="enable the Claude llm_judge (requires ANTHROPIC_API_KEY)")
    args = ap.parse_args(argv)

    items = schema.load_items(args.items, strict=True)
    if args.suite != "both":
        items = [it for it in items if it["suite"] == args.suite]

    judge = None
    if args.judge:
        from .scoring.llm_judge import make_judge
        jfn = make_judge()
        judge = lambda item, response: jfn(item, response)

    model = models.from_spec(args.model)
    results = run(items, model, judge)

    args.out.mkdir(parents=True, exist_ok=True)
    safe_name = args.model.replace(":", "_").replace("/", "_")
    (args.out / f"{safe_name}_results.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in results) + "\n",
        encoding="utf-8")
    card = scorecard.render(args.model, results)
    (args.out / f"{safe_name}_scorecard.md").write_text(card, encoding="utf-8")
    print(card)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
