"""
Tests for the ingestion module — no network calls (mocked).
"""
import io
import json
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
config.settings.ANTHROPIC_API_KEY = "sk-test-key"


def _make_zip(files: dict[str, str]) -> bytes:
    """Create an in-memory zip with a GitHub-style top-level prefix."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in files.items():
            zf.writestr(f"myrepo-abc123/{path}", content)
    return buf.getvalue()


def test_extract_zip_basic():
    from ingestion import _extract_zip
    data = _make_zip({"main.py": "print('hello')", "README.md": "# Hello"})
    files = _extract_zip(data)
    paths = [f.path for f in files]
    assert "main.py" in paths
    assert "README.md" in paths


def test_extract_zip_skips_pycache():
    from ingestion import _extract_zip
    data = _make_zip({
        "main.py": "x = 1",
        "__pycache__/main.cpython-311.pyc": "binary",
    })
    files = _extract_zip(data)
    assert all("__pycache__" not in f.path for f in files)


def test_extract_zip_skips_node_modules():
    from ingestion import _extract_zip
    data = _make_zip({
        "index.js": "console.log(1)",
        "node_modules/lodash/index.js": "module.exports = {}",
    })
    files = _extract_zip(data)
    assert all("node_modules" not in f.path for f in files)


def test_extract_zip_skips_unknown_extensions():
    from ingestion import _extract_zip
    data = _make_zip({
        "main.py": "x=1",
        "photo.png": "\x89PNG",
        "binary.exe": "MZ",
    })
    files = _extract_zip(data)
    paths = [f.path for f in files]
    assert "main.py" in paths
    assert "photo.png" not in paths
    assert "binary.exe" not in paths


def test_language_detection():
    from ingestion import _detect_language
    assert _detect_language("app.py") == "Python"
    assert _detect_language("server.ts") == "TypeScript"
    assert _detect_language("Dockerfile") == "Dockerfile"
    assert _detect_language("main.go") == "Go"
    assert _detect_language("unknown.xyz") == ""


def test_collect_stats():
    from ingestion import _extract_zip, collect_stats
    data = _make_zip({
        "main.py": "import fastapi\nx=1\n",
        "app.js": "const express = require('express');\n",
    })
    files = _extract_zip(data)
    stats = collect_stats(files)
    assert "Python" in stats["languages"]
    assert "JavaScript" in stats["languages"]
    assert "FastAPI" in stats["frameworks"]
    assert "Express.js" in stats["frameworks"]
    assert stats["file_count"] == 2


def test_ingest_zip_base64():
    import base64
    from ingestion import ingest_zip_base64
    data = _make_zip({"hello.py": "print('hi')"})
    b64 = base64.b64encode(data).decode()
    name, files = ingest_zip_base64(b64, "test_repo")
    assert name == "test_repo"
    assert any(f.path == "hello.py" for f in files)


def test_ingest_zip_base64_invalid():
    from ingestion import ingest_zip_base64
    with pytest.raises(ValueError, match="Invalid base64"):
        ingest_zip_base64("NOT_VALID_BASE64!!!", "repo")


def test_ingest_github_bad_url():
    from ingestion import ingest_github_url
    with pytest.raises(ValueError, match="Unrecognised URL"):
        ingest_github_url("https://bitbucket.org/owner/repo")


def test_ingest_github_url_downloads(monkeypatch):
    """Mock the HTTP download and verify parsing."""
    import base64
    from ingestion import ingest_github_url
    data = _make_zip({"service.py": "class Service: pass"})

    mock_resp = MagicMock()
    mock_resp.read.return_value = data
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)

    with patch("urllib.request.urlopen", return_value=mock_resp):
        name, files = ingest_github_url("https://github.com/owner/myrepo")

    assert name == "myrepo"
    assert any(f.path == "service.py" for f in files)
