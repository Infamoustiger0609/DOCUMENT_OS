FROM python:3.11-slim

# tesseract-ocr: main pipeline OCR fallback (extraction.py) + /tools/ocr-pdf.
# ghostscript: /tools/compress-pdf, and a dependency ocrmypdf shells out to for
#   page rasterization/optimization.
# libreoffice-writer: /tools/docx-to-pdf headless conversion (pulls in the
#   rest of libreoffice-core it needs via apt; the full `libreoffice` meta
#   package isn't needed just for Writer->PDF).
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    ghostscript \
    libreoffice-writer \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render's free plan doesn't support preDeployCommand (paid-plan only), and
# render.yaml has no other pre-deploy hook — a pending migration used to be a
# manual, easy-to-forget step (see CLAUDE.md's Deployment section), which is
# exactly how documents.source (migration 0018) shipped in app code before the
# production database had the column, causing real 500s on upload/list.
# "Migrate on boot" instead: alembic upgrade head runs on every container
# start (every deploy, and every free-tier cold-start wake) before uvicorn
# starts serving traffic. Idempotent when already at head, so this adds only
# a trivial DB round-trip on a normal wake. `&&` means a failed migration
# stops the container from ever starting with a stale/mismatched schema,
# rather than silently serving broken requests.
CMD ["sh", "-c", "alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}"]
