"""LiteLLM shim — the single choke point for model calls (non-streaming only).

Streaming is deliberately avoided: token / tool-call-delta streaming is where
cross-provider bugs live, and the plan calls for whole-response completions.
``litellm.drop_params`` is on so provider-unsupported params are silently dropped
instead of erroring.
"""
from __future__ import annotations

from typing import Any, List, Optional

import litellm

from ..config import llm_config

# Drop params a given provider doesn't support instead of raising (cross-provider compat).
litellm.drop_params = True
# Keep LiteLLM quiet; never let it print keys or verbose payloads.
litellm.suppress_debug_info = True


class LLMError(Exception):
    """Raised with a human-readable message when a model call cannot be made."""


async def complete(
    messages: List[dict],
    tools: Optional[List[dict]] = None,
    max_tokens: int = 1024,
) -> Any:
    """Run a non-streaming completion using the current RuntimeLLMConfig.

    Maps the model's provider prefix to the right credential/base:
      openrouter/* -> OPENROUTER key, anthropic/* -> ANTHROPIC key,
      openai/* -> OPENAI key, ollama/* -> api_base.
    LiteLLM natively understands the ``openrouter/`` prefix, so we just hand it the key.
    Raises :class:`LLMError` on any failure (missing key, provider error, timeout).
    """
    cfg = llm_config
    provider = cfg.provider

    kwargs: dict = {
        "model": cfg.model,
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    if provider in ("openrouter", "anthropic", "openai"):
        key = cfg.active_key()
        if not key:
            raise LLMError(
                f"No {provider} API key configured. Set one in Settings."
            )
        kwargs["api_key"] = key
    elif provider == "ollama":
        base = cfg.active_base_url()
        if not base:
            raise LLMError("No Ollama base URL configured. Set one in Settings.")
        kwargs["api_base"] = base
    else:
        # Unknown provider: pass a key through if we happen to have one.
        key = cfg.active_key()
        if key:
            kwargs["api_key"] = key

    try:
        return await litellm.acompletion(**kwargs)
    except LLMError:
        raise
    except Exception as exc:  # noqa: BLE001 — normalise every provider error
        raise LLMError(f"LLM call failed: {exc}") from exc
