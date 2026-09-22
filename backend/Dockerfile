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

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}"]
