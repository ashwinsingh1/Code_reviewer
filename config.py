"""
Configuration loader — reads from environment variables / .env file.
"""
from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root (one level up from this file)
load_dotenv(Path(__file__).parent / ".env")


class Settings:
    # ── Server ─────────────────────────────────────────────────
    HOST: str = os.environ.get("HOST", "127.0.0.1")
    PORT: int = int(os.environ.get("PORT", "7000"))

    # ── Review limits ──────────────────────────────────────────
    MAX_FILES:      int = int(os.environ.get("MAX_FILES", "60"))
    MAX_FILE_BYTES: int = int(os.environ.get("MAX_FILE_BYTES", "100000"))

    # ── LLM providers (all optional) ───────────────────────────
    # Groq — free API key at https://console.groq.com/keys
    GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")


settings = Settings()
