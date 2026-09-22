"""Re-run classification + structured extraction for documents currently
categorized "Other", using their already-stored raw_text (no re-upload/OCR).

One-off recovery script for the reasoning-model token-budget bug (see
CLAUDE.md's "Reasoning-model gotcha" note) that caused real Invoice/Agreement/
GST documents to be silently misclassified as "Other".

Usage (from backend/, with the venv active):
    python scripts/reclassify_other_documents.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from classification import CURRENT_CLASSIFICATION_VERSION, classify_text
from database import SessionLocal
from models import Document
from structured_extraction import extract_structured_data, get_deadline_date

STRUCTURED_CATEGORIES = {"Agreement", "Invoice", "GST Document", "Purchase Order"}


def main() -> None:
    db = SessionLocal()
    try:
        documents = db.query(Document).filter(Document.category == "Other").all()
        print(f"Found {len(documents)} document(s) with category='Other'.\n")

        reclassified = []
        unchanged = []
        failed = []

        for doc in documents:
            try:
                new_category, reasoning = classify_text(doc.raw_text)
            except Exception as exc:
                failed.append((doc, str(exc)))
                print(f"[FAILED] {doc.filename}: {exc}")
                continue

            doc.classification_reasoning = reasoning
            doc.classification_version = CURRENT_CLASSIFICATION_VERSION

            if new_category == "Other":
                db.add(doc)  # still persist the refreshed reasoning/version even if unchanged
                unchanged.append(doc)
                continue

            doc.category = new_category

            if new_category in STRUCTURED_CATEGORIES:
                try:
                    extracted = extract_structured_data(new_category, doc.raw_text)
                    doc.extracted_json = extracted
                    doc.deadline_date = get_deadline_date(new_category, extracted)
                    doc.status = "processed"
                    doc.error_message = None
                except Exception as exc:
                    doc.status = "structuring_failed"
                    doc.error_message = str(exc)
            else:
                doc.status = "processed"
                doc.error_message = None

            db.add(doc)
            reclassified.append(doc)

        db.commit()

        print("=" * 60)
        print(f"Reprocessed: {len(documents)}")
        print(f"Reclassified (category changed): {len(reclassified)}")
        for doc in reclassified:
            print(f"  - {doc.filename}: Other -> {doc.category} (status={doc.status})")
        print(f"Still 'Other' (unchanged): {len(unchanged)}")
        for doc in unchanged:
            print(f"  - {doc.filename}")
        if failed:
            print(f"Failed to reclassify: {len(failed)}")
            for doc, err in failed:
                print(f"  - {doc.filename}: {err}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
