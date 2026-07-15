"""
Groq LLM Review Engine
————————————————————————
Uses the Groq cloud API (https://console.groq.com) to run fast, free code reviews
using Llama 3, Mixtral, or Gemma — no local GPU needed.

Get a free API key at: https://console.groq.com/keys
Add it to your .env file as:  GROQ_API_KEY=gsk_...

Falls back gracefully (returns None) when:
  - GROQ_API_KEY is not set
  - The API call fails for any reason
"""
from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error
from typing import Callable

from config import settings
from ingestion import SourceFile, collect_stats
from llm_prompt import build_review_prompt, parse_llm_response, assemble_report
from models import ReviewReport

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# Models in preference order — all free on Groq's free tier
GROQ_MODELS = [
    "llama-3.3-70b-versatile",   # Best quality, handles large context
    "llama-3.1-8b-instant",      # Fast, good for smaller codebases
    "mixtral-8x7b-32768",        # Great for code, 32k context
    "gemma2-9b-it",              # Good fallback
]


# ── Public entry point ────────────────────────────────────────

def run_groq_review(
    files: list[SourceFile],
    repo_name: str,
    repo_source: str,
    progress_cb: Callable[[str], None] | None = None,
) -> ReviewReport | None:
    """
    Attempt a Groq-powered review.

    Returns a ReviewReport on success, or None if:
      - GROQ_API_KEY is not configured
      - The API call fails
      - The response cannot be parsed
    The caller should fall back to the next engine when None is returned.
    """
    def _prog(msg: str) -> None:
        if progress_cb:
            progress_cb(msg)

    api_key = getattr(settings, "GROQ_API_KEY", "").strip()
    if not api_key:
        logger.info("GROQ_API_KEY not set — skipping Groq.")
        return None

    _prog("Preparing code for Groq AI review…")

    stats = collect_stats(files)
    # Groq supports large context; llama-3.3-70b handles ~128k tokens
    prompt = build_review_prompt(files, stats, max_chars=40_000)

    for model in GROQ_MODELS:
        _prog(f"Sending code to Groq ({model})…")
        try:
            raw = _call_groq(api_key, model, prompt)
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                logger.warning("Groq rate limit on %s — trying next model.", model)
                continue
            if exc.code in (400, 413):
                logger.warning("Groq rejected %s (context too large?) — trying next model.", model)
                continue
            logger.warning("Groq HTTP %d on %s — skipping Groq.", exc.code, model)
            return None
        except Exception as exc:
            logger.warning("Groq call failed (%s): %s — skipping Groq.", model, exc)
            return None
    else:
        _prog("All Groq models failed — switching to static analysis…")
        return None

    _prog("Parsing Groq review response…")
    try:
        data = parse_llm_response(raw)
    except ValueError as exc:
        logger.warning("Could not parse Groq response: %s", exc)
        _prog("Could not understand Groq response — switching to static analysis…")
        return None

    _prog("Building report…")
    report = assemble_report(data, stats, repo_name, repo_source)

    logger.info(
        "Groq review complete: model=%s repo=%r findings=%d score=%.1f",
        model, repo_name, len(report.findings), report.overall_score,
    )
    return report


# ── Groq API call ─────────────────────────────────────────────

def _call_groq(api_key: str, model: str, prompt: str) -> str:
    """Call the Groq chat completions endpoint and return the response text."""
    payload = json.dumps({
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a senior software engineer specialising in code review. "
                    "You always respond with a single valid JSON object and nothing else. "
                    "No markdown, no prose outside the JSON."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 8192,
        "response_format": {"type": "json_object"},
    }).encode()

    req = urllib.request.Request(
        GROQ_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    logger.info("Calling Groq model=%s prompt_len=%d", model, len(prompt))
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read())
        text = body["choices"][0]["message"]["content"]
        logger.info("Groq response length: %d chars", len(text))
        return text
