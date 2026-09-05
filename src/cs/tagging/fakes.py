from __future__ import annotations

from cs.tagging.tagger import TagResult

DEFAULT_TAGS = [
    {
        "dimension": "hook_type",
        "value": "question",
        "confidence": 0.9,
        "evidence": "opens with a question",
    },
    {
        "dimension": "message_angle",
        "value": "pain_relief",
        "confidence": 0.8,
        "evidence": "mentions tiredness",
    },
    {"dimension": "visual_style", "value": "ugc", "confidence": 0.7, "evidence": "handheld look"},
    {
        "dimension": "hook_summary",
        "value": "Asks if you are tired of waking up tired",
        "confidence": 0.9,
        "evidence": "headline",
    },
]


class FakeTagger:
    def __init__(self, tags_by_call: list[list[dict]] | None = None, refuse: bool = False):
        self.tags_by_call = list(tags_by_call or [])
        self.refuse = refuse
        self.calls: list[dict] = []

    async def tag(self, images, user_text, schema) -> TagResult:
        self.calls.append({"n_images": len(images), "user_text": user_text, "schema": schema})
        if self.refuse:
            return TagResult(model="fake", refused=True)
        tags = self.tags_by_call.pop(0) if self.tags_by_call else DEFAULT_TAGS
        return TagResult(tags=list(tags), input_tokens=100, output_tokens=50, model="fake")


class FakeTranscriber:
    def __init__(self, text: str = "hello world"):
        self.text = text
        self.calls = 0

    async def transcribe(self, audio: bytes, media_type: str) -> str:
        self.calls += 1
        return self.text


class FakeEmbedder:
    dim = 1536

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for i, ch in enumerate(t.encode()[: self.dim]):
                v[i] = (ch % 97) / 97.0
            out.append(v)
        return out


class FakeBatchTagger:
    def __init__(self):
        self.submitted: dict[str, list[dict]] = {}
        self.ended: set[str] = set()

    async def submit(self, requests: list[dict]) -> str:
        bid = f"batch_{len(self.submitted) + 1}"
        self.submitted[bid] = requests
        return bid

    async def poll(self, batch_id: str) -> tuple[bool, list[dict]]:
        if batch_id not in self.ended:
            return False, []
        return True, [
            {
                "custom_id": r["custom_id"],
                "ok": True,
                "tags": DEFAULT_TAGS,
                "input_tokens": 1,
                "output_tokens": 1,
            }
            for r in self.submitted[batch_id]
        ]
