"""llm_judge scoring — rubric-based grading by a strong judge model (Claude API).

The judge prompt is saved and versioned under prompts/judge_<version>.md; the
version used is recorded on every result for reproducibility. The API key is read
from ANTHROPIC_API_KEY (never hardcoded, never logged). No call is made unless a
judge is actually run — building/validating the harness costs nothing.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from . import Result

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


@dataclass
class JudgeConfig:
    model: str = "claude-opus-4-8"      # strong judge; owner-approved spend
    prompt_version: str = "v1"
    pass_threshold: float = 0.7
    temperature: float = 0.0
    max_tokens: int = 512


def load_prompt(version: str) -> tuple[str, str]:
    """Return (system, user_template) parsed from the versioned prompt file."""
    text = (PROMPTS_DIR / f"judge_{version}.md").read_text(encoding="utf-8")
    sys_m = re.search(r"## System\s+(.*?)\n## User", text, re.S)
    usr_m = re.search(r"## User[^\n]*\n\s+(.*)$", text, re.S)
    return (sys_m.group(1).strip() if sys_m else "",
            usr_m.group(1).strip() if usr_m else "")


def call_claude(system: str, user: str, cfg: JudgeConfig, api_key: str) -> str:
    resp = httpx.post(
        ANTHROPIC_URL,
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": cfg.model, "max_tokens": cfg.max_tokens,
              "temperature": cfg.temperature, "system": system,
              "messages": [{"role": "user", "content": user}]},
        timeout=120.0,
    )
    resp.raise_for_status()
    return "".join(b.get("text", "") for b in resp.json()["content"])


def _parse_verdict(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.S)
    return json.loads(m.group(0)) if m else {"score": 0.0, "passed": False,
                                             "rationale": "unparseable judge output"}


def make_judge(cfg: JudgeConfig | None = None, api_key: str | None = None):
    """Return a judge(item, response) -> Result. Raises if no API key is present."""
    cfg = cfg or JudgeConfig()
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set — required for llm_judge")
    system, user_tmpl = load_prompt(cfg.prompt_version)

    def judge(item: dict, response: str) -> Result:
        user = user_tmpl.format(
            task_category=item.get("task_category", ""),
            difficulty=item.get("difficulty", ""),
            pass_threshold=cfg.pass_threshold,
            question=item.get("question", ""),
            reference_answer=item.get("reference_answer", ""),
            rubric=item.get("rubric", "Grade correctness against the reference."),
            response=response,
        )
        v = _parse_verdict(call_claude(system, user, cfg, key))
        score = float(v.get("score", 0.0))
        passed = bool(v.get("passed", score >= cfg.pass_threshold))
        return Result(score, passed, v.get("rationale", ""),
                      {"judge_model": cfg.model, "prompt_version": cfg.prompt_version})

    return judge
