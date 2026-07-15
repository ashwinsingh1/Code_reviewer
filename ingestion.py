"""
Repository Ingestion — fetch and parse source files from:
  • GitHub / GitLab public URL  (clones via API zip download)
  • Base64-encoded zip upload
  • Local folder path (internal use / testing)

Returns a flat list of SourceFile objects ready for the review engine.
"""
from __future__ import annotations

import base64
import io
import logging
import os
import re
import urllib.request
import urllib.error
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from config import settings

logger = logging.getLogger(__name__)

# ── File extension allowlist ──────────────────────────────────
INCLUDE_EXTENSIONS: set[str] = {
    ".py", ".js", ".ts", ".tsx", ".jsx",
    ".java", ".kt", ".cs", ".vb", ".fs",
    ".go", ".rs", ".rb", ".php", ".swift",
    ".c", ".cpp", ".h", ".hpp",
    ".sql", ".psql",
    ".sh", ".ps1", ".bat", ".cmd",
    ".tf", ".hcl",
    ".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".env.example",
    ".xml", ".html", ".css", ".scss", ".less",
    ".dockerfile", ".containerfile",
    ".md", ".rst", ".txt",
    ".graphql", ".gql",
}

# Folders to always skip
SKIP_DIRS: set[str] = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    ".env", "dist", "build", "out", "target", "bin", "obj",
    ".idea", ".vscode", "coverage", ".nyc_output", "vendor",
    "packages", ".terraform", ".cache",
}


@dataclass
class SourceFile:
    path: str           # relative path inside the repo
    content: str        # decoded text content
    size_bytes: int     # original byte size
    language: str = ""  # detected language label


# ── Language detection ────────────────────────────────────────

_EXT_TO_LANG: dict[str, str] = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
    ".tsx": "TypeScript/React", ".jsx": "JavaScript/React",
    ".java": "Java", ".kt": "Kotlin", ".cs": "C#", ".vb": "VB.NET",
    ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP",
    ".swift": "Swift", ".c": "C", ".cpp": "C++", ".h": "C/C++",
    ".sql": "SQL", ".psql": "PL/SQL",
    ".sh": "Bash", ".ps1": "PowerShell", ".bat": "Batch",
    ".tf": "Terraform", ".hcl": "HCL",
    ".yaml": "YAML", ".yml": "YAML",
    ".json": "JSON", ".toml": "TOML",
    ".xml": "XML", ".html": "HTML", ".css": "CSS",
    ".graphql": "GraphQL", ".gql": "GraphQL",
    ".md": "Markdown", ".dockerfile": "Dockerfile",
}


def _detect_language(path: str) -> str:
    ext = Path(path).suffix.lower()
    basename = Path(path).name.lower()
    if basename in ("dockerfile", "containerfile"):
        return "Dockerfile"
    return _EXT_TO_LANG.get(ext, "")


# ── GitHub URL normalisation & zip download ───────────────────

_GITHUB_RE = re.compile(
    r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
)
_GITLAB_RE = re.compile(
    r"https?://gitlab\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
)


def _download_github_zip(owner: str, repo: str) -> bytes:
    url = f"https://api.github.com/repos/{owner}/{repo}/zipball/HEAD"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "code-reviewer/1.0"}
    if settings.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {settings.GITHUB_TOKEN}"
    req = urllib.request.Request(url, headers=headers)
    logger.info("Downloading GitHub zip: %s/%s", owner, repo)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def _download_gitlab_zip(owner: str, repo: str) -> bytes:
    slug = urllib.request.quote(f"{owner}/{repo}", safe="")
    url = f"https://gitlab.com/api/v4/projects/{slug}/repository/archive.zip"
    logger.info("Downloading GitLab zip: %s/%s", owner, repo)
    with urllib.request.urlopen(url, timeout=60) as resp:
        return resp.read()


# ── Zip extraction ────────────────────────────────────────────

def _extract_zip(data: bytes) -> list[SourceFile]:
    files: list[SourceFile] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        # GitHub zips have a top-level folder prefix — strip it
        prefix = ""
        if names:
            first = names[0]
            if "/" in first:
                prefix = first.split("/")[0] + "/"

        for name in names:
            rel = name[len(prefix):]           # strip prefix
            if not rel or rel.endswith("/"):   # skip dirs
                continue

            parts = Path(rel).parts
            if any(p in SKIP_DIRS for p in parts):
                continue

            ext = Path(rel).suffix.lower()
            basename = Path(rel).name.lower()
            if ext not in INCLUDE_EXTENSIONS and basename not in ("dockerfile", "containerfile", "makefile"):
                continue

            info = zf.getinfo(name)
            if info.file_size > settings.MAX_FILE_BYTES:
                logger.debug("Skipping large file: %s (%d bytes)", rel, info.file_size)
                continue

            try:
                raw = zf.read(name)
                content = raw.decode("utf-8", errors="replace")
            except Exception as exc:
                logger.warning("Could not read %s: %s", rel, exc)
                continue

            files.append(SourceFile(
                path=rel,
                content=content,
                size_bytes=info.file_size,
                language=_detect_language(rel),
            ))

            if len(files) >= settings.MAX_FILES:
                logger.warning("MAX_FILES (%d) reached — truncating.", settings.MAX_FILES)
                break

    logger.info("Extracted %d files from zip", len(files))
    return files


# ── Public API ────────────────────────────────────────────────

def ingest_github_url(url: str) -> tuple[str, list[SourceFile]]:
    """
    Download and parse a GitHub or GitLab repository URL.
    Returns (repo_name, [SourceFile, ...]).
    """
    m = _GITHUB_RE.match(url.strip())
    if m:
        owner, repo = m["owner"], m["repo"]
        data = _download_github_zip(owner, repo)
        return repo, _extract_zip(data)

    m = _GITLAB_RE.match(url.strip())
    if m:
        owner, repo = m["owner"], m["repo"]
        data = _download_gitlab_zip(owner, repo)
        return repo, _extract_zip(data)

    raise ValueError(
        f"Unrecognised URL format: {url!r}\n"
        "Supported: https://github.com/owner/repo  or  https://gitlab.com/owner/repo"
    )


def ingest_zip_base64(b64: str, repo_name: str = "uploaded_repo") -> tuple[str, list[SourceFile]]:
    """
    Parse a base64-encoded zip file uploaded by the user.
    Returns (repo_name, [SourceFile, ...]).
    """
    try:
        data = base64.b64decode(b64)
    except Exception as exc:
        raise ValueError(f"Invalid base64 zip data: {exc}") from exc
    return repo_name, _extract_zip(data)


def collect_stats(files: list[SourceFile]) -> dict:
    """Derive languages, frameworks, file count, LOC estimate."""
    languages: set[str] = set()
    frameworks: set[str] = set()
    total_loc = 0

    framework_hints = {
        "fastapi": "FastAPI", "flask": "Flask", "django": "Django",
        "react": "React", "vue": "Vue.js", "angular": "Angular",
        "spring": "Spring Boot", "express": "Express.js",
        "rails": "Ruby on Rails", "laravel": "Laravel",
        "pytest": "pytest", "jest": "Jest", "unittest": "unittest",
        "sqlalchemy": "SQLAlchemy", "prisma": "Prisma", "hibernate": "Hibernate",
        "terraform": "Terraform", "kubernetes": "Kubernetes", "docker": "Docker",
        "pydantic": "Pydantic", "uvicorn": "uvicorn",
    }

    for f in files:
        if f.language:
            languages.add(f.language)
        total_loc += f.content.count("\n")
        lower = f.content.lower()
        for hint, name in framework_hints.items():
            if hint in lower:
                frameworks.add(name)

    return {
        "languages": sorted(languages),
        "frameworks": sorted(frameworks),
        "file_count": len(files),
        "estimated_loc": total_loc,
    }
