"""
Pydantic models for the Code Review Automation system.
"""
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field


# ── Severity ──────────────────────────────────────────────────

Severity = Literal["Critical", "High", "Medium", "Low", "Informational"]


# ── A single review finding ───────────────────────────────────

class Finding(BaseModel):
    id: str = Field(..., description="Unique issue ID, e.g. F-001")
    severity: Severity
    category: str = Field(..., description="e.g. Security, Performance, Architecture")
    file_path: Optional[str] = None
    class_name: Optional[str] = None
    method_name: Optional[str] = None
    line_numbers: Optional[str] = None
    description: str
    root_cause: str
    business_impact: str
    technical_impact: str
    current_implementation: Optional[str] = None
    recommendation: str
    expected_benefits: Optional[str] = None
    references: Optional[str] = None
    effort: Literal["Small", "Medium", "Large"] = "Medium"


# ── Scorecard ─────────────────────────────────────────────────

class Scorecard(BaseModel):
    architecture:           float = 0.0
    design:                 float = 0.0
    coding_standards:       float = 0.0
    maintainability:        float = 0.0
    readability:            float = 0.0
    performance:            float = 0.0
    security:               float = 0.0
    scalability:            float = 0.0
    reliability:            float = 0.0
    testability:            float = 0.0
    documentation:          float = 0.0
    devops_readiness:       float = 0.0
    cloud_readiness:        float = 0.0
    api_design:             float = 0.0
    database_design:        float = 0.0
    automation_practices:   float = 0.0
    enterprise_compliance:  float = 0.0
    production_readiness:   float = 0.0
    enterprise_readiness:   float = 0.0

    def overall(self) -> float:
        values = [v for v in self.model_dump().values() if isinstance(v, (int, float))]
        return round(sum(values) / len(values), 1) if values else 0.0


# ── Refactoring roadmap item ──────────────────────────────────

class RoadmapItem(BaseModel):
    title: str
    priority: Literal["Immediate", "Short Term", "Medium Term", "Long Term"]
    effort: Literal["Small", "Medium", "Large"]
    finding_ids: list[str] = Field(default_factory=list)
    expected_benefit: str


# ── Full review report ────────────────────────────────────────

FinalVerdict = Literal[
    "Enterprise Ready",
    "Production Ready",
    "Production Ready with Minor Improvements",
    "Production Ready with Moderate Improvements",
    "Requires Refactoring Before Production",
    "Requires Major Refactoring",
    "Not Recommended for Production",
]


class ReviewReport(BaseModel):
    # Meta
    repo_name:       str
    repo_source:     str        # "github" | "zip" | "folder"
    languages:       list[str]  = Field(default_factory=list)
    frameworks:      list[str]  = Field(default_factory=list)
    file_count:      int        = 0
    estimated_loc:   int        = 0
    project_type:    str        = ""

    # Executive summary
    overall_score:         float   = 0.0
    confidence_pct:        int     = 0
    strengths:             list[str] = Field(default_factory=list)
    weaknesses:            list[str] = Field(default_factory=list)
    production_ready:      bool    = False
    enterprise_ready:      bool    = False
    final_verdict:         FinalVerdict = "Requires Refactoring Before Production"
    verdict_justification: str     = ""

    # Detailed content
    architecture_review: str              = ""
    findings:            list[Finding]    = Field(default_factory=list)
    technical_debt:      str              = ""
    missing_practices:   list[str]        = Field(default_factory=list)
    scorecard:           Scorecard        = Field(default_factory=Scorecard)
    roadmap:             list[RoadmapItem] = Field(default_factory=list)


# ── API request / response ────────────────────────────────────

class ReviewRequest(BaseModel):
    source_type: Literal["zip", "folder"] = "zip"
    zip_base64:  Optional[str] = Field(None, description="Base64-encoded zip content")
    folder_path: Optional[str] = Field(None, description="Absolute or relative local folder path")
    repo_name:   Optional[str] = None


class ReviewStatusResponse(BaseModel):
    job_id:   str
    status:   Literal["queued", "running", "done", "error"]
    progress: str = ""
    report:   Optional[ReviewReport] = None
    error:    Optional[str] = None
