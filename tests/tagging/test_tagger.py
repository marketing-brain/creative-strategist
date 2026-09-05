import json
from types import SimpleNamespace

from cs.tagging.prompts import SYSTEM, prompt_sha, user_text
from cs.tagging.tagger import ClaudeTagger
from cs.tagging.taxonomy import load_taxonomy


class _FakeBetaMessages:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _client(response):
    return SimpleNamespace(beta=SimpleNamespace(messages=_FakeBetaMessages(response)))


def _response(text, stop="end_turn"):
    return SimpleNamespace(
        stop_reason=stop,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        model="claude-opus-5",
    )


async def test_claude_tagger_sends_images_and_parses_json(settings):
    payload = {
        "tags": [
            {"dimension": "hook_type", "value": "question", "confidence": 0.9, "evidence": "e"}
        ]
    }
    client = _client(_response(json.dumps(payload)))
    tagger = ClaudeTagger(settings, client=client)
    schema = load_taxonomy().json_schema("image")
    res = await tagger.tag([(b"\xff\xd8\xff", "image/jpeg")], "describe", schema)
    assert res.tags == payload["tags"] and res.input_tokens == 10 and not res.refused
    call = client.beta.messages.calls[0]
    assert call["model"] == "claude-opus-5"
    assert call["betas"] == ["server-side-fallback-2026-07-01"] and call["fallbacks"] == "default"
    assert call["output_config"]["format"]["type"] == "json_schema"
    content = call["messages"][0]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/jpeg"
    assert content[-1]["type"] == "text"


async def test_claude_tagger_handles_refusal(settings):
    client = _client(_response("", stop="refusal"))
    res = await ClaudeTagger(settings, client=client).tag([], "x", {"type": "object"})
    assert res.refused and res.tags == []


def test_prompt_builders():
    t = load_taxonomy()
    text = user_text(t, "video", {"headline": "H", "primary_text": "P"}, "hello world", [0.0, 1.0])
    assert "pacing" in text and "hello world" in text and "t=1.0s" in text
    assert len(prompt_sha(SYSTEM, text, {"a": 1})) == 64
