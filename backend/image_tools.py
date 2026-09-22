"""Image conversion/resize for the /tools/* endpoints, via Pillow. See
CLAUDE.md's Document tools section."""

import io
from typing import Optional

from PIL import Image

PILLOW_FORMAT_BY_EXT = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "tiff": "TIFF", "tif": "TIFF"}


def convert_image(data: bytes, target_format: str) -> bytes:
    image = Image.open(io.BytesIO(data))
    pillow_format = PILLOW_FORMAT_BY_EXT[target_format]
    if pillow_format == "JPEG" and image.mode in ("RGBA", "P"):
        # JPEG has no alpha channel; a straight save() of an RGBA/palette
        # image in JPEG format raises OSError from Pillow.
        image = image.convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format=pillow_format)
    return buffer.getvalue()


def resize_image(
    data: bytes,
    extension: str,
    width: Optional[int] = None,
    height: Optional[int] = None,
    target_size_kb: Optional[int] = None,
) -> bytes:
    """Resizes by explicit dimensions (aspect ratio preserved if only one of
    width/height is given), by target file size, or both (dimensions applied
    first, then quality reduced to hit the target). target_size_kb is only
    reliably honorable for JPEG — PNG/TIFF are lossless formats with no
    equivalent "quality" knob, so for those it's a best-effort no-op beyond
    whatever dimension resize was also requested."""
    image = Image.open(io.BytesIO(data))
    pillow_format = PILLOW_FORMAT_BY_EXT.get(extension.lstrip("."), image.format or "JPEG")

    if width or height:
        orig_w, orig_h = image.size
        if width and not height:
            height = round(orig_h * (width / orig_w))
        elif height and not width:
            width = round(orig_w * (height / orig_h))
        image = image.resize((width, height), Image.LANCZOS)

    if pillow_format == "JPEG" and image.mode in ("RGBA", "P"):
        image = image.convert("RGB")

    if target_size_kb and pillow_format == "JPEG":
        target_bytes = target_size_kb * 1024
        buffer = io.BytesIO()
        for quality in range(95, 9, -10):
            buffer = io.BytesIO()
            image.save(buffer, format=pillow_format, quality=quality)
            if buffer.tell() <= target_bytes:
                break
        return buffer.getvalue()

    buffer = io.BytesIO()
    image.save(buffer, format=pillow_format)
    return buffer.getvalue()
