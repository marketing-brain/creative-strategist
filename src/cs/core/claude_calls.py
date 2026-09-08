"""Shared helpers for building Anthropic Messages API calls consistently across
the tagging, insights, and generation modules -- all three construct a client
from a configurable `claude_model_*` setting and call `beta.messages.create`."""

from __future__ import annotations

# The server-side `fallbacks` beta param is only accepted by these models --
# every other current model (Sonnet, Haiku, older Opus/Sonnet) rejects it
# outright with a 400 ("does not support the `fallbacks` parameter"). Confirmed
# empirically: claude-haiku-4-5 rejects it for both tagging and generation copy.
# Since claude_model_tagging/insights/copilot/copy are all independently
# configurable, any of the four call sites can be pointed at a non-Opus/Fable
# model at any time -- fallback kwargs must be conditional, never hardcoded.
_FALLBACK_CAPABLE_MODELS = frozenset(
    {"claude-opus-5", "claude-opus-4-8", "claude-fable-5", "claude-fable-5-1"}
)


def fallback_kwargs(model: str) -> dict:
    """Extra kwargs to splat into `beta.messages.create(...)` for server-side
    model fallback -- empty for a model that doesn't support the param."""
    if model in _FALLBACK_CAPABLE_MODELS:
        return {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    return {}
