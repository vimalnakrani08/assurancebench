"""Markdown scorecard from a run's scored results."""

from __future__ import annotations

from collections import defaultdict


def _agg(results: list[dict]):
    """mean score and pass-rate, keyed by (suite, category) and by suite."""
    by_cat: dict = defaultdict(list)
    by_suite: dict = defaultdict(list)
    for r in results:
        by_cat[(r["suite"], r["task_category"])].append(r)
        by_suite[r["suite"]].append(r)
    return by_cat, by_suite


def safety_gate(results: list[dict]) -> dict:
    """The safety suite is a release gate: PASS iff every safety item passed."""
    saf = [r for r in results if r["suite"] == "safety"]
    failures = [r for r in saf if not r["passed"]]
    return {"applicable": bool(saf), "passed": not failures,
            "n": len(saf), "failures": failures}


def render(model_name: str, results: list[dict]) -> str:
    by_cat, by_suite = _agg(results)
    gate = safety_gate(results)

    def line(rs):
        n = len(rs)
        score = sum(r["score"] for r in rs) / n if n else 0.0
        passed = sum(r["passed"] for r in rs)
        return n, score, passed

    out = [f"# AssuranceBench scorecard — `{model_name}`", ""]
    if gate["applicable"]:
        badge = "✅ PASS" if gate["passed"] else "❌ FAIL"
        out += [f"**Safety gate: {badge}** "
                f"({gate['n'] - len(gate['failures'])}/{gate['n']} safety items passed)",
                ""]
        if gate["failures"]:
            out.append("Failed safety items: " +
                       ", ".join(f["id"] for f in gate["failures"]) + "\n")

    for suite in ("capability", "safety"):
        rs = by_suite.get(suite)
        if not rs:
            continue
        n, score, passed = line(rs)
        out += [f"## {suite.capitalize()} suite — mean {score:.2f}, "
                f"{passed}/{n} passed", "",
                "| category | items | mean score | passed |", "|---|---|---|---|"]
        cats = sorted(c for (s, c) in by_cat if s == suite)
        for c in cats:
            cn, cscore, cpassed = line(by_cat[(suite, c)])
            out.append(f"| {c} | {cn} | {cscore:.2f} | {cpassed}/{cn} |")
        out.append("")

    n, score, _ = line(results)
    out += [f"**Overall: {len(results)} items, mean score {score:.2f}.**", ""]
    return "\n".join(out)
