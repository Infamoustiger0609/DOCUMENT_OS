"""Signature stamping for the self-serve e-signature feature (Phase 31 — see
CLAUDE.md's E-signature section). Uses PyMuPDF (already a dependency — see
extraction.py/pdf_tools.py) rather than adding pikepdf's low-level object API
or a new dependency like reportlab: `Page.insert_image()` already handles
placing an arbitrary image (including a transparent PNG, via the PDF's own
soft-mask support) at a precise rectangle in one call.
"""

import io

import pymupdf as fitz
from PIL import Image

# v1 scope (see CLAUDE.md): a fixed placement, not a user-chosen page/x/y —
# always the bottom-right corner of the LAST page. A configurable position is
# an explicitly noted future refinement, not missing functionality.
SIGNATURE_TARGET_WIDTH_PT = 160.0
SIGNATURE_MARGIN_PT = 36.0
SIGNATURE_MIN_WIDTH_PT = 20.0


def stamp_signature(pdf_bytes: bytes, signature_image_bytes: bytes) -> bytes:
    """Overlays `signature_image_bytes` onto the bottom-right corner of the
    last page of `pdf_bytes`, preserving the signature image's own aspect
    ratio scaled to a fixed target width (clamped to fit a narrow page).
    Returns the new PDF's bytes — the original is never modified in place,
    matching every other /tools/*-style transformation in this app."""
    with Image.open(io.BytesIO(signature_image_bytes)) as img:
        img_width, img_height = img.size
    aspect = (img_height / img_width) if img_width else 1.0

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc[-1]
        page_rect = page.rect
        width = min(
            SIGNATURE_TARGET_WIDTH_PT,
            max(SIGNATURE_MIN_WIDTH_PT, page_rect.width - 2 * SIGNATURE_MARGIN_PT),
        )
        height = width * aspect

        x1 = page_rect.width - SIGNATURE_MARGIN_PT
        y1 = page_rect.height - SIGNATURE_MARGIN_PT
        rect = fitz.Rect(x1 - width, y1 - height, x1, y1)

        page.insert_image(rect, stream=signature_image_bytes)
        return doc.tobytes()
    finally:
        doc.close()
