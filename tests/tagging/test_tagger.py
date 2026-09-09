import json
from types import SimpleNamespace

from cs.tagging.prompts import SYSTEM, prompt_sha, user_text
from cs.tagging.tagger import GeminiGatewayTagger
from cs.tagging.taxonomy import load_taxonomy


class _FakeCompletions:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _client(response):
    return SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions(response)))


def _response(text, finish_reason="stop", refusal=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(content=text, refusal=refusal),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        model="google/gemini-2.5-flash-lite",
    )


async def test_gemini_tagger_sends_images_and_parses_json(settings):
    payload = {
        "tags": [
            {"dimension": "hook_type", "value": "question", "confidence": 0.9, "evidence": "e"}
        ]
    }
    client = _client(_response(json.dumps(payload)))
    tagger = GeminiGatewayTagger(settings, client=client)
    schema = load_taxonomy().json_schema("image")
    res = await tagger.tag([(b"\xff\xd8\xff", "image/jpeg")], "describe", schema)
    assert res.tags == payload["tags"] and res.input_tokens == 10 and not res.refused
    call = client.chat.completions.calls[0]
    assert call["model"] == "google/gemini-2.5-flash-lite"
    assert call["response_format"]["json_schema"]["schema"] == schema
    content = call["messages"][-1]["content"]
    assert content[0]["type"] == "image_url" and content[0]["image_url"]["url"].startswith(
        "data:image/jpeg;base64,"
    )
    assert content[-1]["type"] == "text"


async def test_gemini_tagger_handles_refusal(settings):
    client = _client(_response(None, finish_reason="content_filter", refusal="blocked"))
    res = await GeminiGatewayTagger(settings, client=client).tag([], "x", {"type": "object"})
    assert res.refused and res.tags == []


def test_prompt_builders():
    t = load_taxonomy()
    text = user_text(t, "video", {"headline": "H", "primary_text": "P"}, "hello world", [0.0, 1.0])
    assert "pacing" in text and "hello world" in text and "t=1.0s" in text
    assert len(prompt_sha(SYSTEM, text, {"a": 1})) == 64
