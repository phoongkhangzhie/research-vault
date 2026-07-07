"""_llm.py — shared urllib-based Anthropic Messages API call for gates/*.

Both ``support_matcher.py`` and ``coldread.py`` need the identical stdlib-only
LLM call (same endpoint, same auth, same error handling) — only the
``max_tokens`` budget and the timeout differ per caller. Extracted here so the
two gates share ONE implementation instead of two independently-maintained
copies of the same urllib plumbing (charter §6: reuse over create).

Stdlib only. Never imported eagerly by callers that only need mock judge_fn
in tests — both gates call this lazily inside their own ``_default_judge_fn``
wrapper, only when no ``judge_fn=`` override is supplied.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


def call_anthropic_messages(
    prompt: str,
    model: str,
    *,
    max_tokens: int = 1024,
    timeout: int = 60,
    caller_label: str = "gate",
) -> str:
    """Call the Anthropic Messages API via stdlib urllib.

    Requires ANTHROPIC_API_KEY in the environment. Zero external deps.

    Args:
        prompt:       the full prompt string to send.
        model:        the model-id to call.
        max_tokens:   response token budget (coldread needs more than
                      support_matcher — it may emit multiple FLAG blocks).
        timeout:      request timeout in seconds.
        caller_label: used only in the error message (e.g. "support-matcher",
                      "cold-read") so a missing-key error names its gate.

    Raises RuntimeError if the API key is absent or the request fails.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            f"ANTHROPIC_API_KEY is not set — the {caller_label} judge requires it. "
            "Set the env var or pass judge_fn= to mock the call."
        )

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_bytes = e.read()
        raise RuntimeError(
            f"Anthropic API error {e.code}: {body_bytes[:400]}"
        ) from e

    content = result.get("content", [])
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            return block["text"]
    return ""
