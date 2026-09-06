import httpx
import respx

from cs.connectors.meta.client import GraphClient
from cs.connectors.meta.connector import MetaConnector

G = "https://graph.facebook.com/v21.0"


@respx.mock
async def test_upload_and_create_ad(settings):
    respx.post(f"{G}/act_1/adimages").mock(
        return_value=httpx.Response(200, json={"images": {"a.jpg": {"hash": "h1"}}})
    )
    respx.post(f"{G}/act_1/adcreatives").mock(return_value=httpx.Response(200, json={"id": "cr9"}))
    respx.post(f"{G}/act_1/ads").mock(return_value=httpx.Response(200, json={"id": "ad9"}))
    respx.get(f"{G}/act_1/campaigns").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": "c1", "name": "C", "status": "ACTIVE"}]}
        )
    )
    async with httpx.AsyncClient() as http:
        mc = MetaConnector(GraphClient(settings, "tok", http))
        assert await mc.upload_image("1", b"jpg", "a.jpg") == "h1"
        assert await mc.create_ad("1", "as1", "My ad", {"page_id": "p"}, "PAUSED") == ("cr9", "ad9")
        assert (await mc.list_campaigns("1"))[0]["id"] == "c1"
