import logging
from pathlib import Path

from sqlalchemy.orm import Session

from classification import CURRENT_CLASSIFICATION_VERSION, classify_text
from extraction import extract_text
from models import Document
from storage import download_file_from_storage
from structured_extraction import extract_structured_data, get_deadline_date

logger = logging.getLogger(__name__)

STRUCTURED_CATEGORIES = {"Agreement", "Invoice", "GST Document", "GST Filing", "Purchase Order"}

# error_message is stored on the documents row and returned by GET /documents(/{id})
# to any authenticated user (there's no per-user document ownership — see CLAUDE.md's
# Settings section), so it must never contain raw exception text: a Storage/Groq/DB
# client's str(exc) could in principle echo back request/connection details. Real
# exceptions are still fully logged server-side via logger.exception() below; only
# these fixed, generic strings are ever persisted or sent to a client.
EXTRACTION_FAILED_MESSAGE = (
    "Text extraction failed. Please try re-uploading the file — if it keeps failing, "
    "the file may be corrupted or unreadable."
)
CLASSIFICATION_FAILED_MESSAGE = (
    "Classification failed. Try reprocessing this document, or contact support if it "
    "keeps failing."
)
STRUCTURING_FAILED_MESSAGE = (
    "Structured data extraction failed. Try reprocessing this document, or contact "
    "support if it keeps failing."
)


def classify_and_structure(document: Document, db: Session) -> Document:
    """Runs classification + structured extraction against document.raw_text,
    which must already be populated. Used both as the tail end of the upload
    pipeline (process_document, below) and for manually reprocessing a document
    whose raw_text already exists but whose classification/structuring failed
    or looks wrong (see POST /documents/{id}/reprocess and
    scripts/reclassify_other_documents.py).
    """
    try:
        category, reasoning = classify_text(document.raw_text)
        document.category = category
        document.classification_reasoning = reasoning
        document.classification_version = CURRENT_CLASSIFICATION_VERSION
        document.status = "classified"
        document.error_message = None
    except Exception:
        logger.exception("Classification failed for document %s", document.id)
        document.status = "classification_failed"
        document.error_message = CLASSIFICATION_FAILED_MESSAGE
        db.add(document)
        db.commit()
        db.refresh(document)
        return document

    if document.category in STRUCTURED_CATEGORIES:
        try:
            extracted = extract_structured_data(document.category, document.raw_text)
            document.extracted_json = extracted
            document.deadline_date = get_deadline_date(document.category, extracted)
            document.status = "processed"
            document.error_message = None
        except Exception:
            logger.exception("Structured extraction failed for document %s", document.id)
            document.status = "structuring_failed"
            document.error_message = STRUCTURING_FAILED_MESSAGE
    else:
        document.status = "processed"

    db.add(document)
    db.commit()
    db.refresh(document)
    return document


def process_document(document: Document, db: Session) -> Document:
    extension = Path(document.storage_path).suffix.lower()
    try:
        file_bytes = download_file_from_storage(document.storage_path)
        document.raw_text = extract_text(file_bytes, extension)
        document.status = "extracted"
        document.error_message = None
    except Exception:
        logger.exception("Text extraction failed for document %s", document.id)
        document.status = "extraction_failed"
        document.error_message = EXTRACTION_FAILED_MESSAGE
        db.add(document)
        db.commit()
        db.refresh(document)
        return document

    return classify_and_structure(document, db)
