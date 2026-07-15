# Enterprise Code Review Automation

An AI-powered, enterprise-grade code review web application built with **FastAPI** and **Claude-3.5-Sonnet**. Paste any public GitHub or GitLab URL — or upload a zip file — and receive a comprehensive, structured code review report in seconds.

---

## Features

- **Multi-language support** — Python, JavaScript, TypeScript, Java, C#, Go, Rust, PHP, Ruby, SQL, Terraform, Docker, YAML, and 20+ more
- **25-dimension review** — Architecture, Security (OWASP Top 10), Performance, Maintainability, Testing, DevOps, Cloud Readiness, and more
- **Structured findings** — Every issue has severity, root cause, business impact, and a concrete recommendation
- **Scored scorecard** — 19 categories scored 0–10 with visual progress bars
- **Refactoring roadmap** — Prioritised as Immediate / Short Term / Medium Term / Long Term
- **Dual export** — Download the report as a self-contained HTML file or raw JSON
- **GitHub & GitLab URL ingestion** — Direct zip download via API, no git clone needed
- **Zip file upload** — Drag-and-drop or file picker for local projects
- **Async job queue** — Reviews run in the background; the UI polls for status

---

## Requirements

- Python 3.10+
- An [Anthropic API key](https://console.anthropic.com/)

---

## Setup

```bash
cd code_reviewer
pip install -r requirements.txt
cp .env.example .env
# Edit .env and set your ANTHROPIC_API_KEY
```

---

## Run

**Windows:**
```
run.bat
```

**Any OS:**
```bash
cd code_reviewer
python server.py
```

Then open **http://localhost:7000** in your browser.

---

## Project Structure

```
code_reviewer/
├── server.py          # FastAPI application — API routes, job queue
├── reviewer.py        # AI review engine — Claude orchestration
├── ingestion.py       # Repo ingestion — GitHub URL + zip parsing
├── report_builder.py  # HTML report generator
├── models.py          # Pydantic data models
├── config.py          # Settings from .env
├── index.html         # Web UI (single-page app)
├── requirements.txt   # Python dependencies
├── .env.example       # Environment variable template
└── run.bat            # Windows launcher
```

---

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | ✓ | — | Your Anthropic API key |
| `CLAUDE_MODEL` | | `claude-3-5-sonnet-20241022` | Claude model to use |
| `CLAUDE_MAX_TOKENS` | | `8000` | Max tokens in response |
| `HOST` | | `127.0.0.1` | Server bind address |
| `PORT` | | `7000` | Server port |
| `GITHUB_TOKEN` | | — | For private GitHub repos |
| `MAX_CODE_CHARS` | | `80000` | Max source chars sent to Claude |
| `MAX_FILES` | | `60` | Max files per review |
| `LOG_LEVEL` | | `INFO` | Logging verbosity |

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/review` | Start a review (JSON body: `url` or `zip_base64`) |
| `POST` | `/api/review/upload` | Start a review (multipart zip file upload) |
| `GET` | `/api/review/{job_id}` | Poll job status and get report |
| `GET` | `/api/review/{job_id}/html` | Download full HTML report |
| `GET` | `/api/review/{job_id}/json` | Download report as JSON |
| `GET` | `/health` | Health check |

---

## Review Coverage

The AI reviewer evaluates all 19 enterprise dimensions from the specification:

1. Architecture (SOLID, DRY, patterns)
2. Design
3. Coding Standards
4. Maintainability & Complexity
5. Readability
6. Performance (algorithms, N+1, async)
7. **Security** (OWASP Top 10, CWE, SANS 25)
8. Scalability
9. Reliability
10. Testability
11. Documentation
12. DevOps Readiness
13. Cloud Readiness
14. API Design
15. Database Design
16. Automation / RPA (if applicable)
17. Enterprise Compliance
18. Overall Production Readiness
19. Overall Enterprise Readiness

---

Made with **IBM Bob**
