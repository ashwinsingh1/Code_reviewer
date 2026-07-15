"""
Integration tests for the Code Review Automation FastAPI server.
Uses httpx TestClient — no real Claude calls (mocked).
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# Patch settings before importing server to avoid requiring a real .env
import config
config.settings.ANTHROPIC_API_KEY = "sk-test-key"


def _make_mock_report():
    from models import ReviewReport, Scorecard
    return ReviewReport(
        repo_name="test_repo",
        repo_source="github",
        languages=["Python"],
        frameworks=["FastAPI"],
        file_count=5,
        estimated_loc=200,
        project_type="Web API",
        overall_score=6.5,
        confidence_pct=90,
        strengths=["Clean structure"],
        weaknesses=["No tests"],
        production_ready=False,
        enterprise_ready=False,
        final_verdict="Requires Refactoring Before Production",
        verdict_justification="No tests found.",
        scorecard=Scorecard(
            architecture=6, design=6, coding_standards=7, maintainability=6,
            readability=7, performance=6, security=5, scalability=5,
            reliability=5, testability=2, documentation=6, devops_readiness=3,
            cloud_readiness=3, api_design=7, database_design=5,
            automation_practices=5, enterprise_compliance=4,
            production_readiness=5, enterprise_readiness=4,
        ),
    )


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    import server
    return TestClient(server.app)


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_root_serves_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Code Review" in resp.text


def test_start_review_missing_url(client):
    resp = client.post("/api/review", json={"source_type": "github"})
    assert resp.status_code == 400


def test_start_review_queues_job(client):
    with patch("server._run_review_job"):   # prevent actual execution
        resp = client.post("/api/review", json={
            "source_type": "github",
            "url": "https://github.com/owner/repo",
        })
    assert resp.status_code == 200
    data = resp.json()
    assert "job_id" in data
    assert data["status"] in ("queued", "running")


def test_get_unknown_job(client):
    resp = client.get("/api/review/nonexistent-job-id")
    assert resp.status_code == 404


def test_download_html_not_ready(client):
    with patch("server._run_review_job"):
        resp = client.post("/api/review", json={
            "source_type": "github",
            "url": "https://github.com/owner/repo",
        })
    job_id = resp.json()["job_id"]
    resp2 = client.get(f"/api/review/{job_id}/html")
    assert resp2.status_code == 400   # not done yet


def test_full_review_cycle(client):
    """Simulate a complete review cycle with mocked reviewer."""
    mock_report = _make_mock_report()

    with patch("server._run_review_job") as mock_job:
        # Start the job
        resp = client.post("/api/review", json={
            "source_type": "github",
            "url": "https://github.com/owner/repo",
        })
        job_id = resp.json()["job_id"]

        # Manually mark the job as done with our mock report
        import server as srv
        srv._jobs[job_id].status = "done"
        srv._jobs[job_id].report = mock_report

    # Poll status
    resp2 = client.get(f"/api/review/{job_id}")
    assert resp2.status_code == 200
    data = resp2.json()
    assert data["status"] == "done"
    assert data["report"]["repo_name"] == "test_repo"

    # Download HTML
    resp3 = client.get(f"/api/review/{job_id}/html")
    assert resp3.status_code == 200
    assert "text/html" in resp3.headers["content-type"]
    assert "test_repo" in resp3.text

    # Download JSON
    resp4 = client.get(f"/api/review/{job_id}/json")
    assert resp4.status_code == 200
    assert resp4.json()["repo_name"] == "test_repo"
