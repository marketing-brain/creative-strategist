from cs.core.claude_calls import fallback_kwargs


def test_fallback_kwargs_for_supporting_model():
    kw = fallback_kwargs("claude-opus-5")
    assert kw == {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}


def test_fallback_kwargs_empty_for_unsupported_model():
    """Regression test: claude-haiku-4-5 rejects the `fallbacks` beta param outright
    with a 400 ('does not support the `fallbacks` parameter'), confirmed live against
    both the tagging and generation-copy call sites. claude_model_tagging/insights/
    copilot/copy are all independently configurable, so any call site can be pointed
    at a non-Opus/Fable model -- fallback kwargs must never be sent unconditionally."""
    assert fallback_kwargs("claude-haiku-4-5") == {}
    assert fallback_kwargs("claude-sonnet-5") == {}
