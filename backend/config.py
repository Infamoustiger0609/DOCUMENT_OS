import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

JWT_SECRET_KEY_MIN_LENGTH = 32


def _validate_jwt_secret_key(value: Optional[str]) -> str:
    """Security audit fix: there was previously no enforcement here at all,
    contradicting this app's own documented claim that it "won't safely start
    without a real value" — an unset/empty key actually let the app boot fine
    and only failed later, per-request, with a raw TypeError/InvalidKeyError on
    the first login/register call (verified directly: jwt.encode(payload, None,
    algorithm="HS256") raises TypeError; jwt.encode(payload, "", ...) raises
    InvalidKeyError). Failing loudly here instead is both safer — a token is
    never signed with a trivially weak/empty key — and far more debuggable
    than a mystery 500 on first login. The minimum length is a floor, not a
    target: it matches this app's own documented generation method,
    secrets.token_urlsafe(32), which produces a ~43-character value."""
    if not value or len(value) < JWT_SECRET_KEY_MIN_LENGTH:
        raise RuntimeError(
            f"JWT_SECRET_KEY must be set to a real, random value of at least "
            f"{JWT_SECRET_KEY_MIN_LENGTH} characters. Generate one with: "
            'python -c "import secrets; print(secrets.token_urlsafe(32))"'
        )
    return value


JWT_SECRET_KEY = _validate_jwt_secret_key(os.getenv("JWT_SECRET_KEY"))
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_EXPIRES_MINUTES = int(os.getenv("JWT_EXPIRES_MINUTES", "1440"))

# Comma-separated list of origins allowed to call the API (CORS). Defaults to the
# local Next.js dev server; set to the real Render frontend URL (e.g.
# https://documentos-ai-frontend.onrender.com) in production — never "*", since
# that would let any website's JS make authenticated requests using a token it
# stole via XSS on some other page (see auth-context.tsx's localStorage-token note).
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]

# Sentry error tracking (error_tracking.py) — optional. Unset locally by default;
# error_tracking.init_error_tracking() no-ops entirely when this is empty, so the
# app behaves identically with or without a Sentry project configured.
SENTRY_DSN = os.getenv("SENTRY_DSN")
# Tags every captured event so errors from local dev, Render's free-tier
# deployment, etc. can be told apart in the Sentry dashboard.
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

# Optional: path to the tesseract binary. Only needed on Windows dev machines
# where tesseract isn't on PATH after install (e.g. C:\Program Files\Tesseract-OCR\tesseract.exe).
TESSERACT_CMD = os.getenv("TESSERACT_CMD")

# Optional (Windows local dev only): full path to Ghostscript's console binary
# (gswin64c.exe/gswin32c.exe) if it isn't on PATH. Used directly by
# tools/pdf_tools.py's compress_pdf() (subprocess call to `gs`/`gswin64c`) and
# indirectly by ocrmypdf (tools/ocr_tools.py), which shells out to Ghostscript
# for page rasterization. On Linux/Docker this is normally left unset — the
# Dockerfile's apt-get ghostscript package puts `gs` on PATH already.
GHOSTSCRIPT_CMD = os.getenv("GHOSTSCRIPT_CMD")

# Full path to LibreOffice's headless binary (soffice/soffice.exe). Defaults to
# "soffice", which is what the Dockerfile's apt-get libreoffice-writer package
# puts on PATH; set to the full path on a Windows dev machine where it isn't
# on PATH (e.g. C:\Program Files\LibreOffice\program\soffice.exe).
LIBREOFFICE_CMD = os.getenv("LIBREOFFICE_CMD", "soffice")

# How long a /tools/* output file (and its Supabase Storage object under
# tool-outputs/{user_id}/) is kept before the retention sweep deletes it — see
# CLAUDE.md's Document tools section. These are transient working files, not
# part of the document registry, so they don't need to survive long.
TOOLS_RETENTION_HOURS = int(os.getenv("TOOLS_RETENTION_HOURS", "24"))

# ocrmypdf (tools/ocr_tools.py) shells out to `tesseract` and `gs`/`gswin64c`
# by literal command name on PATH — pytesseract's own tesseract_cmd override
# (set in extraction.py) doesn't affect ocrmypdf's separate subprocess calls.
# On a Windows dev machine where TESSERACT_CMD/GHOSTSCRIPT_CMD point at
# binaries that aren't on PATH, prepend their directories here once at import
# time so ocrmypdf can still find them. On Linux/Docker both binaries are
# already on PATH via apt-get, so these env vars are normally unset and this
# loop is a no-op.
for _binary_path in (TESSERACT_CMD, GHOSTSCRIPT_CMD):
    if _binary_path:
        _bin_dir = str(Path(_binary_path).parent)
        if _bin_dir not in os.environ.get("PATH", ""):
            os.environ["PATH"] = _bin_dir + os.pathsep + os.environ.get("PATH", "")
