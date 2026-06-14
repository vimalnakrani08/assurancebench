# AssuranceBench

**An open evaluation benchmark for US GAAP / PCAOB-based external audit — with a
first-class safety suite.**

AssuranceBench scores a language model on two suites that share one harness but
are reported separately:

- **Capability suite** — the 9 weighted taxonomy tasks (citation lookup, procedure
  suggestion, concept explanation, disclosure review, comparison, filing
  summarization, document drafting, anomaly flagging, calculation support).
- **Safety suite** — the 5 dangerous-to-get-wrong zones (independence, legal/
  regulatory, tax, fraud, novel-transaction treatment), scored for **calibrated
  deferral**: explain the framework, defer the firm conclusion to a professional —
  not a confident yes/no, and not an unhelpful blanket refusal. The safety suite
  is a **release gate**.

> **Status: v1, evolving.** The harness, schema, and scoring are built; the items
> are authored and **domain-verified in chat** (see below). This is research
> infrastructure, not a finished benchmark.

## How items are authored (important)

Item *infrastructure* is code; item *correctness* is human-reviewed. This
repository's code builds the schema, harness, runner, and scoring, and can *draft*
candidate items — but **a generated item is never treated as final**. Every
question and its verified answer is reviewed by a domain expert before it counts.
Held-out items never touch training data (see Contamination, below).

## Item schema (JSONL)

```jsonc
{
  "id": "...", "suite": "capability" | "safety",
  "task_category": "...",          // one of the 9 tasks or 5 safety zones
  "question": "...",
  "reference_answer": "...",        // verified-correct (chat-reviewed)
  "answer_type": "citation" | "free_form" | "multiple_choice" | "deferral",
  "expected_citations": ["AS 2301.05"],   // for exact_citation
  "choices": [...], "correct_choice": "B", // for mc
  "rubric": "...",                  // for llm_judge
  "scoring_method": "exact_citation" | "llm_judge" | "deferral_check" | "mc",
  "difficulty": "basic" | "intermediate" | "advanced",
  "source_provenance": "...",       // ground-truth origin (contamination tracking)
  "deferral_required": true,        // safety: must the model defer the conclusion?
  "metadata": {}
}
```

`src/schema.py` validates structure (enums, conditional required fields, suite/
deferral consistency). It does not judge correctness.

## Scoring methods

| method | used for | how |
|---|---|---|
| `exact_citation` | citation lookup (highest-trust) | canonicalizes across AS / ASC / 17 CFR / SAB / GAGAS and tolerates format variants (`AS 2301.05` == `AS 2301 .05`) |
| `mc` | multiple choice | exact match (letter or option text) |
| `llm_judge` | free-form | rubric-based grading by a strong judge model (Claude API); the **judge prompt is saved & versioned** in `prompts/judge_v1.md` |
| `deferral_check` | safety | did the answer explain the framework **and** defer (when `deferral_required`), or answer directly (calibration counter-examples)? Transparent heuristic by default; a judge-backed grader can be wired in |

## Run

```bash
pip install -r requirements.txt

# self-test the harness (no network, no API, no spend)
python -m tests.test_harness

# run a model over a benchmark file
python -m src.runner --items items/seed.jsonl --model "ollama:qwen2.5:7b" --suite both
python -m src.runner --items items/seed.jsonl --model "anthropic:claude-opus-4-8" --judge
```

Model specs: `ollama:<name>`, `anthropic:<model>`, `openai:<model>`, or `mock`.
API keys come from the environment (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) — never
hardcoded. The runner emits per-category / per-suite / overall scores, a markdown
scorecard, and an explicit **safety-gate PASS/FAIL**.

## Contamination control

Benchmark numbers are only trustworthy if eval items never leak into training.
`src/contamination.py` flags any eval item whose question+answer n-grams overlap
the SFT training set above a threshold (run in Phase 3 against the generated SFT
data). Every item carries `source_provenance` so a flagged overlap is traceable.

## Honest baselines

The base (pre-SFT) model is expected to score **low** — that is the point: the
benchmark measures the headroom that SFT + RAG must close, and reports where the
model loses on knowledge but can win on grounding, citation, and calibrated
deferral.

## License

Apache 2.0 (to match the AuditLM project). Author: Vimal Nakrani — Independent
Researcher.
