"""Scoring methods. Each returns a Result: {score: float in [0,1], passed: bool,
rationale: str, detail: dict}."""

from dataclasses import dataclass, field


@dataclass
class Result:
    score: float
    passed: bool
    rationale: str
    detail: dict = field(default_factory=dict)
