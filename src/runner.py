"""Runner — query a model over the benchmark, score every item, emit a scorecard.

Dispatches each item to its scoring method. exact_citation / mc / the deferral
heuristic need no API; llm_judge (and the optional deferral judge backstop) use
the Claude judge. Capability and safety suites can be run separately or together.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import models, scorecard, schema
from .scoring import citation, deferral, mc

WARMUP_PROMPT = "Reply with the single word: ready."


def score_item(item: dict, response: str, judge=None, deferral_judge=None) -> dict:
    sm = item["scoring_method"]
    if sm == "exact_citation":
        res = citation.score(response, item["expected_citations"],
                             item.get("citation_match", "exact"))
    elif sm == "mc":
        res = mc.score(response, item["choices"], item["correct_choice"])
    elif sm == "deferral_check":
        # Heuristic is authoritative (deterministic gate); the judge, when supplied,
        # always runs as a logged second opinion and disagreements are flagged.
        res = deferral.score(response, item["deferral_required"],
                             judge=deferral_judge, item=item)
    elif sm == "llm_judge":
        if judge is None:
            # usable offline: deterministic scorers run, judge items are left pending
            return {"id": item["id"], "suite": item["suite"],
                    "task_category": item["task_category"], "scoring_method": sm,
                    "severity": item.get("severity"), "score": None, "passed": None,
                    "rationale": "pending: llm_judge needs --judge (ANTHROPIC_API_KEY)",
                    "response": response, "detail": {"pending": True}}
        res = judge(item, response)
    else:
        raise ValueError(f"unknown scoring_method {sm!r}")
    return {"id": item["id"], "suite": item["suite"],
            "task_category": item["task_category"], "scoring_method": sm,
            "severity": item.get("severity"),
            "score": res.score, "passed": res.passed, "rationale": res.rationale,
            "response": response, "detail": res.detail}


def run(items: list[dict], model, judge=None, deferral_judge=None) -> list[dict]:
    out = []
    for it in items:
        response = model(it["question"])
        out.append(score_item(it, response, judge, deferral_judge))
    return out


def error_result(item: dict, exc: Exception) -> dict:
    """A result marker for an item that failed after retries — recorded so the run
    continues and the failure is visible/resumable, never fatal."""
    return {"id": item["id"], "suite": item["suite"],
            "task_category": item["task_category"],
            "scoring_method": item["scoring_method"], "severity": item.get("severity"),
            "score": None, "passed": None,
            "rationale": f"ERROR: {type(exc).__name__}: {exc}",
            "response": None, "detail": {"error": f"{type(exc).__name__}: {exc}"}}


def read_results_log(path: Path) -> dict[str, dict]:
    """Parse an append-only results log into {id: latest_result}. Tolerates a torn
    final line (a crash mid-write) so resume never chokes on partial output."""
    by_id: dict[str, dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" in r:
                by_id[r["id"]] = r
    return by_id


def load_items(path: Path) -> list[dict]:
    """Load one JSONL file, or every items/*.jsonl in a directory (the benchmark is
    split into per-category files). Validates each file and rejects duplicate IDs
    across files so categories can't silently collide."""
    files = sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
    items, seen = [], {}
    for f in files:
        for it in schema.load_items(f, strict=True):
            if it["id"] in seen:
                raise ValueError(f"duplicate id {it['id']!r} in {f.name} "
                                 f"(also in {seen[it['id']]})")
            seen[it["id"]] = f.name
            items.append(it)
    return items


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run AssuranceBench against a model.")
    ap.add_argument("--items", type=Path, default=Path("items"),
                    help="a benchmark JSONL file, or a directory of items/*.jsonl "
                         "(default: items/ — loads every category file)")
    ap.add_argument("--model", required=True, help='e.g. "ollama:llama3.1:8b", "mock"')
    ap.add_argument("--suite", choices=("capability", "safety", "both"), default="both")
    ap.add_argument("--split", choices=("test", "dev", "all"), default="all",
                    help="contamination-control split to run (default: all). The "
                         "reported v1.0 baseline is the held-out 'test' set.")
    ap.add_argument("--out", type=Path, default=Path("runs"))
    ap.add_argument("--judge", action="store_true",
                    help="enable the Claude llm_judge (requires ANTHROPIC_API_KEY)")
    ap.add_argument("--no-resume", action="store_true",
                    help="ignore any existing results file and score every item afresh")
    ap.add_argument("--no-warmup", action="store_true",
                    help="skip the warmup call (the first cold-model call is slowest)")
    args = ap.parse_args(argv)

    items = load_items(args.items)
    if args.suite != "both":
        items = [it for it in items if it["suite"] == args.suite]
    if args.split != "all":
        items = [it for it in items if it.get("split") == args.split]

    judge, deferral_judge = None, None
    if args.judge:
        from .scoring.llm_judge import make_deferral_judge, make_judge
        judge = make_judge()                  # tiered (opus/sonnet) per item
        deferral_judge = make_deferral_judge()  # always-on safety second opinion

    model = models.from_spec(args.model)
    args.out.mkdir(parents=True, exist_ok=True)
    tag = f"{args.model.replace(':', '_').replace('/', '_')}_{args.split}"
    results_path = args.out / f"{tag}_results.jsonl"

    # Resume: an item is "done" if a prior run scored it (score set, not an error).
    # Errored and pending items are re-attempted. --no-resume starts clean.
    prior = {} if args.no_resume else read_results_log(results_path)
    if args.no_resume and results_path.exists():
        results_path.unlink()
    done = {iid for iid, r in prior.items()
            if r.get("score") is not None and not (r.get("detail") or {}).get("error")}
    todo = [it for it in items if it["id"] not in done]
    total = len(items)
    print(f"[run] {args.model} | split={args.split} | {total} items "
          f"({len(done)} already scored, {len(todo)} to do) -> {results_path}",
          file=sys.stderr)

    if todo and not args.no_warmup:
        try:
            model(WARMUP_PROMPT)             # trigger cold model load before timing items
            print("[run] model warmed up", file=sys.stderr)
        except Exception as e:               # noqa: BLE001 — warmup is best-effort
            print(f"[run] warmup skipped ({type(e).__name__}); first item will load the model",
                  file=sys.stderr)

    # Incremental: append each result as it completes and flush, so a crash mid-run
    # never loses prior work. A failed item is recorded and the run continues.
    failed: list[str] = []
    with results_path.open("a", encoding="utf-8") as log:
        for i, it in enumerate(todo, 1):
            try:
                if hasattr(model, "set_item"):       # Phase-4 verified: adapter, opt-in
                    model.set_item(it)
                response = model(it["question"])
                r = score_item(it, response, judge, deferral_judge)
                if getattr(model, "last_report", None) is not None:
                    r["verification"] = model.last_report
            except Exception as e:           # noqa: BLE001 — isolate one bad item
                r = error_result(it, e)
                failed.append(it["id"])
            log.write(json.dumps(r, ensure_ascii=False) + "\n")
            log.flush()
            status = "ERROR" if (r.get("detail") or {}).get("error") else f"passed={r['passed']}"
            print(f"[{len(done) + i}/{total}] {it['id']} {status}", file=sys.stderr)

    # Render the scorecard from the full (resumed + new) set, deduped, in item order.
    merged = read_results_log(results_path)
    results = [merged[it["id"]] for it in items if it["id"] in merged]
    results_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in results) + "\n",
        encoding="utf-8")                    # rewrite clean (dedup any re-run lines)
    card = scorecard.render(args.model, results)
    (args.out / f"{tag}_scorecard.md").write_text(card, encoding="utf-8")
    print(card)
    errored = [r["id"] for r in results if (r.get("detail") or {}).get("error")]
    if errored:
        print(f"\n[run] {len(errored)} item(s) FAILED after retries (re-run to retry "
              f"just these): {', '.join(errored)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
