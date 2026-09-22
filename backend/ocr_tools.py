"""OCR-to-searchable-PDF for POST /tools/ocr-pdf. Deliberately NOT the same
code path as extraction.py's extract_text_from_pdf_via_ocr — that function
returns plain text for the document-intelligence pipeline (classification/
structured extraction); this tool's whole job is to return a PDF with an
invisible OCR text layer overlaid on the original page images, which is a
different output shape entirely. Both ultimately drive the same underlying
Tesseract binary (config.py's TESSERACT_CMD applies to both), so nothing about
the OCR engine itself is duplicated — only the output format differs, which is
exactly why this uses ocrmypdf (purpose-built for text-layer embedding) rather
than hand-rolling PDF page + invisible text overlay via PyMuPDF."""

import tempfile
from pathlib import Path

import ocrmypdf


def ocr_pdf(data: bytes, force_ocr: bool = False) -> bytes:
    """skip_text=True (the default here) leaves any page that already has a
    text layer untouched and OCRs only the image-only pages, succeeding either
    way — this tool is "make my scan searchable", not "re-OCR everything", so
    a PDF that's already fully text-layered (or a mix of born-digital and
    scanned pages) is a normal, successful no-op/partial-op, never an error.
    Verified directly: calling this twice in a row with force_ocr=False on the
    same file does NOT raise ocrmypdf's PriorOcrFoundError (that only fires
    when neither skip_text nor force_ocr is set, which this function never
    does). Pass force_ocr=True to re-OCR every page regardless, replacing any
    existing text layer (e.g. for a bad prior OCR pass)."""
    with tempfile.TemporaryDirectory() as tmp:
        input_path = Path(tmp) / "input.pdf"
        output_path = Path(tmp) / "output.pdf"
        input_path.write_bytes(data)

        ocrmypdf.ocr(
            input_path,
            output_path,
            force_ocr=force_ocr,
            skip_text=not force_ocr,
            progress_bar=False,
        )
        return output_path.read_bytes()
