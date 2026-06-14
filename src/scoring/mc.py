"""mc scoring — exact match for multiple-choice items."""

from __future__ import annotations

import re

from . import Result


def _chosen(response: str, choices: list[str]) -> str | None:
    """Extract the selected option from a model answer.

    Accepts a leading letter ("B", "B)", "(B)", "Answer: B") or a verbatim
    restatement of one of the choices.
    """
    m = re.search(r"\b([A-Z])\b[).:]?", response.strip()[:8])
    if m:
        idx = ord(m.group(1).upper()) - ord("A")
        if 0 <= idx < len(choices):
            return choices[idx]
    low = response.lower()
    hits = [c for c in choices if c.lower() in low]
    return hits[0] if len(hits) == 1 else None


def score(response: str, choices: list[str], correct_choice) -> Result:
    # correct_choice may be the option text or its letter/index
    if isinstance(correct_choice, int):
        correct = choices[correct_choice]
    elif len(str(correct_choice)) == 1 and str(correct_choice).isalpha():
        correct = choices[ord(str(correct_choice).upper()) - ord("A")]
    else:
        correct = str(correct_choice)
    chosen = _chosen(response, choices)
    ok = chosen is not None and chosen.strip().lower() == correct.strip().lower()
    return Result(
        score=1.0 if ok else 0.0,
        passed=ok,
        rationale=f"chose {chosen!r}; correct {correct!r}",
        detail={"chosen": chosen, "correct": correct},
    )
