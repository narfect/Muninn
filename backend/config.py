"""Environment-driven configuration.

Reads settings from process environment and an optional ``.env`` file in the
project root. No third-party dependency (a tiny ``.env`` parser is included) so
the core runs with only the Python standard library.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Project root = two levels up from this file (backend/config.py -> project root)
ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal ``.env`` loader: ``KEY=VALUE`` lines, ``#`` comments, optional quotes.

    Existing environment variables always win, so real secrets set in the shell are
    never overridden by a committed example file.
    """
    if not path.exists():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass


_load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Immutable application settings resolved at import time."""

    # --- server ---
    host: str = field(default_factory=lambda: _env("MUNINN_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("MUNINN_PORT", "8000") or 8000))
    db_path: str = field(
        default_factory=lambda: _env("MUNINN_DB", str(ROOT / "data" / "muninn.db"))
    )

    # --- Hindsight (memory) ---
    hindsight_base_url: str = field(
        default_factory=lambda: _env("HINDSIGHT_BASE_URL", "")
    )
    hindsight_api_key: str = field(
        default_factory=lambda: _env("HINDSIGHT_API_KEY", "")
    )
    hindsight_namespace: str = field(
        default_factory=lambda: _env("HINDSIGHT_NAMESPACE", "default")
    )
    hindsight_bank: str = field(
        default_factory=lambda: _env("HINDSIGHT_BANK", "muninn-incidents")
    )
    # auto | hindsight | local
    memory_backend: str = field(
        default_factory=lambda: _env("MUNINN_MEMORY_BACKEND", "auto").lower()
    )

    # --- LLM (reasoning) ---
    groq_api_key: str = field(default_factory=lambda: _env("GROQ_API_KEY", ""))
    groq_base_url: str = field(
        default_factory=lambda: _env("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    )
    groq_model: str = field(
        default_factory=lambda: _env("GROQ_MODEL", "openai/gpt-oss-120b")
    )
    # auto | groq | local
    llm_backend: str = field(
        default_factory=lambda: _env("MUNINN_LLM_BACKEND", "auto").lower()
    )

    # --- behaviour ---
    request_timeout: float = field(
        default_factory=lambda: float(_env("MUNINN_HTTP_TIMEOUT", "30") or 30)
    )
    recall_top_k: int = field(
        default_factory=lambda: int(_env("MUNINN_RECALL_TOP_K", "5") or 5)
    )
    # --- transport hardening (S9) ---
    # Reject request bodies larger than this (bytes) with HTTP 413 before reading them,
    # so a bogus Content-Length can't exhaust memory. Default 1 MiB.
    max_body_bytes: int = field(
        default_factory=lambda: int(_env("MUNINN_MAX_BODY_BYTES", "1048576") or 1048576)
    )

    def resolved_memory_backend(self) -> str:
        """Decide which memory backend will actually be used."""
        if self.memory_backend == "hindsight":
            return "hindsight"
        if self.memory_backend == "local":
            return "local"
        # auto
        if self.hindsight_base_url and self.hindsight_api_key:
            return "hindsight"
        return "local"

    def resolved_llm_backend(self) -> str:
        """Decide which reasoning backend will actually be used."""
        if self.llm_backend == "groq":
            return "groq"
        if self.llm_backend == "local":
            return "local"
        # auto
        return "groq" if self.groq_api_key else "local"


settings = Settings()
