"""Document generation from a learned template (Phase 32 — see CLAUDE.md's
Document generation section). Two steps, two endpoints (plus small
list/get/delete CRUD around templates):

1. POST /templates/learn-from-sample — upload a sample Invoice/Agreement,
   learn its field STRUCTURE (never its values — see template_learning.py).
2. POST /templates/{id}/generate — fill that structure with new values
   (given directly, or parsed from a natural-language instruction) and
   render a brand-new PDF (template_generation.py). The result is an
   ordinary `documents` row, not a separate table — see the docstring on
   generate_document() below for why.

Deliberately NOT visual-layout cloning of the sample (exact fonts, logo
position, spacing) — only the field structure carries over. See CLAUDE.md
for the full scope boundary.
"""

import logging
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from sqlalchemy.orm import Session

from audit_log import record as record_audit_log
from auth import get_current_user
from cache import invalidate_document_list_caches
from classification import CURRENT_CLASSIFICATION_VERSION, classify_text
from database import get_db
from extraction import extract_text
from file_validation import matches_declared_type
from models import Document, Template, User
from processing import CLASSIFICATION_FAILED_MESSAGE, EXTRACTION_FAILED_MESSAGE
from rate_limit import limiter, user_or_ip_key
from schemas import DocumentOut, TemplateGenerateRequest, TemplateOut
from storage import download_file_from_storage, upload_file_to_storage
from template_generation import build_generated_filename, guess_deadline_date, render_document, values_to_text
from template_learning import learn_field_schema, normalize_values, parse_instruction_to_values

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/templates", tags=["templates"])

# Matches this phase's own stated scope — see CLAUDE.md's Document generation
# section for why these two first, and how a 3rd category gets added later.
SUPPORTED_TEMPLATE_CATEGORIES = {"Invoice", "Agreement"}

ALLOWED_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024
GENERATED_DOCUMENTS_STORAGE_PREFIX = "generated-documents"

# Same 20/hour per-user shape as every other AI-calling/compute-heavy
# endpoint in this app (uploads, /tools/*, /editor/chat, /copilot/chat,
# /analytics/generate) — see CLAUDE.md's Rate limiting section.
TEMPLATES_RATE_LIMIT = "20/hour"


@router.post("/learn-from-sample", response_model=TemplateOut)
@limiter.limit(TEMPLATES_RATE_LIMIT, key_func=user_or_ip_key)
async def learn_from_sample(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Uploads the sample the same way POST /documents/upload does (so it's a
    real, ordinary Document the user can see/reprocess/delete like any
    other), then — synchronously, not backgrounded — extracts its text and
    classifies it, same functions the main pipeline uses (extraction.py,
    classification.py), just without the structured-extraction step (which
    would extract this ONE sample's values, not its reusable structure).
    Synchronous because the caller needs the learned schema back in this
    same response, not via polling."""
    extension = Path(file.filename or "").suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400, detail="Unsupported file type. Allowed: PDF, JPG, PNG, TIFF."
        )

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(contents) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="File exceeds the 20MB limit.")
    if not matches_declared_type(contents, extension):
        raise HTTPException(
            status_code=400,
            detail="File content doesn't match its extension. Please upload a genuine PDF, JPG, PNG, or TIFF file.",
        )

    object_key = f"{uuid.uuid4()}{extension}"
    upload_file_to_storage(contents, object_key, content_type=ALLOWED_EXTENSIONS[extension])

    document = Document(
        filename=file.filename, storage_path=object_key, status="uploaded", user_id=current_user.id
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    invalidate_document_list_caches()

    try:
        document.raw_text = extract_text(contents, extension)
        document.status = "extracted"
        document.error_message = None
        db.add(document)
        db.commit()
    except Exception:
        logger.exception("Text extraction failed for template sample (document %s)", document.id)
        document.status = "extraction_failed"
        document.error_message = EXTRACTION_FAILED_MESSAGE
        db.add(document)
        db.commit()
        invalidate_document_list_caches()
        raise HTTPException(
            status_code=502,
            detail="Could not extract text from this sample. Please try a clearer scan or a different file.",
        )

    try:
        category, reasoning = classify_text(document.raw_text)
        document.category = category
        document.classification_reasoning = reasoning
        document.classification_version = CURRENT_CLASSIFICATION_VERSION
        document.status = "processed"
        document.error_message = None
        db.add(document)
        db.commit()
    except Exception:
        logger.exception("Classification failed for template sample (document %s)", document.id)
        document.status = "classification_failed"
        document.error_message = CLASSIFICATION_FAILED_MESSAGE
        db.add(document)
        db.commit()
        invalidate_document_list_caches()
        raise HTTPException(status_code=502, detail="Could not classify this sample. Please try again.")

    invalidate_document_list_caches()

    if category not in SUPPORTED_TEMPLATE_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"This sample was classified as '{category}'. Template learning currently "
                f"supports only: {', '.join(sorted(SUPPORTED_TEMPLATE_CATEGORIES))}."
            ),
        )

    try:
        field_schema = learn_field_schema(category, document.raw_text)
    except Exception:
        logger.exception("Template schema learning failed for document %s", document.id)
        raise HTTPException(
            status_code=502,
            detail="Could not learn a template from this sample right now. Please try again.",
        )

    template = Template(
        user_id=current_user.id,
        name=(name or "").strip() or f"{category} template — {file.filename}",
        category=category,
        field_schema=field_schema,
        created_from_document_id=document.id,
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


@router.get("", response_model=List[TemplateOut])
def list_templates(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return (
        db.query(Template)
        .filter(Template.user_id == current_user.id)
        .order_by(Template.created_at.desc())
        .all()
    )


def _get_owned_template(db: Session, template_id: uuid.UUID, current_user: User) -> Template:
    """Same id+user_id-in-one-filter pattern as every other ownership check
    in this app (main.py's _get_owned_document, signature_router.py's
    _get_owned_document_for_signing) — someone else's template 404s exactly
    like a nonexistent one."""
    template = (
        db.query(Template)
        .filter(Template.id == template_id, Template.user_id == current_user.id)
        .first()
    )
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found.")
    return template


@router.get("/{template_id}", response_model=TemplateOut)
def get_template(
    template_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _get_owned_template(db, template_id, current_user)


@router.delete("/{template_id}", status_code=204)
def delete_template(
    template_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    template = _get_owned_template(db, template_id, current_user)
    db.delete(template)
    db.commit()
    return Response(status_code=204)


@router.post("/{template_id}/generate", response_model=DocumentOut)
@limiter.limit(TEMPLATES_RATE_LIMIT, key_func=user_or_ip_key)
def generate_document(
    request: Request,
    response: Response,
    template_id: uuid.UUID,
    payload: TemplateGenerateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Produces a brand-new, ordinary `documents` row — NOT a separate
    "generated documents" table. Its category is already known (the
    template's own), and `values` already IS its structured data (there was
    never a values-extraction step to skip), so it lands directly on
    status="processed" with extracted_json/raw_text/deadline_date all
    populated in this same request — no background pipeline, no polling.
    This means a generated document is immediately visible in /documents,
    downloadable via the existing GET /documents/{id}/download-url,
    chattable via the existing POST /documents/{id}/chat (raw_text is a
    plain-text rendering of its own values), and searchable via the
    Copilot's full-text search — all for free, no new code path needed."""
    template = _get_owned_template(db, template_id, current_user)

    if payload.values:
        values = normalize_values(template.field_schema, payload.values)
    elif payload.instruction and payload.instruction.strip():
        try:
            values = parse_instruction_to_values(template.field_schema, payload.instruction)
        except Exception:
            logger.exception("Instruction parsing failed for template %s", template_id)
            raise HTTPException(
                status_code=502,
                detail="Could not understand that description. Please try rephrasing, or use the form instead.",
            )
    else:
        raise HTTPException(
            status_code=400, detail="Provide either field values or a natural-language description."
        )

    signature_image_bytes = None
    if current_user.signature_storage_path:
        try:
            signature_image_bytes = download_file_from_storage(current_user.signature_storage_path)
        except Exception:
            # Best-effort — a saved signature that can't be fetched right
            # now shouldn't block generating the document at all; it just
            # renders with blank space for a physical signature instead,
            # same as a user with no saved signature.
            logger.warning(
                "Could not fetch saved signature for user %s during generation", current_user.id, exc_info=True
            )

    try:
        pdf_bytes = render_document(
            template.field_schema, values, signature_image_bytes=signature_image_bytes
        )
    except Exception:
        logger.exception("Document generation failed for template %s", template_id)
        raise HTTPException(status_code=502, detail="Could not generate a document right now. Please try again.")

    # output_filename is what the user sees (and what a real download is
    # saved as — see GET /documents/{id}/download-url and the frontend's
    # download buttons); storage_path is the internal Storage object key.
    # A UUID belongs only in the latter — it must never leak into the former,
    # which previously happened here (the old scheme literally embedded a
    # uuid4() into storage_path and then displayed that whole key as if it
    # were the filename).
    output_filename = build_generated_filename(template.category, template.field_schema, values)
    storage_path = f"{GENERATED_DOCUMENTS_STORAGE_PREFIX}/{current_user.id}/{uuid.uuid4()}.pdf"
    upload_file_to_storage(pdf_bytes, storage_path, content_type="application/pdf")

    # extracted_json is deliberately NOT the flat, fixed-key shape
    # structured_extraction.py's SCHEMAS use for normal Invoice/Agreement
    # documents — a learned template's field keys are whatever Groq named
    # them from this specific sample, not guaranteed to match the fixed
    # schema's names. Wrapping the schema + values together makes this
    # document's extracted_json fully self-describing: the frontend's
    # document detail page renders it generically from this shape (see
    # GeneratedDocumentCards in documents/[id]/page.tsx) instead of trying
    # (and failing) to look up fixed FIELD_SECTIONS keys that don't apply.
    extracted_json = {
        "_generated": True,
        "_field_schema": template.field_schema,
        "_values": values,
    }

    document = Document(
        user_id=current_user.id,
        filename=output_filename,
        storage_path=storage_path,
        status="processed",
        category=template.category,
        raw_text=values_to_text(template.field_schema, values),
        extracted_json=extracted_json,
        deadline_date=guess_deadline_date(values),
        generated_from_template_id=template.id,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    invalidate_document_list_caches()
    record_audit_log(db, current_user, "generate", "document", document.id, detail=output_filename)
    return document
