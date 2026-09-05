import io

from PIL import Image

from cs.tagging.images import prepare_image


def _png(w, h):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(buf, format="PNG")
    return buf.getvalue()


def test_prepare_resizes_and_encodes_jpeg():
    data, w, h = prepare_image(_png(4000, 2000))
    assert (w, h) == (1568, 784)
    assert data[:3] == b"\xff\xd8\xff"


def test_prepare_keeps_small_images():
    data, w, h = prepare_image(_png(300, 300))
    assert (w, h) == (300, 300)
