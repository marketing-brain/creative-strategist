import io

from PIL import Image


def prepare_image(data: bytes, max_side: int = 1568) -> tuple[bytes, int, int]:
    im = Image.open(io.BytesIO(data))
    im = im.convert("RGB")
    w, h = im.size
    scale = max(w, h) / max_side
    if scale > 1:
        im = im.resize((round(w / scale), round(h / scale)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85, optimize=True)
    return buf.getvalue(), im.size[0], im.size[1]
