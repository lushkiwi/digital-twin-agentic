"""Application settings + runtime-mutable LLM configuration.

Settings are loaded once from the repo-root ``.env`` (via python-dotenv) using the
exact env var names from ``.env.example``.  The LLM configuration is special: it is
mutable at runtime (via ``POST /api/config``) and holds API keys that must live in
process memory only — never logged, never returned unmasked.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

from dotenv import load_dotenv
from pydantic import BaseModel

# Repo root is two levels above this file's package: backend/app/config.py -> repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]

# Load the repo-root .env once at import time.  Values already in the real
# environment win (override=False) so container/CI env vars are respected.
load_dotenv(REPO_ROOT / ".env", override=False)

# Imported AFTER load_dotenv so params.THING_NS reflects the repo-root .env.  params is
# the frozen single source of truth for component ids / thing ids (avoids duplication).
from . import params  # noqa: E402


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


class Settings(BaseModel):
    """Static process settings, materialised from env at import time."""

    ditto_base_url: str = _get("DITTO_BASE_URL", "http://localhost:8080")
    ditto_user: str = _get("DITTO_USER", "ditto")
    ditto_pass: str = _get("DITTO_PASS", "ditto")
    # V2 is a four-component chained system (CONTRACTS §0): one thing per component,
    # ids derived from the frozen param registry so bounds/ids live in exactly one place.
    thing_ns: str = params.THING_NS
    thing_ids: Dict[str, str] = {c: params.thing_id(c) for c in params.COMPONENTS}

    backend_port: int = _get_int("BACKEND_PORT", 8000)

    telemetry_interval_s: float = _get_float("TELEMETRY_INTERVAL_S", 1.0)
    sleeper_reflect_interval_s: int = _get_int("SLEEPER_REFLECT_INTERVAL_S", 60)
    max_tool_iterations: int = _get_int("MAX_TOOL_ITERATIONS", 8)

    # Frontend dev origin allowed through CORS.
    frontend_origin: str = "http://localhost:5173"


settings = Settings()


# Fixed model presets surfaced to the UI's Settings panel.
LLM_PRESETS = [
    "openrouter/anthropic/claude-sonnet-5",
    "openrouter/anthropic/claude-fable-5",
    "openrouter/openai/gpt-5.6-sol",
    "anthropic/claude-fable-5",
    "openai/gpt-5.6",
    "ollama/llama3.1",
]

# Providers that authenticate with an API key (vs. ollama which uses a base URL).
KEYED_PROVIDERS = ("openrouter", "anthropic", "openai")


def mask_key(key: Optional[str]) -> Optional[str]:
    """Mask an API key for display: first 7 chars + ``****`` (e.g. ``sk-ant-****``)."""
    if not key:
        return None
    return key[:7] + "****"


class RuntimeLLMConfig:
    """Process-memory LLM config, seeded from env, mutable via ``POST /api/config``.

    Holds a per-provider key set so switching models does not lose a previously
    configured key.  API keys are never logged and only ever surfaced masked.
    """

    def __init__(self) -> None:
        self.model: str = _get("LLM_MODEL", "openrouter/anthropic/claude-sonnet-5")
        # Per-provider key slots, seeded from env, mutable via POST /api/config.
        self._keys: dict[str, Optional[str]] = {
            "openrouter": _get("OPENROUTER_API_KEY") or None,
            "anthropic": _get("ANTHROPIC_API_KEY") or None,
            "openai": _get("OPENAI_API_KEY") or None,
        }
        self._ollama_base_url: str = _get("OLLAMA_BASE_URL", "http://localhost:11434")
        # Optional generic base_url override set via the API (applies to the active model).
        self._base_url_override: Optional[str] = None

    # ---- provider derivation -------------------------------------------------
    @property
    def provider(self) -> str:
        # First path segment: e.g. openrouter/anthropic/claude-sonnet-5 -> "openrouter".
        return self.model.split("/", 1)[0] if "/" in self.model else self.model

    def active_key(self) -> Optional[str]:
        """The API key that applies to the currently selected model's provider."""
        return self._keys.get(self.provider)  # ollama/unknown -> None

    def active_base_url(self) -> Optional[str]:
        """The base URL that applies to the current model (ollama uses its base by default)."""
        if self._base_url_override:
            return self._base_url_override
        if self.provider == "ollama":
            return self._ollama_base_url
        return None

    def has_key(self) -> bool:
        """True when the active model can actually make a call (key present, or ollama)."""
        if self.provider == "ollama":
            return bool(self.active_base_url())
        return bool(self.active_key())

    # ---- mutation ------------------------------------------------------------
    def update(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        """Apply an override.  Empty/None api_key keeps the existing key."""
        if model:
            self.model = model
        # Only overwrite the key when a non-empty one is supplied; store it under the
        # (possibly newly-selected) model's provider slot. Keyless providers ignore it.
        if api_key and self.provider != "ollama":
            self._keys[self.provider] = api_key
        if base_url is not None:
            base_url = base_url.strip()
            if self.provider == "ollama":
                # For ollama, base_url configures the server endpoint.
                self._ollama_base_url = base_url or self._ollama_base_url
                self._base_url_override = None
            else:
                self._base_url_override = base_url or None

    # ---- serialisation -------------------------------------------------------
    def public_dict(self) -> dict:
        return {
            "model": self.model,
            "api_key_masked": mask_key(self.active_key()),
            "base_url": self.active_base_url(),
            "presets": LLM_PRESETS,
        }


# Module-level singleton.
llm_config = RuntimeLLMConfig()
