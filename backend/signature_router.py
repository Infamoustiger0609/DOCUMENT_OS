"""Self-serve e-signature capture + document stamping (Phase 31 — see
CLAUDE.md's E-signature section). Two distinct concerns sharing one router:

1. `/auth/signature` (save/preview/remove) — the user's own saved signature
   image, referenced by `users.signature_storage_path`.
2. `/documents/{id}/sign` + `/signed-documents/{id}/download-url` — stamping
   that saved signature onto one of the user's own PDFs, producing a new
   SignedDocument row (the original document is never modified in place).

Deliberately scoped to **self-serve** signing (your own documents) — see
CLAUDE.md for why a multi-party signature-request workflow is explicitly out
of scope for this phase.
"""

import logging
import uuid
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from sqlalchemy.orm import Session

from audit_log import record as record_audit_log
from auth import get_current_user
from database import get_db
from file_validation import matches_declared_type
from models import Document, SignedDocument, User
from rate_limit import limiter, user_or_ip_key
from schemas import DownloadUrlOut, SignatureStatusOut, SignedDocumentOut
from signature_tools import stamp_signature
from storage import (
    create_signed_url,
    delete_file_from_storage,
    download_file_from_storage,
    upload_file_to_storage,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["signature"])

SIGNATURE_EXTENSIONS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
SIGNATURE_MAX_FILE_SIZE_BYTES = 2 * 1024 * 1024
SIGNATURES_STORAGE_PREFIX = "signatures"
SIGNED_DOCUMENTS_STORAGE_PREFIX = "signed-documents"
# Same 300s window as main.py's DOWNLOAD_URL_EXPIRES_SECONDS — not imported
# from there to avoid a main.py <-> router import cycle (main.py imports this
# router, the same reason tools_router.py/editor_router.py define their own
# constants rather than importing main.py's).
DOWNLOAD_URL_EXPIRES_SECONDS = 300

# Same per-user-per-hour shape as /documents/upload and every /tools/*
# endpoint (see CLAUDE.md's Rate limiting section) — signing shells out to
# PyMuPDF, not free, so it gets the same shared budget rather than an
# arbitrary new number. Also applied to POST /auth/signature (security audit
# finding — see CLAUDE.md's Security audit section): every other upload
# endpoint in the app already had a rate limit, this one didn't.
SIGN_RATE_LIMIT = "20/hour"


def _signature_storage_key(user_id: uuid.UUID, extension: str) -> str:
    return f"{SIGNATURES_STORAGE_PREFIX}/{user_id}/signature{extension}"


def _signature_status(current_user: User) -> SignatureStatusOut:
    if not current_user.signature_storage_path:
        return SignatureStatusOut(has_signature=False)
    try:
        url = create_signed_url(current_user.signature_storage_path, expires_in=DOWNLOAD_URL_EXPIRES_SECONDS)
    except Exception:
        logger.exception("Signed URL generation failed for signature (user %s)", current_user.id)
        raise HTTPException(status_code=502, detail="Could not load your saved signature right now.")
    return SignatureStatusOut(has_signature=True, download_url=url, expires_in=DOWNLOAD_URL_EXPIRES_SECONDS)


@router.post("/auth/signature", response_model=SignatureStatusOut)
@limiter.limit(SIGN_RATE_LIMIT, key_func=user_or_ip_key)
async def save_signature(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Saves (or replaces) the current user's one signature image — drawn on
    a canvas and exported as a PNG blob, or a directly uploaded PNG/JPEG, the
    frontend sends either the same way. A second save overwrites the first;
    there's only ever one saved signature per user, not a history of them."""
    extension = Path(file.filename or "").suffix.lower()
    if extension not in SIGNATURE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Signature must be a PNG or JPEG image.")

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded signature is empty.")
    if len(contents) > SIGNATURE_MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="Signature image exceeds the 2MB limit.")
    if not matches_declared_type(contents, extension):
        raise HTTPException(
            status_code=400,
            detail="File content doesn't match its extension. Please provide a genuine PNG or JPEG image.",
        )

    new_key = _signature_storage_key(current_user.id, extension)
    # The extension (hence the key) can change between saves — e.g. a JPEG
    # upload replacing an earlier canvas-drawn PNG — so the old object is
    # only safe to delete once the new one has a different key.
    if current_user.signature_storage_path and current_user.signature_storage_path != new_key:
        try:
            delete_file_from_storage(current_user.signature_storage_path)
        except Exception:
            logger.warning(
                "Storage delete failed for old signature (user %s)", current_user.id, exc_info=True
            )

    upload_file_to_storage(contents, new_key, content_type=SIGNATURE_EXTENSIONS[extension])
    current_user.signature_storage_path = new_key
    db.add(current_user)
    db.commit()

    return _signature_status(current_user)


@router.get("/auth/signature", response_model=SignatureStatusOut)
def get_signature(current_user: User = Depends(get_current_user)):
    """Backs the Settings page's live signature preview — a short-lived
    signed URL, generated fresh each call (this endpoint isn't cached), not a
    long-lived link."""
    return _signature_status(current_user)


@router.delete("/auth/signature", status_code=204)
def delete_signature(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if current_user.signature_storage_path:
        try:
            delete_file_from_storage(current_user.signature_storage_path)
        except Exception:
            logger.warning(
                "Storage delete failed removing signature (user %s)", current_user.id, exc_info=True
            )
        current_user.signature_storage_path = None
        db.add(current_user)
        db.commit()
    return Response(status_code=204)


def _get_owned_document_for_signing(db: Session, document_id: uuid.UUID, current_user: User) -> Document:
    """Same shape as main.py's _get_owned_document (id + user_id in one
    filter, so someone else's document 404s exactly like a nonexistent one —
    see CLAUDE.md's Per-user document isolation section) — duplicated here
    rather than imported from main.py, since main.py is the one importing
    this router and importing back would be circular."""
    document = (
        db.query(Document)
        .filter(Document.id == document_id, Document.user_id == current_user.id)
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    return document


@router.post("/documents/{document_id}/sign", response_model=SignedDocumentOut)
@limiter.limit(SIGN_RATE_LIMIT, key_func=user_or_ip_key)
def sign_document(
    request: Request,
    response: Response,
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not current_user.signature_storage_path:
        raise HTTPException(
            status_code=400,
            detail="Save a signature in Settings before signing a document.",
        )

    document = _get_owned_document_for_signing(db, document_id, current_user)
    if Path(document.storage_path).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="Only PDF documents can be signed.")

    try:
        pdf_bytes = download_file_from_storage(document.storage_path)
        signature_bytes = download_file_from_storage(current_user.signature_storage_path)
        signed_bytes = stamp_signature(pdf_bytes, signature_bytes)
    except Exception:
        logger.exception("Signing failed for document %s", document_id)
        raise HTTPException(
            status_code=502, detail="Could not sign this document right now. Please try again."
        )

    # output_filename (display-only, stored in SignedDocument.filename) is
    # built from the user's own original upload filename — never used to
    # build the Storage KEY below. Verified live that Supabase Storage
    # genuinely resolves "../" segments in a key (the object lands outside
    # the intended prefix), so embedding it directly here would have let a
    # crafted upload filename write outside signed-documents/{user_id}/ — see
    # the matching fix in tools_common._build_tool_storage_key(). Signing
    # only ever produces a PDF (checked above), so the extension is fixed.
    output_filename = f"signed_{document.filename}"
    storage_path = f"{SIGNED_DOCUMENTS_STORAGE_PREFIX}/{current_user.id}/{uuid.uuid4()}.pdf"
    upload_file_to_storage(signed_bytes, storage_path, content_type="application/pdf")

    signed_document = SignedDocument(
        user_id=current_user.id,
        document_id=document.id,
        filename=output_filename,
        storage_path=storage_path,
    )
    db.add(signed_document)
    db.commit()
    db.refresh(signed_document)
    record_audit_log(db, current_user, "sign", "document", document.id, detail=document.filename)
    return signed_document


@router.get("/documents/{document_id}/signed", response_model=List[SignedDocumentOut])
def list_signed_versions(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Newest first — lets the document detail page show "already signed,
    download it" for a document signed in an earlier visit, not just
    immediately after POST /sign in the same session."""
    _get_owned_document_for_signing(db, document_id, current_user)
    return (
        db.query(SignedDocument)
        .filter(SignedDocument.document_id == document_id, SignedDocument.user_id == current_user.id)
        .order_by(SignedDocument.created_at.desc())
        .all()
    )


@router.get("/signed-documents/{signed_id}/download-url", response_model=DownloadUrlOut)
def get_signed_document_download_url(
    signed_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    signed_document = (
        db.query(SignedDocument)
        .filter(SignedDocument.id == signed_id, SignedDocument.user_id == current_user.id)
        .first()
    )
    if signed_document is None:
        raise HTTPException(status_code=404, detail="Signed document not found.")

    try:
        url = create_signed_url(signed_document.storage_path, expires_in=DOWNLOAD_URL_EXPIRES_SECONDS)
    except Exception:
        logger.exception("Signed URL generation failed for signed document %s", signed_id)
        raise HTTPException(status_code=502, detail="Could not generate a download link right now.")
    return DownloadUrlOut(url=url, expires_in=DOWNLOAD_URL_EXPIRES_SECONDS)
