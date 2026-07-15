"""
Repository Ingestion — fetch and parse source files from:
  • Base64-encoded zip upload
  • Local folder path

Returns a flat list of SourceFile objects ready for the review engine.
"""
from __future__ import annotations

import base64
import io
import logging
import os
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

def ingest_folder(folder_path: str, repo_name: str = "") -> tuple[str, list[SourceFile]]:
    """
    Walk a local folder and collect reviewable source files.
    Returns (repo_name, [SourceFile, ...]).
    """
    root = Path(folder_path).resolve()
    if not root.exists():
        raise ValueError(f"Folder not found: {folder_path!r}")
    if not root.is_dir():
        raise ValueError(f"Path is not a directory: {folder_path!r}")

    name = repo_name or root.name
    files: list[SourceFile] = []

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue

        # Skip blacklisted directories
        parts = path.relative_to(root).parts
        if any(p in SKIP_DIRS for p in parts):
            continue

        ext = path.suffix.lower()
        basename = path.name.lower()
        if ext not in INCLUDE_EXTENSIONS and basename not in ("dockerfile", "containerfile", "makefile"):
            continue

        size = path.stat().st_size
        if size > settings.MAX_FILE_BYTES:
            logger.debug("Skipping large file: %s (%d bytes)", path, size)
            continue

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            logger.warning("Could not read %s: %s", path, exc)
            continue

        rel = str(path.relative_to(root)).replace("\\", "/")
        files.append(SourceFile(
            path=rel,
            content=content,
            size_bytes=size,
            language=_detect_language(rel),
        ))

        if len(files) >= settings.MAX_FILES:
            logger.warning("MAX_FILES (%d) reached — truncating.", settings.MAX_FILES)
            break

    logger.info("Collected %d files from folder: %s", len(files), root)
    return name, files


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
