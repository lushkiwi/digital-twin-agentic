"""Interactive agent — the tool-using chat loop.

``run_chat`` is an async generator yielding the exact SSE event dicts from
CONTRACTS §3 (plan_text / tool_call / tool_result / assistant_text / error / done).
The stream ALWAYS ends with a ``done`` event; an ``error`` is always followed by
``done``.

Cross-provider tool-calling detail: after the model asks for tool calls we append the
assistant message *verbatim* (via ``model_dump``) and then one ``role: tool`` message
per call keyed by ``tool_call_id`` — this is what keeps Anthropic/OpenAI/OpenRouter
tool round-trips consistent under LiteLLM.
"""
from __future__ import annotations

import json
from typing import Any, AsyncGenerator, List

from ..config import settings
from . import tools
from .llm import LLMError, complete
from .prompts import SYSTEM_INTERACTIVE

MAX_HISTORY = 10


def _assistant_message_dict(msg: Any) -> dict:
    """Serialise the LiteLLM assistant message for replay, verbatim-ish.

    Drops top-level keys whose value is ``None`` (e.g. an empty ``function_call``)
    since some providers reject them on the follow-up call, but keeps ``tool_calls``
    and everything else intact.
    """
    if hasattr(msg, "model_dump"):
        d = msg.model_dump()
    elif isinstance(msg, dict):
        d = dict(msg)
    else:
        d = {
            "role": "assistant",
            "content": getattr(msg, "content", None),
            "tool_calls": getattr(msg, "tool_calls", None),
        }
    d = {k: v for k, v in d.items() if v is not None}
    d.setdefault("role", "assistant")
    return d


def _parse_args(raw: Any) -> Any:
    """Parse a tool call's ``arguments`` JSON string into a dict (best-effort)."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return {}
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return raw  # non-object -> executor returns a structured error
    return raw


async def run_chat(
    message: str, history: List[dict]
) -> AsyncGenerator[dict, None]:
    try:
        messages: List[dict] = [{"role": "system", "content": SYSTEM_INTERACTIVE}]
        for turn in (history or [])[-MAX_HISTORY:]:
            role = turn.get("role")
            content = turn.get("content")
            if role in ("user", "assistant") and isinstance(content, str):
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": message})

        max_iters = max(1, settings.max_tool_iterations)
        finished = False

        for _iteration in range(max_iters):
            resp = await complete(messages, tools=tools.tool_schemas(), max_tokens=1024)
            msg = resp.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None)
            text = getattr(msg, "content", None)

            if not tool_calls:
                yield {"type": "assistant_text", "text": text or ""}
                finished = True
                break

            # Assistant prose emitted alongside tool calls -> plan_text.
            if text and text.strip():
                yield {"type": "plan_text", "text": text}

            # Replay the assistant tool-call message verbatim before the tool results.
            messages.append(_assistant_message_dict(msg))

            for tc in tool_calls:
                tc_id = getattr(tc, "id", None)
                fn = getattr(tc, "function", None)
                name = getattr(fn, "name", None) or ""
                args = _parse_args(getattr(fn, "arguments", None))

                yield {"type": "tool_call", "id": tc_id, "name": name, "args": args}

                outcome = await tools.execute(name, args)

                yield {
                    "type": "tool_result",
                    "id": tc_id,
                    "ok": outcome.ok,
                    "result": outcome.result if outcome.ok else {"error": outcome.error},
                    "ditto_request": outcome.ditto_request,
                    "ditto_status": outcome.ditto_status,
                }

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": json.dumps(outcome.llm_content()),
                    }
                )

        if not finished:
            yield {
                "type": "assistant_text",
                "text": (
                    f"I reached the tool-step limit ({max_iters}) before fully "
                    "resolving this. Above are the steps I took — ask me to continue "
                    "if you'd like me to keep going."
                ),
            }
    except LLMError as exc:
        yield {"type": "error", "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        yield {"type": "error", "message": f"Unexpected error: {exc}"}
    finally:
        yield {"type": "done"}
