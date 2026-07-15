"""
Tests for the ingestion module — no network calls.
"""
import io
import sys
import tempfile
import zipfile
from pathlib import Path

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


def test_ingest_folder_basic():
    from ingestion import ingest_folder
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "app.py").write_text("print('hello')", encoding="utf-8")
        (root / "README.md").write_text("# Hello", encoding="utf-8")
        name, files = ingest_folder(tmp, "my_project")
    assert name == "my_project"
    paths = [f.path for f in files]
    assert "app.py" in paths
    assert "README.md" in paths


def test_ingest_folder_skips_pycache():
    from ingestion import ingest_folder
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "main.py").write_text("x = 1", encoding="utf-8")
        pycache = root / "__pycache__"
        pycache.mkdir()
        (pycache / "main.cpython-311.pyc").write_bytes(b"binary")
        _, files = ingest_folder(tmp)
    assert all("__pycache__" not in f.path for f in files)


def test_ingest_folder_uses_dirname_as_default_name():
    from ingestion import ingest_folder
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "app.py").write_text("x=1", encoding="utf-8")
        name, _ = ingest_folder(tmp)
    assert name == root.name


def test_ingest_folder_not_found():
    from ingestion import ingest_folder
    with pytest.raises(ValueError, match="Folder not found"):
        ingest_folder("/nonexistent/path/that/does/not/exist")


def test_ingest_folder_not_a_directory():
    from ingestion import ingest_folder
    with tempfile.NamedTemporaryFile(suffix=".py") as f:
        with pytest.raises(ValueError, match="not a directory"):
            ingest_folder(f.name)
