"""Live CANDIDATE-model smoketest — one call through src/models.py before a full
run, so a broken candidate path (e.g. a 400) shows in seconds instead of erroring
all 166 items. Distinct from scripts/judge_smoketest.py, which tests the JUDGE path.

    export ANTHROPIC_API_KEY=sk-ant-...
    python scripts/model_smoketest.py                          # anthropic:claude-opus-4-8
    python scripts/model_smoketest.py anthropic:claude-sonnet-4-6
    python scripts/model_smoketest.py ollama:llama3.1:8b

On failure it prints the surfaced API error body (model name + reason), not a bare
HTTPStatusError, so the malformed field is obvious.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import models  # noqa: E402


def main() -> int:
    spec = sys.argv[1] if len(sys.argv) > 1 else "anthropic:claude-opus-4-8"
    print(f"candidate model: {spec}")
    try:
        model = models.from_spec(spec)
    except Exception as e:  # noqa: BLE001 — bad spec / missing API key
        print(f"could not build model: {type(e).__name__}: {e}")
        return 2
    t0 = time.monotonic()
    try:
        out = model("In one short sentence, what does PCAOB AS 2401 address?")
        print(f"OK in {time.monotonic() - t0:.1f}s -> {out[:200]!r}")
        print("\nCandidate path works — safe to run the full baseline with this model.")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"FAILED in {time.monotonic() - t0:.1f}s: {type(e).__name__}: {e}")
        print("\nDo NOT run the full baseline yet — the error above names the cause.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
