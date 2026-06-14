"""exact_citation scoring — the highest-trust task, so normalization is robust.

A citation is correct if its canonical form appears in the model's answer,
regardless of surface format. We canonicalize across the five citation systems in
the corpus and tolerate the common variants:

    AS 2301.05  ==  AS 2301 .05  ==  AS2301.05
    17 CFR 210.2-01  ==  17 CFR §210.2-01  ==  Rule 2-01 (Reg S-X)   [S-X/S-K only]
    ASC 606  ==  ASC 606-10
    SAB No. 99  ==  SAB 99  ==  Staff Accounting Bulletin No. 99
    GAGAS 3.87  ==  Yellow Book 3.87
"""

from __future__ import annotations

import re

from . import Result

# Recognizers for each citation system; each yields the citation's identifying span.
_PATTERNS = [
    re.compile(r"\bAS\s*\d{3,4}(?:\s*\.\s*[A-Z]?\d+)*", re.I),                 # PCAOB AS
    re.compile(r"\bASC\s*\d{3}(?:\s*-\s*\d+){0,3}", re.I),                     # FASB ASC
    re.compile(r"\b\d{1,2}\s*CFR\s*§?\s*\d+\.\d+(?:-\d+)?", re.I),             # CFR (S-X/S-K)
    re.compile(r"\b(?:staff\s+accounting\s+bulletin|SAB)\s*(?:no\.?\s*)?\d+[A-Z]?", re.I),
    re.compile(r"\b(?:GAGAS|yellow\s+book)\s*\d\.\d{2,3}", re.I),             # GAGAS
]


def canonical(citation: str) -> str:
    """Canonical, format-independent form of a single citation string."""
    s = citation.upper()
    for a, b in (("STAFF ACCOUNTING BULLETIN", "SAB"), ("YELLOW BOOK", "GAGAS"),
                 ("NO.", ""), ("NO ", ""), ("SECTION", ""), ("§", "")):
        s = s.replace(a, b)
    s = re.sub(r"\s+", "", s)
    return s


def extract(text: str) -> set[str]:
    """All citations present in a block of text, in canonical form."""
    found = set()
    for pat in _PATTERNS:
        for m in pat.finditer(text):
            found.add(canonical(m.group(0)))
    return found


def score(response: str, expected_citations: list[str], match: str = "exact") -> Result:
    """Score citations. match="exact" (default) requires the exact paragraph;
    match="prefix" lets a topic-level expectation be satisfied by a more specific
    cite (ASC 606 by ASC 606-10-25) — opt-in per item so exactness isn't inflated."""
    want = {canonical(c) for c in expected_citations}
    have = extract(response)
    matched = set()
    for w in want:
        if w in have or (match == "prefix" and any(h.startswith(w) for h in have)):
            matched.add(w)
    missing = want - matched
    s = len(matched) / len(want) if want else 0.0
    return Result(
        score=s,
        passed=not missing,
        rationale=("all expected citations present" if not missing
                   else f"missing ({match}): {sorted(missing)}"),
        detail={"expected": sorted(want), "found": sorted(have), "match": match,
                "matched": sorted(matched), "missing": sorted(missing)},
    )
