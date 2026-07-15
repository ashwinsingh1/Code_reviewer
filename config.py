"""
Configuration loader — reads from environment variables / .env file.
All settings have sensible defaults; only ANTHROPIC_API_KEY is required.
"""
from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root (one level up from this file)
load_dotenv(Path(__file__).parent / ".env")


def _require(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if not val:
        raise RuntimeError(
            f"Missing required environment variable: {key}\n"
            f"Create a .env file in the code_reviewer/ folder and set {key}=<your key>"
        )
    return val


class Settings:
    # ── Anthropic ──────────────────────────────────────────────
    ANTHROPIC_API_KEY: str = ""          # loaded lazily on first use
    CLAUDE_MODEL:      str = os.environ.get("CLAUDE_MODEL", "claude-3-5-sonnet-20241022")
    CLAUDE_MAX_TOKENS: int = int(os.environ.get("CLAUDE_MAX_TOKENS", "8000"))

    # ── Server ─────────────────────────────────────────────────
    HOST: str = os.environ.get("HOST", "127.0.0.1")
    PORT: int = int(os.environ.get("PORT", "7000"))

    # ── Review limits ──────────────────────────────────────────
    # Max characters of source code sent to Claude per review batch
    MAX_CODE_CHARS: int = int(os.environ.get("MAX_CODE_CHARS", "80000"))
    # Max number of files included in a single review
    MAX_FILES:      int = int(os.environ.get("MAX_FILES", "60"))
    # Max individual file size included (bytes)
    MAX_FILE_BYTES: int = int(os.environ.get("MAX_FILE_BYTES", "100000"))

    # ── GitHub ─────────────────────────────────────────────────
    # Optional — only needed for private repos
    GITHUB_TOKEN: str = os.environ.get("GITHUB_TOKEN", "")

    def get_api_key(self) -> str:
        if not self.ANTHROPIC_API_KEY:
            self.ANTHROPIC_API_KEY = _require("ANTHROPIC_API_KEY")
        return self.ANTHROPIC_API_KEY


settings = Settings()
