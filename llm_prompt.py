"""
Shared prompt builder and JSON response parser used by all LLM review engines
(Ollama, Groq, and any future provider).
"""
from __future__ import annotations

import json
import logging
import re
import textwrap

from ingestion import SourceFile, collect_stats
from models import Finding, FinalVerdict, ReviewReport, RoadmapItem, Scorecard

logger = logging.getLogger(__name__)

# Max characters of source code to include in the prompt.
# Groq's context window is large (131 k tokens for llama3) so we allow more.
DEFAULT_MAX_CHARS = 40_000


# ── Prompt ────────────────────────────────────────────────────

def build_review_prompt(files: list[SourceFile], stats: dict,
                        max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Return the full code-review prompt ready to send to any LLM."""

    # Prioritise source code over docs / config
    ordered = sorted(
        files,
        key=lambda f: (
            f.path.endswith((".md", ".txt", ".rst")),
            f.path.endswith((".json", ".yaml", ".yml", ".toml", ".ini")),
        )
    )

    sections: list[str] = []
    used = 0
    for sf in ordered:
        block = f"### FILE: {sf.path}\n```\n{sf.content}\n```"
        if used + len(block) > max_chars:
            stub = (f"### FILE: {sf.path} [truncated — too large to include fully]\n"
                    f"```\n{sf.content[:400]}\n...\n```")
            sections.append(stub)
            break
        sections.append(block)
        used += len(block)

    code_block = "\n\n".join(sections)

    return textwrap.dedent(f"""
    You are a senior software engineer doing a thorough, honest code review.
    Give specific, plain-English feedback that anyone — including non-technical
    stakeholders — can understand. Explain *why* something matters.
    Avoid acronyms and jargon. Be direct and actionable.

    ## Project info
    - Languages: {', '.join(stats['languages']) or 'unknown'}
    - Frameworks: {', '.join(stats['frameworks']) or 'none detected'}
    - Files: {stats['file_count']}
    - Approximate lines of code: {stats['estimated_loc']}

    ## Source code
    {code_block}

    ## Instructions
    Return ONLY a single valid JSON object — no markdown fences, no text outside the JSON.

    {{
      "project_type": "short description e.g. Web API, CLI tool, data pipeline",
      "overall_score": <number 0-10>,
      "confidence_pct": <integer 0-100>,
      "final_verdict": "<one of: Enterprise Ready | Production Ready | Production Ready with Minor Improvements | Production Ready with Moderate Improvements | Requires Refactoring Before Production | Requires Major Refactoring | Not Recommended for Production>",
      "verdict_justification": "1-2 plain-English sentences explaining the verdict",
      "architecture_review": "3-5 sentences: what does this code do and how is it structured?",
      "technical_debt": "Plain-English summary of any shortcuts, hacks, or unfinished work",
      "production_ready": true or false,
      "enterprise_ready": true or false,
      "strengths": ["what the code does well — in plain English"],
      "weaknesses": ["what the code does poorly — in plain English"],
      "missing_practices": ["important things that are absent from the codebase"],
      "findings": [
        {{
          "id": "F-001",
          "severity": "Critical | High | Medium | Low | Informational",
          "category": "Security | Reliability | Maintainability | Performance | Documentation | Testability | Observability | Configuration",
          "file_path": "path/to/file or null",
          "line_numbers": "42 or 10-30 or null",
          "description": "One sentence — exactly what the problem is and where.",
          "root_cause": "Why did this happen? Explain as if to a smart non-developer.",
          "business_impact": "What goes wrong for users or the business if this is not fixed?",
          "technical_impact": "What breaks or degrades technically if this is not fixed?",
          "recommendation": "Step-by-step plain-English instructions to fix it.",
          "effort": "Small | Medium | Large"
        }}
      ],
      "scorecard": {{
        "architecture": 0-10,
        "design": 0-10,
        "coding_standards": 0-10,
        "maintainability": 0-10,
        "readability": 0-10,
        "performance": 0-10,
        "security": 0-10,
        "scalability": 0-10,
        "reliability": 0-10,
        "testability": 0-10,
        "documentation": 0-10,
        "devops_readiness": 0-10,
        "cloud_readiness": 0-10,
        "api_design": 0-10,
        "database_design": 0-10,
        "automation_practices": 0-10,
        "enterprise_compliance": 0-10,
        "production_readiness": 0-10,
        "enterprise_readiness": 0-10
      }},
      "roadmap": [
        {{
          "title": "Plain-English action title",
          "priority": "Immediate | Short Term | Medium Term | Long Term",
          "effort": "Small | Medium | Large",
          "finding_ids": ["F-001"],
          "expected_benefit": "Plain-English benefit of doing this"
        }}
      ]
    }}

    Generate at least 5 findings. Mention file names and line numbers wherever visible.
    Every description, root cause, impact, and recommendation must be plain English —
    no technical jargon that a non-developer would not understand.
    """).strip()


# ── Response parser ───────────────────────────────────────────

def parse_llm_response(raw: str) -> dict:
    """
    Extract and parse the JSON object from an LLM response.
    Handles markdown fences and stray prose before/after the JSON.
    """
    text = raw.strip()

    # Strip ```json ... ``` or ``` ... ``` fences
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```\s*$", "", text, flags=re.MULTILINE)
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find the first complete {...} block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    raise ValueError(
        "The AI model did not return valid JSON. "
        "Try a different model or check the server logs for the raw response."
    )


# ── Report assembler ──────────────────────────────────────────

VALID_VERDICTS: set[str] = {
    "Enterprise Ready",
    "Production Ready",
    "Production Ready with Minor Improvements",
    "Production Ready with Moderate Improvements",
    "Requires Refactoring Before Production",
    "Requires Major Refactoring",
    "Not Recommended for Production",
}


def assemble_report(data: dict, stats: dict,
                    repo_name: str, repo_source: str) -> ReviewReport:
    """Convert parsed LLM JSON + collected stats into a ReviewReport."""

    # Scorecard — default any missing dimension to 6.0
    sc_raw = data.get("scorecard", {})
    scorecard = Scorecard(**{
        k: float(sc_raw.get(k, 6.0))
        for k in Scorecard.model_fields
    })

    # Findings
    findings: list[Finding] = []
    for i, f in enumerate(data.get("findings", []), 1):
        f.setdefault("id", f"F-{i:03d}")
        if f.get("severity") not in (
                "Critical", "High", "Medium", "Low", "Informational"):
            f["severity"] = "Medium"
        try:
            findings.append(Finding(**f))
        except Exception as exc:
            logger.warning("Skipping malformed finding #%d: %s", i, exc)

    # Roadmap
    roadmap: list[RoadmapItem] = []
    for item in data.get("roadmap", []):
        try:
            roadmap.append(RoadmapItem(**item))
        except Exception as exc:
            logger.warning("Skipping malformed roadmap item: %s", exc)

    # Verdict
    raw_verdict = data.get("final_verdict", "Requires Refactoring Before Production")
    verdict: FinalVerdict = (
        raw_verdict if raw_verdict in VALID_VERDICTS
        else "Requires Refactoring Before Production"
    )

    return ReviewReport(
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
        final_verdict=verdict,
        verdict_justification=data.get("verdict_justification", ""),
        architecture_review=data.get("architecture_review", ""),
        findings=findings,
        technical_debt=data.get("technical_debt", ""),
        missing_practices=data.get("missing_practices", []),
        scorecard=scorecard,
        roadmap=roadmap,
    )
