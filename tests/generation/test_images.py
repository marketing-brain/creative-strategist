import base64
from types import SimpleNamespace

from cs.generation.fakes import solid_png
from cs.generation.images import GatewayImageGenerator


async def test_generate_without_references_uses_generations(settings):
    calls = {}

    class Images:
        async def generate(self, **kw):
            calls["generate"] = kw
            return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(solid_png(64, 64)).decode())])

        async def edit(self, **kw):
            calls["edit"] = kw
            return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(solid_png(64, 64)).decode())])

    gen = GatewayImageGenerator(settings, client=SimpleNamespace(images=Images()))
    out = await gen.generate("a bottle", [], n=1)
    assert out[0][:8] == b"\x89PNG\r\n\x1a\n" and calls["generate"]["model"] == settings.image_model
    out = await gen.generate("a bottle", [solid_png(32, 32)], n=1)
    assert "edit" in calls and calls["edit"]["prompt"] == "a bottle"
