"""
AI Review Engine — orchestrates the Claude-3.5-Sonnet code review.

Strategy:
  1. Build a structured prompt from the repository source files.
  2. Send to Claude with the full enterprise review specification.
  3. Parse the JSON response into ReviewReport models.
  4. Fall back gracefully on any parsing error.
"""
from __future__ import annotations

import json
import logging
import re
import textwrap
from typing import Callable

import anthropic

from config import settings
from ingestion import SourceFile, collect_stats
from models import (
    Finding, FinalVerdict, ReviewReport, RoadmapItem, Scorecard
)

logger = logging.getLogger(__name__)

# ── System prompt ─────────────────────────────────────────────

SYSTEM_PROMPT = textwrap.dedent("""
You are an Enterprise Principal Software Architect and Senior Code Reviewer with 25+ years of experience.
You perform deep, evidence-based code reviews covering:
  Architecture (SOLID, DRY, KISS, patterns), Coding Standards, Maintainability, Performance,
  Security (OWASP Top 10, CWE, SANS 25), Exception Handling, Logging, Configuration,
  API Design, Database Design, Cloud Readiness, DevOps, Testing, Documentation,
  Dependencies, Technical Debt, and Enterprise Compliance.

You ALWAYS respond with valid JSON and nothing else — no markdown fences, no prose outside JSON.
The JSON schema is described in the user message.
""").strip()


# ── Prompt builder ────────────────────────────────────────────

def _build_prompt(files: list[SourceFile], stats: dict) -> str:
    # Assemble source code, respecting MAX_CODE_CHARS budget
    file_sections: list[str] = []
    total_chars = 0

    # Prioritise non-documentation files
    priority_order = sorted(
        files,
        key=lambda f: (
            f.path.endswith(".md") or f.path.endswith(".txt"),   # docs last
            f.path.endswith(".json") or f.path.endswith(".yaml"),  # config after code
        )
    )

    for sf in priority_order:
        snippet = f"### FILE: {sf.path}\n{sf.content}"
        if total_chars + len(snippet) > settings.MAX_CODE_CHARS:
            # Include a truncated version so Claude knows the file exists
            file_sections.append(
                f"### FILE: {sf.path} [TRUNCATED — file too large to include fully]\n"
                + sf.content[:500] + "\n...(truncated)"
            )
            break
        file_sections.append(snippet)
        total_chars += len(snippet)

    source_block = "\n\n".join(file_sections)

    schema_description = """
Return a single JSON object with this exact structure:

{
  "project_type": "string — e.g. Web API, CLI Tool, Microservice",
  "confidence_pct": integer 0-100,
  "strengths": ["string", ...],
  "weaknesses": ["string", ...],
  "production_ready": boolean,
  "enterprise_ready": boolean,
  "final_verdict": one of ["Enterprise Ready","Production Ready","Production Ready with Minor Improvements","Production Ready with Moderate Improvements","Requires Refactoring Before Production","Requires Major Refactoring","Not Recommended for Production"],
  "verdict_justification": "string",
  "architecture_review": "string — detailed architecture analysis",
  "technical_debt": "string — technical debt assessment",
  "missing_practices": ["string", ...],
  "findings": [
    {
      "id": "F-001",
      "severity": one of ["Critical","High","Medium","Low","Informational"],
      "category": "string",
      "file_path": "string or null",
      "class_name": "string or null",
      "method_name": "string or null",
      "line_numbers": "string or null",
      "description": "string",
      "root_cause": "string",
      "business_impact": "string",
      "technical_impact": "string",
      "current_implementation": "string or null",
      "recommendation": "string — concrete fix with example code where helpful",
      "expected_benefits": "string or null",
      "references": "string or null — e.g. OWASP A01, CWE-79",
      "effort": one of ["Small","Medium","Large"]
    }
  ],
  "scorecard": {
    "architecture": float,
    "design": float,
    "coding_standards": float,
    "maintainability": float,
    "readability": float,
    "performance": float,
    "security": float,
    "scalability": float,
    "reliability": float,
    "testability": float,
    "documentation": float,
    "devops_readiness": float,
    "cloud_readiness": float,
    "api_design": float,
    "database_design": float,
    "automation_practices": float,
    "enterprise_compliance": float,
    "production_readiness": float,
    "enterprise_readiness": float
  },
  "roadmap": [
    {
      "title": "string",
      "priority": one of ["Immediate","Short Term","Medium Term","Long Term"],
      "effort": one of ["Small","Medium","Large"],
      "finding_ids": ["F-001", ...],
      "expected_benefit": "string"
    }
  ]
}
"""

    return f"""You are reviewing the following repository.

## Repository Statistics
- Languages: {', '.join(stats['languages']) or 'Unknown'}
- Frameworks: {', '.join(stats['frameworks']) or 'None detected'}
- File count: {stats['file_count']}
- Estimated LoC: {stats['estimated_loc']}

## Source Files
{source_block}

## Instructions
Perform a comprehensive enterprise-grade code review covering ALL of:
Architecture, SOLID/DRY/KISS/YAGNI, Coding Standards, Maintainability, Cyclomatic Complexity,
Performance (algorithmic complexity, N+1 queries, loops, async issues),
Security (OWASP Top 10, SQL injection, XSS, CSRF, hardcoded secrets, weak crypto),
Exception handling, Logging & Observability, Configuration management,
API design (REST principles, versioning, auth, rate limiting, pagination),
Database design, Cloud/DevOps readiness, Testing gaps,
Documentation quality, Dependency risks, Technical debt.

Generate at least 10 findings. Be specific — cite file paths and line numbers where visible.
Every finding MUST include a concrete, actionable recommendation.

{schema_description}
"""


# ── Claude API call ───────────────────────────────────────────

def _call_claude(prompt: str, progress_cb: Callable[[str], None] | None = None) -> str:
    """Call Claude and return the raw response text."""
    client = anthropic.Anthropic(api_key=settings.get_api_key())

    if progress_cb:
        progress_cb("Sending code to Claude for analysis…")

    logger.info("Calling Claude model=%s max_tokens=%d", settings.CLAUDE_MODEL, settings.CLAUDE_MAX_TOKENS)

    message = client.messages.create(
        model=settings.CLAUDE_MODEL,
        max_tokens=settings.CLAUDE_MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text if message.content else ""
    logger.debug("Claude response length: %d chars", len(raw))
    return raw


# ── JSON parser ───────────────────────────────────────────────

def _parse_response(raw: str) -> dict:
    """Extract JSON from Claude response, handling markdown fences."""
    # Strip markdown code fences if present
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        # Try to find a JSON object inside the text
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        logger.error("Failed to parse Claude JSON: %s\nRaw (first 500): %s", exc, raw[:500])
        raise ValueError(f"Claude returned non-JSON content: {exc}") from exc


# ── Report assembler ──────────────────────────────────────────

def _assemble_report(
    data: dict,
    stats: dict,
    repo_name: str,
    repo_source: str,
) -> ReviewReport:
    """Convert parsed Claude JSON + stats into a ReviewReport."""

    # Parse scorecard
    sc_data = data.get("scorecard", {})
    scorecard = Scorecard(**{k: float(sc_data.get(k, 0)) for k in Scorecard.model_fields})

    # Parse findings
    findings: list[Finding] = []
    for i, f in enumerate(data.get("findings", []), 1):
        f.setdefault("id", f"F-{i:03d}")
        try:
            findings.append(Finding(**f))
        except Exception as exc:
            logger.warning("Skipping malformed finding #%d: %s", i, exc)

    # Parse roadmap
    roadmap: list[RoadmapItem] = []
    for item in data.get("roadmap", []):
        try:
            roadmap.append(RoadmapItem(**item))
        except Exception as exc:
            logger.warning("Skipping malformed roadmap item: %s", exc)

    report = ReviewReport(
        repo_name=repo_name,
        repo_source=repo_source,
        languages=stats["languages"],
        frameworks=stats["frameworks"],
        file_count=stats["file_count"],
        estimated_loc=stats["estimated_loc"],
        project_type=data.get("project_type", ""),
        overall_score=scorecard.overall(),
        confidence_pct=int(data.get("confidence_pct", 85)),
        strengths=data.get("strengths", []),
        weaknesses=data.get("weaknesses", []),
        production_ready=bool(data.get("production_ready", False)),
        enterprise_ready=bool(data.get("enterprise_ready", False)),
        final_verdict=data.get("final_verdict", "Requires Refactoring Before Production"),
        verdict_justification=data.get("verdict_justification", ""),
        architecture_review=data.get("architecture_review", ""),
        findings=findings,
        technical_debt=data.get("technical_debt", ""),
        missing_practices=data.get("missing_practices", []),
        scorecard=scorecard,
        roadmap=roadmap,
    )
    return report


# ── Main entry point ──────────────────────────────────────────

def run_review(
    files: list[SourceFile],
    repo_name: str,
    repo_source: str,
    progress_cb: Callable[[str], None] | None = None,
) -> ReviewReport:
    """
    Orchestrate the full review pipeline:
      ingest → stats → prompt → Claude → parse → assemble → return
    """
    if progress_cb:
        progress_cb("Collecting repository statistics…")

    stats = collect_stats(files)
    logger.info(
        "Review started: repo=%r files=%d loc=%d languages=%s",
        repo_name, stats["file_count"], stats["estimated_loc"],
        ",".join(stats["languages"]),
    )

    if progress_cb:
        progress_cb(f"Building review prompt ({stats['file_count']} files, ~{stats['estimated_loc']} lines)…")

    prompt = _build_prompt(files, stats)

    raw_response = _call_claude(prompt, progress_cb=progress_cb)

    if progress_cb:
        progress_cb("Parsing AI review output…")

    data = _parse_response(raw_response)

    if progress_cb:
        progress_cb("Assembling final report…")

    report = _assemble_report(data, stats, repo_name, repo_source)

    logger.info(
        "Review complete: repo=%r findings=%d overall=%.1f verdict=%r",
        repo_name, len(report.findings), report.overall_score, report.final_verdict,
    )
    return report
