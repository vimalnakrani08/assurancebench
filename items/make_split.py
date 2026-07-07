"""Assign the contamination-control train/test split — stratified, seeded, idempotent.

AssuranceBench is split into two disjoint sets, recorded as a ``split`` field on
every item and mirrored in ``items/split_manifest.json``:

  - ``test`` — the held-out EVALUATION set and the CONTAMINATION BOUNDARY. It must
    NEVER be used to generate or train SFT data. The contamination check
    (src/contamination.py) enforces this against SFT data; this split is
    what it checks against. ~80% of items.
  - ``dev``  — a small development / sanity-check set. ~20% of items.

The split is STRATIFIED so ``test`` is representative of the whole, not skewed:
  - every capability task_category is split proportionally;
  - the citation_currency cluster is its own stratum (so it isn't dumped on one
    side);
  - within safety, every (zone x hard/soft) cell is split proportionally — which
    preserves both the 5-zone and the hard/soft proportions at once.

Determinism: each stratum is shuffled by a Random seeded from a fixed string that
includes SPLIT_SEED and the stratum key, so re-running reproduces the exact split
(Python seeds Random from a str via SHA-512 — stable across platforms/versions).
Re-running after re-authoring re-applies the same assignment to unchanged items.

    python items/make_split.py            # rewrites items/*.jsonl + manifest
    python items/make_split.py --check     # verify current files match; no writes
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.schema import validate_item  # noqa: E402

SPLIT_SEED = 20260616          # fixed stamp for reproducibility; record in reports
DEV_FRACTION = 0.20            # ~80/20 test/dev (see split report for the rationale)
ITEMS_DIR = Path(__file__).resolve().parent
MANIFEST = ITEMS_DIR / "split_manifest.json"


def stratum_key(item: dict) -> str:
    """The finest stratum an item belongs to (drives proportional allocation)."""
    if item["suite"] == "safety":
        # (zone x hard/soft) preserves zone AND severity proportions together
        return f"safety:{item['task_category']}:{item.get('severity')}"
    if (item.get("metadata") or {}).get("type") == "citation_currency":
        return "capability:citation_currency"     # split the cluster on its own
    return f"capability:{item['task_category']}"


def assign(items: list[dict]) -> dict[str, str]:
    """Return {id: split}. dev_n = round(DEV_FRACTION * stratum size); the rest is
    test. Tiny strata (n < 3) round to 0 dev, so they sit wholly in test — test
    representation of every stratum is therefore guaranteed for any n >= 1."""
    by: dict[str, list[str]] = {}
    for it in items:
        by.setdefault(stratum_key(it), []).append(it["id"])
    out: dict[str, str] = {}
    for key in sorted(by):
        ids = sorted(by[key])                       # stable input order
        rng = random.Random(f"assurancebench-split-v1::{SPLIT_SEED}::{key}")
        rng.shuffle(ids)
        dev_n = round(DEV_FRACTION * len(ids))
        for i, iid in enumerate(ids):
            out[iid] = "dev" if i < dev_n else "test"
    return out


def load_all() -> list[tuple[Path, list[dict]]]:
    files = sorted(p for p in ITEMS_DIR.glob("*.jsonl"))
    out = []
    for f in files:
        items = [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
        out.append((f, items))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Assign the stratified train/test split.")
    ap.add_argument("--check", action="store_true",
                    help="verify the on-disk split matches a fresh computation; no writes")
    args = ap.parse_args(argv)

    per_file = load_all()
    all_items = [it for _, items in per_file for it in items]
    split = assign(all_items)

    if args.check:
        mismatch = [it["id"] for it in all_items if it.get("split") != split[it["id"]]]
        if mismatch:
            print(f"[FAIL] {len(mismatch)} item(s) differ from the seeded split: "
                  f"{mismatch[:8]}{'...' if len(mismatch) > 8 else ''}")
            return 1
        print(f"[ok] all {len(all_items)} items match the seeded split (seed={SPLIT_SEED})")
        return 0

    # write the split field back into each file (only field added; order preserved)
    for f, items in per_file:
        for it in items:
            it["split"] = split[it["id"]]
            errs = validate_item(it)
            if errs:
                raise SystemExit(f"{it['id']} invalid after split: {errs}")
        f.write_text("\n".join(json.dumps(it, ensure_ascii=False) for it in items) + "\n",
                     encoding="utf-8")

    # manifest: the human-readable contamination-boundary artifact + reproducibility
    strata = {}
    for it in all_items:
        k = stratum_key(it)
        strata.setdefault(k, {"test": 0, "dev": 0})[split[it["id"]]] += 1
    manifest = {
        "seed": SPLIT_SEED,
        "dev_fraction": DEV_FRACTION,
        "method": "stratified by (capability task_category | citation_currency | "
                  "safety zone x severity); seeded per-stratum shuffle",
        "boundary": "split=='test' is the contamination boundary; it must NEVER seed "
                    "or train SFT data. Enforced by src/contamination.py.",
        "totals": {
            "test": sum(1 for v in split.values() if v == "test"),
            "dev": sum(1 for v in split.values() if v == "dev"),
            "all": len(split),
        },
        "by_stratum": {k: strata[k] for k in sorted(strata)},
        "ids": {iid: split[iid] for iid in sorted(split)},
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(f"[done] split {manifest['totals']['all']} items -> "
          f"test {manifest['totals']['test']} / dev {manifest['totals']['dev']} "
          f"(seed={SPLIT_SEED}); manifest -> {MANIFEST.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
