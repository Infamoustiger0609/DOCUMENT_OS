"""Document-editing tool endpoints — merge/split/compress PDF, PDF<->DOCX,
image convert/resize, OCR-to-searchable-PDF. Deliberately separate from the
main documents pipeline (main.py's /documents/* routes): no classification, no
structured-field extraction, no Groq call — pure file transformation. See
CLAUDE.md's Document tools section for the full design, including why each
endpoint takes its input file directly via multipart (rather than a separate
upload-then-process step) and how output retention/cleanup works.
"""

import logging
import uuid
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from file_validation import matches_declared_type
from image_tools import convert_image, resize_image
from models import User
from ocr_tools import ocr_pdf
from office_tools import docx_to_pdf
from pdf_tools import compress_pdf, merge_pdfs, pdf_to_docx, split_pdf
from rate_limit import limiter, user_or_ip_key
from schemas import DownloadUrlOut, ToolFileOut
from storage import create_signed_url
from tools_common import (
    TOOLS_MAX_FILE_SIZE_BYTES,
    cleanup_expired_tool_files,
    get_owned_tool_file,
    save_tool_output,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tools", tags=["tools"])

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
IMAGE_MIME_BY_TARGET = {"jpg": "image/jpeg", "png": "image/png", "tiff": "image/tiff"}

# Same per-user-per-hour shape as main.py's /documents/upload limit (also
# keyed by user_or_ip_key) — these are at least as compute-heavy per call
# (Ghostscript/LibreOffice/OCR subprocesses), so the same budget is, if
# anything, a conservative shared limit rather than an arbitrary new number.
TOOLS_RATE_LIMIT = "20/hour"


async def _read_validated_upload(file: UploadFile, extension: str, label: Optional[str] = None) -> bytes:
    """Shared validation for every tool endpoint below: extension allowed,
    non-empty, under the size cap, and content matches its declared type —
    same three checks main.py's /documents/upload does, just against
    TOOLS_MAX_FILE_SIZE_BYTES instead of the main pipeline's 20MB cap."""
    prefix = f"{label}: " if label else ""
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail=f"{prefix}Uploaded file is empty.")
    if len(contents) > TOOLS_MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"{prefix}File exceeds the {TOOLS_MAX_FILE_SIZE_BYTES // (1024 * 1024)}MB limit.",
        )
    if not matches_declared_type(contents, extension):
        raise HTTPException(
            status_code=400,
            detail=f"{prefix}File content doesn't match its extension ({extension}).",
        )
    return contents


@router.post("/merge-pdf", response_model=ToolFileOut)
@limiter.limit(TOOLS_RATE_LIMIT, key_func=user_or_ip_key)
async def merge_pdf_endpoint(
    request: Request,
    response: Response,
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cleanup_expired_tool_files(db)
    if len(files) < 2:
        raise HTTPException(status_code=400, detail="Provide at least 2 PDF files to merge.")

    contents_list = []
    for f in files:
        extension = Path(f.filename or "").suffix.lower()
        if extension != ".pdf":
            raise HTTPException(status_code=400, detail=f"{f.filename}: only PDF files can be merged.")
        contents_list.append(await _read_validated_upload(f, ".pdf", label=f.filename))

    try:
        merged = merge_pdfs(contents_list)
    except Exception:
        logger.exception("PDF merge failed for user %s", current_user.id)
        raise HTTPException(status_code=502, detail="Could not merge these PDFs.")

    return save_tool_output(
        db,
        current_user,
        tool_name="merge-pdf",
        original_filename=", ".join(f.filename or "unnamed.pdf" for f in files),
        output_filename="merged.pdf",
        data=merged,
        mime_type=PDF_MIME,
    )


@router.post("/split-pdf", response_model=List[ToolFileOut])
@limiter.limit(TOOLS_RATE_LIMIT, key_func=user_or_ip_key)
async def split_pdf_endpoint(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    ranges: Optional[str] = Form(None, description='e.g. "1-3,5,7-9". Omit to split into one PDF per page.'),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cleanup_expired_tool_files(db)
    extension = Path(file.filename or "").suffix.lower()
    if extension != ".pdf":
        raise HTTPException(status_code=400, detail="Only PDF files can be split.")
    contents = await _read_validated_upload(file, ".pdf")

    try:
        parts = split_pdf(contents, ranges)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        logger.exception("PDF split failed for user %s", current_user.id)
        raise HTTPException(status_code=502, detail="Could not split this PDF.")

    return [
        save_tool_output(
            db,
            current_user,
            tool_name="split-pdf",
            original_filename=file.filename or "unnamed.pdf",
            output_filename=name,
            data=part_bytes,
            mime_type=PDF_MIME,
        )
        for name, part_bytes in parts
    ]


@router.post("/compress-pdf", response_model=ToolFileOut)
@limiter.limit(TOOLS_RATE_LIMIT, key_func=user_or_ip_key)
async def compress_pdf_endpoint(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    quality: Literal["low", "medium", "high"] = Form("medium"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cleanup_expired_tool_files(db)
    extension = Path(file.filename or "").suffix.lower()
    if extension != ".pdf":
        raise HTTPException(status_code=400, detail="Only PDF files can be compressed.")
    contents = await _read_validated_upload(file, ".pdf")

    try:
        compressed = compress_pdf(contents, quality)
    except Exception:
        logger.exception("PDF compression failed for user %s", current_user.id)
        raise HTTPException(status_code=502, detail="Could not compress this PDF.")

    stem = Path(file.filename or "document").stem
    return save_tool_output(
        db,
        current_user,
        tool_name="compress-pdf",
        original_filename=file.filename or "unnamed.pdf",
        output_filename=f"compressed_{stem}.pdf",
        data=compressed,
        mime_type=PDF_MIME,
    )


@router.post("/pdf-to-docx", response_model=ToolFileOut)
@limiter.limit(TOOLS_RATE_LIMIT, key_func=user_or_ip_key)
async def pdf_to_docx_endpoint(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cleanup_expired_tool_files(db)
    extension = Path(file.filename or "").suffix.lower()
    if extension != ".pdf":
        raise HTTPException(status_code=400, detail="Only PDF files can be converted to DOCX.")
    contents = await _read_validated_upload(file, ".pdf")

    try:
        converted = pdf_to_docx(contents)
    except Exception:
        logger.exception("PDF-to-DOCX conversion failed for user %s", current_user.id)
        raise HTTPException(status_code=502, detail="Could not convert this PDF to DOCX.")

    stem = Path(file.filename or "document").stem
    return save_tool_output(
        db,
        current_user,
        tool_name="pdf-to-docx",
        original_filename=file.filename or "unnamed.pdf",
        output_filename=f"{stem}.docx",
        data=converted,
        mime_type=DOCX_MIME,
    )


@router.post("/docx-to-pdf", response_model=ToolFileOut)
@limiter.limit(TOOLS_RATE_LIMIT, key_func=user_or_ip_key)
async def docx_to_pdf_endpoint(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cleanup_expired_tool_files(db)
    extension = Path(file.filename or "").suffix.lower()
    if extension != ".docx":
        raise HTTPException(status_code=400, detail="Only DOCX files can be converted to PDF.")
    contents = await _read_validated_upload(file, ".docx")

    try:
        converted = docx_to_pdf(contents)
    except Exception:
        logger.exception("DOCX-to-PDF conversion failed for user %s", current_user.id)
        raise HTTPException(status_code=502, detail="Could not convert this DOCX to PDF.")

    stem = Path(file.filename or "document").stem
    return save_tool_output(
        db,
        current_user,
        tool_name="docx-to-pdf",
        original_filename=file.filename or "unnamed.docx",
        output_filename=f"{stem}.pdf",
        data=converted,
        mime_type=PDF_MIME,
    )


@router.post("/convert-image", response_model=ToolFileOut)
@limiter.limit(TOOLS_RATE_LIMIT, key_func=user_or_ip_key)
async def convert_image_endpoint(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    target_format: Literal["jpg", "png", "tiff"] = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cleanup_expired_tool_files(db)
    extension = Path(file.filename or "").suffix.lower()
    if extension not in IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Only JPG, PNG, or TIFF files can be converted.")
    contents = await _read_validated_upload(file, extension)

    try:
        converted = convert_image(contents, target_format)
    except Exception:
        logger.exception("Image conversion failed for user %s", current_user.id)
        raise HTTPException(status_code=502, detail="Could not convert this image.")

    stem = Path(file.filename or "image").stem
    return save_tool_output(
        db,
        current_user,
        tool_name="convert-image",
        original_filename=file.filename or "unnamed",
        output_filename=f"{stem}.{target_format}",
        data=converted,
        mime_type=IMAGE_MIME_BY_TARGET[target_format],
    )


@router.post("/resize-image", response_model=ToolFileOut)
@limiter.limit(TOOLS_RATE_LIMIT, key_func=user_or_ip_key)
async def resize_image_endpoint(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    width: Optional[int] = Form(None),
    height: Optional[int] = Form(None),
    target_size_kb: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cleanup_expired_tool_files(db)
    extension = Path(file.filename or "").suffix.lower()
    if extension not in IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Only JPG, PNG, or TIFF files can be resized.")
    if not width and not height and not target_size_kb:
        raise HTTPException(
            status_code=400, detail="Provide at least one of: width, height, target_size_kb."
        )
    contents = await _read_validated_upload(file, extension)

    try:
        resized = resize_image(contents, extension, width, height, target_size_kb)
    except Exception:
        logger.exception("Image resize failed for user %s", current_user.id)
        raise HTTPException(status_code=502, detail="Could not resize this image.")

    mime_by_ext = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                   ".tif": "image/tiff", ".tiff": "image/tiff"}
    return save_tool_output(
        db,
        current_user,
        tool_name="resize-image",
        original_filename=file.filename or "unnamed",
        output_filename=f"resized_{file.filename}",
        data=resized,
        mime_type=mime_by_ext[extension],
    )


@router.post("/ocr-pdf", response_model=ToolFileOut)
@limiter.limit(TOOLS_RATE_LIMIT, key_func=user_or_ip_key)
async def ocr_pdf_endpoint(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    force_ocr: bool = Form(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cleanup_expired_tool_files(db)
    extension = Path(file.filename or "").suffix.lower()
    if extension != ".pdf":
        raise HTTPException(status_code=400, detail="Only PDF files can be OCR'd.")
    contents = await _read_validated_upload(file, ".pdf")

    try:
        searchable = ocr_pdf(contents, force_ocr=force_ocr)
    except Exception:
        logger.exception("OCR failed for user %s", current_user.id)
        raise HTTPException(status_code=502, detail="Could not OCR this PDF.")

    return save_tool_output(
        db,
        current_user,
        tool_name="ocr-pdf",
        original_filename=file.filename or "unnamed.pdf",
        output_filename=f"searchable_{file.filename}",
        data=searchable,
        mime_type=PDF_MIME,
    )


DOWNLOAD_URL_EXPIRES_SECONDS = 300


@router.get("/{tool_file_id}/download-url", response_model=DownloadUrlOut)
def get_tool_file_download_url(
    tool_file_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    tool_file = get_owned_tool_file(db, tool_file_id, current_user)
    try:
        url = create_signed_url(tool_file.storage_path, expires_in=DOWNLOAD_URL_EXPIRES_SECONDS)
    except Exception:
        logger.exception("Signed URL generation failed for tool file %s", tool_file_id)
        raise HTTPException(status_code=502, detail="Could not generate a download link right now.")
    return DownloadUrlOut(url=url, expires_in=DOWNLOAD_URL_EXPIRES_SECONDS)
