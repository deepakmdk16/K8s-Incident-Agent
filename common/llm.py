"""Minimal Anthropic Messages wrapper shared by both eval arms.

One call shape, pinned model, no sampling parameters: `claude-opus-5` removed
`temperature`/`top_p`/`top_k` (a request carrying them is rejected with 400),
so run-to-run determinism is addressed by replicate runs in the harness, not by
a sampling knob. Every result carries exact token counts so cost per case is
reported from measurement, never estimated.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import anthropic
from anthropic.types import ContentBlock, MessageParam, ToolUnionParam

PINNED_MODEL = "claude-opus-5"

# USD per 1M tokens, Anthropic first-party rates (2026-06). Keyed by model so an
# unknown override yields None rather than a silently wrong cost figure.
_PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.00, 25.00),
}


# Cached input is billed off the input rate: a write costs 1.25x, a read 0.1x.
# `usage.input_tokens` counts only the UNCACHED remainder, so the three add up.
_CACHE_WRITE_MULTIPLIER = 1.25
_CACHE_READ_MULTIPLIER = 0.10


def _cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> float | None:
    """USD for one call, or None for an unpriced model (never a wrong number)."""
    prices = _PRICES_PER_MTOK.get(model)
    if prices is None:
        return None
    price_in, price_out = prices
    billed_input = (
        input_tokens
        + cache_write_tokens * _CACHE_WRITE_MULTIPLIER
        + cache_read_tokens * _CACHE_READ_MULTIPLIER
    )
    return (billed_input * price_in + output_tokens * price_out) / 1_000_000


@dataclass(frozen=True)
class LLMResult:
    """One completed model call with its measured usage."""

    text: str
    model: str
    input_tokens: int
    output_tokens: int
    stop_reason: str

    @property
    def cost_usd(self) -> float | None:
        return _cost_usd(self.model, self.input_tokens, self.output_tokens)


CompleteFn = Callable[[str], LLMResult]


def load_env_file(path: Path = Path(".env")) -> None:
    """Export KEY=VALUE lines from a .env file without overriding existing env."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.split("#", 1)[0].strip().strip("'\"")
        if key and value and key not in os.environ:
            os.environ[key] = value


def _client() -> anthropic.Anthropic:
    """Shared client. Identity-linked API keys require the workspace id on every
    request; a standard workspace key works with this unset (see .env.example)."""
    workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID", "")
    return anthropic.Anthropic(
        default_headers={"anthropic-workspace-id": workspace_id} if workspace_id else None
    )


def complete(prompt: str, *, model: str = PINNED_MODEL, max_tokens: int = 16000) -> LLMResult:
    """One user-turn Messages call. Raises on any non-natural stop; never truncates silently."""
    response = _client().messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason != "end_turn":
        raise RuntimeError(
            f"model stopped abnormally: stop_reason={response.stop_reason!r} "
            f"(model={response.model}, output_tokens={response.usage.output_tokens})"
        )
    text = "".join(block.text for block in response.content if block.type == "text")
    if not text.strip():
        raise RuntimeError("model returned no text content")
    return LLMResult(
        text=text,
        model=response.model,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        stop_reason=response.stop_reason,
    )


@dataclass(frozen=True)
class ToolSpec:
    """One tool offered to the model, in Anthropic tool-definition shape."""

    name: str
    description: str
    input_schema: dict[str, object]


@dataclass(frozen=True)
class ToolCall:
    """One `tool_use` block the model emitted, with its arguments already parsed."""

    id: str
    name: str
    arguments: dict[str, object]


@dataclass(frozen=True)
class Turn:
    """One assistant turn of a tool-use conversation, with its measured usage.

    `assistant_content` is the response's content blocks verbatim; the caller
    appends them back unchanged (thinking blocks included — the pinned model
    runs adaptive thinking, and dropping those blocks breaks continuation).
    """

    text: str
    tool_calls: tuple[ToolCall, ...]
    assistant_content: list[ContentBlock]
    stop_reason: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_write_tokens: int
    cache_read_tokens: int

    @property
    def cost_usd(self) -> float | None:
        return _cost_usd(
            self.model,
            self.input_tokens,
            self.output_tokens,
            self.cache_write_tokens,
            self.cache_read_tokens,
        )


ConverseFn = Callable[[Sequence[MessageParam], str, Sequence[ToolSpec]], Turn]


def converse(
    messages: Sequence[MessageParam],
    system: str,
    tools: Sequence[ToolSpec],
    *,
    model: str = PINNED_MODEL,
    max_tokens: int = 16000,
) -> Turn:
    """One assistant turn with tools available. Raises on any unusable stop.

    Top-level `cache_control` marks the last cacheable block of the request, so
    each turn re-reads the whole preceding conversation from cache instead of
    paying full input price for it; `cache_read_tokens` on the result is the
    measured proof that it hit.
    """
    response = _client().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system}],
        tools=[
            cast(
                ToolUnionParam,
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                },
            )
            for tool in tools
        ],
        messages=list(messages),
        cache_control={"type": "ephemeral"},
    )
    if response.stop_reason not in ("end_turn", "tool_use"):
        raise RuntimeError(
            f"model stopped abnormally: stop_reason={response.stop_reason!r} "
            f"(model={response.model}, output_tokens={response.usage.output_tokens})"
        )
    text = "".join(block.text for block in response.content if block.type == "text")
    if response.stop_reason == "end_turn" and not text.strip():
        raise RuntimeError("model ended its turn with no text content")

    calls = tuple(
        ToolCall(id=block.id, name=block.name, arguments=block.input)
        for block in response.content
        if block.type == "tool_use"
    )

    usage = response.usage
    return Turn(
        text=text,
        tool_calls=calls,
        assistant_content=response.content,
        stop_reason=response.stop_reason,
        model=response.model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_write_tokens=usage.cache_creation_input_tokens or 0,
        cache_read_tokens=usage.cache_read_input_tokens or 0,
    )
