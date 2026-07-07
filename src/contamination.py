"""Contamination control — the hook that keeps benchmark numbers trustworthy.

Held-out eval items must NEVER appear (verbatim or near-duplicate) in SFT training
data. This checks a candidate SFT training set against the held-out items and flags
any overlap. Every item also carries ``source_provenance`` so a flagged overlap can
be traced.
"""

from __future__ import annotations

import re


def shingles(text: str, k: int = 8) -> set[str]:
    """Word k-grams (k=8 ≈ a clause) — long enough that incidental overlap is rare."""
    words = re.findall(r"\w+", text.lower())
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}


def build_train_index(train_texts: list[str], k: int = 8) -> set[str]:
    idx: set[str] = set()
    for t in train_texts:
        idx |= shingles(t, k)
    return idx


def check_contamination(eval_items: list[dict], train_texts: list[str],
                        max_overlap: float = 0.5, k: int = 8) -> list[dict]:
    """Flag eval items whose question+answer n-grams overlap training above the
    threshold. Returns one record per flagged item (empty == clean)."""
    train_idx = build_train_index(train_texts, k)
    flagged = []
    for it in eval_items:
        probe = f"{it.get('question', '')} {it.get('reference_answer', '')}"
        sh = shingles(probe, k)
        if not sh:
            continue
        overlap = len(sh & train_idx) / len(sh)
        if overlap >= max_overlap:
            flagged.append({"id": it.get("id"), "overlap": round(overlap, 3),
                            "source_provenance": it.get("source_provenance")})
    return flagged
