import io

import pymupdf as fitz  # PyMuPDF's `fitz` import name is deprecated in favor of `pymupdf`
import pytesseract
from PIL import Image

from config import TESSERACT_CMD

if TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

# Below this many characters, treat a PDF's embedded text layer as absent
# and fall back to OCR (covers pages that are just a scanned image with no real text).
TEXT_LAYER_MIN_CHARS = 20


def extract_text_from_pdf_via_ocr(file_bytes: bytes) -> str:
    text_parts = []
    with fitz.open(stream=file_bytes, filetype="pdf") as pdf:
        for page in pdf:
            pixmap = page.get_pixmap(dpi=300)
            image = Image.open(io.BytesIO(pixmap.tobytes("png")))
            text_parts.append(pytesseract.image_to_string(image))
    return "\n".join(text_parts).strip()


def extract_text_from_pdf(file_bytes: bytes) -> str:
    with fitz.open(stream=file_bytes, filetype="pdf") as pdf:
        text = "\n".join(page.get_text() for page in pdf).strip()

    if len(text) >= TEXT_LAYER_MIN_CHARS:
        return text
    return extract_text_from_pdf_via_ocr(file_bytes)


def extract_text_from_image(file_bytes: bytes) -> str:
    image = Image.open(io.BytesIO(file_bytes))
    return pytesseract.image_to_string(image).strip()


def extract_text(file_bytes: bytes, extension: str) -> str:
    if extension == ".pdf":
        return extract_text_from_pdf(file_bytes)
    return extract_text_from_image(file_bytes)
