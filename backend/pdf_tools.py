"""PDF-only transformations for the /tools/* endpoints: merge, split, compress
(via Ghostscript), and PDF->DOCX (via pdf2docx). No classification, no Groq
call, no raw_text extraction — pure file transformation. See CLAUDE.md's
Document tools section."""

import io
import os
import subprocess
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import List, Optional, Tuple

import pikepdf

from config import GHOSTSCRIPT_CMD

# Ghostscript's own PDFSETTINGS presets, from smallest/lowest-fidelity to
# largest/highest — trades image resolution (72/150/300 dpi) for file size.
GHOSTSCRIPT_QUALITY_PRESETS = {
    "low": "/screen",
    "medium": "/ebook",
    "high": "/printer",
}


def merge_pdfs(pdf_byte_list: List[bytes]) -> bytes:
    """Concatenates PDFs in the given order into one. Every source PDF must
    stay open (via ExitStack) until the merged output is saved — pikepdf
    copies pages by reference into the new Pdf, so closing a source early
    would invalidate pages already queued from it."""
    with ExitStack() as stack:
        output = pikepdf.Pdf.new()
        for data in pdf_byte_list:
            src = stack.enter_context(pikepdf.open(io.BytesIO(data)))
            output.pages.extend(src.pages)
        buffer = io.BytesIO()
        output.save(buffer)
        output.close()
        return buffer.getvalue()


def _parse_page_ranges(ranges: str, page_count: int) -> List[Tuple[int, int]]:
    """'1-3,5,7-9' -> [(0,2),(4,4),(6,8)], zero-indexed and inclusive. Raises
    ValueError (turned into a 400 by the router) on anything out of bounds."""
    groups: List[Tuple[int, int]] = []
    for chunk in ranges.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start_s, end_s = chunk.split("-", 1)
            start, end = int(start_s), int(end_s)
        else:
            start = end = int(chunk)
        if start < 1 or end > page_count or start > end:
            raise ValueError(f"Invalid page range '{chunk}' for a {page_count}-page PDF.")
        groups.append((start - 1, end - 1))
    if not groups:
        raise ValueError("No valid page ranges provided.")
    return groups


def split_pdf(data: bytes, ranges: Optional[str]) -> List[Tuple[str, bytes]]:
    """Splits one PDF into several. With no `ranges`, splits into one PDF per
    page. With `ranges` (e.g. "1-3,5,7-9"), produces one output PDF per
    comma-separated group. Returns [(filename, pdf_bytes), ...]."""
    results: List[Tuple[str, bytes]] = []
    with pikepdf.open(io.BytesIO(data)) as src:
        page_count = len(src.pages)
        page_groups = _parse_page_ranges(ranges, page_count) if ranges else [
            (i, i) for i in range(page_count)
        ]

        for start, end in page_groups:
            part = pikepdf.Pdf.new()
            part.pages.extend(src.pages[start : end + 1])
            buffer = io.BytesIO()
            part.save(buffer)
            part.close()
            label = f"page_{start + 1}" if start == end else f"pages_{start + 1}-{end + 1}"
            results.append((f"{label}.pdf", buffer.getvalue()))
    return results


def _resolve_ghostscript_binary() -> str:
    if GHOSTSCRIPT_CMD:
        return GHOSTSCRIPT_CMD
    return "gswin64c" if os.name == "nt" else "gs"


def compress_pdf(data: bytes, quality: str) -> bytes:
    """Recompresses a PDF via Ghostscript's pdfwrite device — mainly shrinks
    embedded images down to the preset's target DPI, so gains are largest on
    image-heavy/scanned PDFs and small-to-none on already-lean text PDFs."""
    preset = GHOSTSCRIPT_QUALITY_PRESETS.get(quality, GHOSTSCRIPT_QUALITY_PRESETS["medium"])
    gs_bin = _resolve_ghostscript_binary()

    with tempfile.TemporaryDirectory() as tmp:
        input_path = Path(tmp) / "input.pdf"
        output_path = Path(tmp) / "output.pdf"
        input_path.write_bytes(data)

        result = subprocess.run(
            [
                gs_bin,
                "-sDEVICE=pdfwrite",
                "-dCompatibilityLevel=1.4",
                f"-dPDFSETTINGS={preset}",
                "-dNOPAUSE",
                "-dBATCH",
                "-dQUIET",
                f"-sOutputFile={output_path}",
                str(input_path),
            ],
            capture_output=True,
            timeout=180,
        )
        if result.returncode != 0 or not output_path.exists():
            raise RuntimeError(
                f"Ghostscript failed (exit {result.returncode}): "
                f"{result.stderr.decode(errors='replace')[:500]}"
            )
        return output_path.read_bytes()


def pdf_to_docx(data: bytes) -> bytes:
    """pdf2docx's Converter works against real file paths, not in-memory
    streams, hence the temp files — same reason office_tools.docx_to_pdf uses
    them for LibreOffice."""
    from pdf2docx import Converter  # heavy import (pulls in fitz/docx) — deferred to call time

    with tempfile.TemporaryDirectory() as tmp:
        input_path = Path(tmp) / "input.pdf"
        output_path = Path(tmp) / "output.docx"
        input_path.write_bytes(data)

        converter = Converter(str(input_path))
        try:
            converter.convert(str(output_path))
        finally:
            converter.close()
        return output_path.read_bytes()
