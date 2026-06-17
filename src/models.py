"""Model endpoints under test. A model is a callable: prompt(str) -> answer(str).

Supports a local model via Ollama and API models (Anthropic, OpenAI); a mock is
provided so the harness can be exercised end-to-end with no network or spend.
Specs are "provider:model", e.g. "ollama:llama3.1:8b", "anthropic:claude-opus-4-8",
"openai:gpt-4o", "mock". API keys come from the environment, never code.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Callable

import httpx

Model = Callable[[str], str]

# A neutral system framing; the benchmark question carries the task instruction.
SYSTEM = ("You are an assistant for US external audit (PCAOB standards) and US "
          "GAAP accounting. Answer precisely and cite standards by their identifiers "
          "(e.g. AS 2301.05, ASC 606, 17 CFR 210.2-01) where relevant.")

# Transient network failures worth retrying (httpx.TimeoutException covers
# Read/Connect/Write/Pool timeouts — the ReadTimeout that killed the baseline).
TRANSIENT = (httpx.TimeoutException, httpx.ConnectError,
             httpx.RemoteProtocolError, httpx.PoolTimeout)


def _with_retries(do_call, retries: int = 3, backoff: float = 3.0):
    """Call do_call(); retry transient network errors and 5xx with linear backoff.
    Non-transient HTTP errors (4xx) raise immediately. Raises the last error if all
    attempts fail, so the caller can mark the item and continue."""
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return do_call()
        except TRANSIENT as e:
            last = e
        except httpx.HTTPStatusError as e:
            if e.response.status_code < 500:
                raise                       # 4xx won't fix itself
            last = e
        if attempt < retries:
            time.sleep(backoff * attempt)
    raise last  # type: ignore[misc]


def ollama_model(name: str, host: str = "http://localhost:11434",
                 retries: int = 3, num_ctx: int = 8192) -> Model:
    # num_ctx is large enough to hold a RAG prompt (retrieved passages); it is benign
    # for the short base-model prompts (same greedy output, just a larger KV budget).
    def run(prompt: str) -> str:
        def call() -> str:
            r = httpx.post(f"{host}/api/chat", json={
                "model": name, "stream": False,
                "messages": [{"role": "system", "content": SYSTEM},
                             {"role": "user", "content": prompt}],
                "options": {"temperature": 0.0, "num_ctx": num_ctx}}, timeout=300.0)
            r.raise_for_status()
            return r.json()["message"]["content"]
        return _with_retries(call, retries=retries)
    return run


def anthropic_model(name: str, retries: int = 3, backoff: float = 3.0) -> Model:
    key = os.environ["ANTHROPIC_API_KEY"]

    def run(prompt: str) -> str:
        # Mirror the working judge call (llm_judge.call_claude): standard headers,
        # NO temperature (claude-opus-4-8 deprecates/rejects it -> 400), retry
        # transient/5xx/429, and surface the API error body on other 4xx.
        last: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                r = httpx.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                             "content-type": "application/json"},
                    json={"model": name, "max_tokens": 1024, "system": SYSTEM,
                          "messages": [{"role": "user", "content": prompt}]},
                    timeout=120.0)
            except TRANSIENT as e:
                last = e
                if attempt < retries:
                    time.sleep(backoff * attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last = RuntimeError(f"Anthropic API {r.status_code} for model "
                                    f"{name!r}: {r.text[:300]}")
                if attempt < retries:
                    time.sleep(backoff * attempt)
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"Anthropic API {r.status_code} for model "
                                   f"{name!r}: {r.text[:1000]}")
            return "".join(b.get("text", "") for b in r.json()["content"])
        raise last  # type: ignore[misc]
    return run


def openai_model(name: str) -> Model:
    key = os.environ["OPENAI_API_KEY"]

    def run(prompt: str) -> str:
        r = httpx.post("https://api.openai.com/v1/chat/completions",
                       headers={"Authorization": f"Bearer {key}"},
                       json={"model": name, "temperature": 0.0,
                             "messages": [{"role": "system", "content": SYSTEM},
                                          {"role": "user", "content": prompt}]},
                       timeout=120.0)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    return run


def mock_model(fn: Callable[[str], str] | None = None) -> Model:
    return fn or (lambda prompt: "I am unable to answer this question.")


def rag_model(inner_spec: str, k: int = 5) -> Model:
    """Retrieval-augmented wrapper: retrieve hybrid passages from the auditlm corpus,
    build the grounding prompt, and answer with the INNER base model. The retrieval +
    grounding layer lives in the separate `auditlm` repo (rag/), located via the
    AUDITLM_RAG env var (default: sibling ../auditlm checkout). This keeps the
    benchmark independently usable — the adapter only activates when pointed at an
    auditlm checkout; nothing about items/split/scoring/judge changes.

    Spec form: "rag:ollama:llama3.1:8b" (inner_spec = "ollama:llama3.1:8b")."""
    auditlm = os.environ.get("AUDITLM_RAG") or str(
        Path(__file__).resolve().parents[2] / "auditlm")
    rag_dir = Path(auditlm) / "rag"
    if not rag_dir.exists():
        raise RuntimeError(
            f"RAG layer not found at {rag_dir} — set AUDITLM_RAG to your auditlm "
            f"checkout (the benchmark itself needs no auditlm; only the rag: adapter does).")
    sys.path.insert(0, str(rag_dir))
    from ground import Grounder  # noqa: E402  (imports torch/faiss/sentence-transformers)

    grounder = Grounder(k=k)          # builds the index-backed retriever once
    base = from_spec(inner_spec)

    def run(prompt: str) -> str:
        grounded, _ = grounder.ground(prompt)
        return base(grounded)
    return run


def from_spec(spec: str) -> Model:
    if spec == "mock":
        return mock_model()
    provider, _, name = spec.partition(":")
    if provider == "rag":
        return rag_model(name)        # name is the inner spec, e.g. "ollama:llama3.1:8b"
    if provider == "ollama":
        return ollama_model(name)
    if provider == "anthropic":
        return anthropic_model(name)
    if provider == "openai":
        return openai_model(name)
    raise ValueError(f"unknown model spec: {spec!r}")
