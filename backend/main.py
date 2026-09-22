import logging
import time
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

import sentry_sdk
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from sqlalchemy.orm import Session

from audit_log import record as record_audit_log
from auth import (
    create_access_token,
    get_current_user,
    hash_password,
    validate_password_strength,
    verify_password,
)
from cache import TTLCache, deadlines_cache, documents_cache, invalidate_document_list_caches
from chat import answer_question
from config import ALLOWED_ORIGINS
from database import SessionLocal, get_db
from error_tracking import init_error_tracking
from file_validation import matches_declared_type
from logging_config import configure_logging, request_id_var
from models import Document, SignedDocument, ToolFile, User
from processing import classify_and_structure, process_document
from rate_limit import limiter, rate_limit_exceeded_handler, user_or_ip_key
from schemas import (
    ChangePasswordRequest,
    ChatRequest,
    ChatResponse,
    DocumentListOut,
    DocumentOut,
    DocumentUploadOut,
    DownloadUrlOut,
    Token,
    UserCreate,
    UserLogin,
    UserOut,
    UserPreferencesUpdate,
    UserProfileUpdate,
)
from analytics_router import router as analytics_router
from copilot_router import router as copilot_router
from editor_router import router as editor_router
from signature_router import router as signature_router
from storage import create_signed_url, delete_file_from_storage, upload_file_to_storage
from templates_router import router as templates_router
from tools_router import router as tools_router

configure_logging()
init_error_tracking()
logger = logging.getLogger(__name__)

DOWNLOAD_URL_EXPIRES_SECONDS = 300
# Cached for less than the URL's real validity window, not the full 300s — a
# URL served right before it's evicted from cache should still have a
# reasonable amount of life left, not expire moments after the client gets it.
SIGNED_URL_CACHE_TTL_SECONDS = DOWNLOAD_URL_EXPIRES_SECONDS - 30
signed_url_cache = TTLCache(ttl_seconds=SIGNED_URL_CACHE_TTL_SECONDS)


app = FastAPI(title="DocumentOS AI Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
app.include_router(tools_router)
app.include_router(editor_router)
app.include_router(copilot_router)
app.include_router(analytics_router)
app.include_router(signature_router)
app.include_router(templates_router)


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    """Tags every log line and every Sentry event produced while handling this
    request with the same request_id (see logging_config.py), and logs one
    structured line per request — method/path/status/duration — replacing
    uvicorn's own differently-shaped access log (silenced in configure_logging())."""
    request_id = str(uuid.uuid4())
    token = request_id_var.set(request_id)
    sentry_sdk.set_tag("request_id", request_id)
    start = time.monotonic()
    try:
        # The completion log below must run before the finally block resets
        # request_id_var — otherwise it would log request_id="-" for every
        # single successful request, defeating the whole point of tagging them.
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("Unhandled exception handling %s %s", request.method, request.url.path)
            raise

        duration_ms = round((time.monotonic() - start) * 1000, 2)
        logger.info(
            "%s %s -> %s (%sms)",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        request_id_var.reset(token)

ALLOWED_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}
MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/auth/register", response_model=Token)
def register(payload: UserCreate, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing is not None:
        raise HTTPException(status_code=400, detail="Email is already registered.")
    password_error = validate_password_strength(payload.password)
    if password_error:
        raise HTTPException(status_code=400, detail=password_error)

    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        name=payload.name,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return Token(access_token=create_access_token(subject=str(user.id), token_version=user.token_version))


@app.post("/auth/login", response_model=Token)
@limiter.limit("5/minute")
def login(request: Request, response: Response, payload: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect email or password.")

    return Token(access_token=create_access_token(subject=str(user.id), token_version=user.token_version))


@app.get("/auth/me", response_model=UserOut)
def read_current_user(current_user: User = Depends(get_current_user)):
    return current_user


@app.patch("/auth/me", response_model=UserOut)
def update_preferences(
    payload: UserPreferencesUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    current_user.due_soon_threshold_days = payload.due_soon_threshold_days
    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    return current_user


@app.patch("/auth/profile", response_model=UserOut)
def update_profile(
    payload: UserProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Deliberately a separate endpoint from PATCH /auth/me (preferences) — see
    # CLAUDE.md's Settings section: identity fields (name/email) live on the
    # Account tab, due_soon_threshold_days on the App tab, and keeping them on
    # separate routes matches that split instead of one endpoint doing both.
    existing = (
        db.query(User).filter(User.email == payload.email, User.id != current_user.id).first()
    )
    if existing is not None:
        raise HTTPException(status_code=400, detail="Email is already registered.")

    current_user.name = payload.name
    current_user.email = payload.email
    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    return current_user


@app.post("/auth/change-password", status_code=204)
def change_password(
    payload: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect.")
    password_error = validate_password_strength(payload.new_password)
    if password_error:
        raise HTTPException(status_code=400, detail=password_error)

    current_user.hashed_password = hash_password(payload.new_password)
    # Invalidates every token issued before this change (see auth.py's
    # get_current_user "tv" claim check) — a real gap found in a security
    # audit: a stolen token previously stayed valid for its full ~24h natural
    # lifetime even after the legitimate user changed their password in
    # response to the compromise. The request making this very call already
    # authenticated successfully before this line runs, so it isn't affected;
    # only tokens issued before now (including this one, for any *future*
    # request) are rejected from here on.
    current_user.token_version += 1
    db.add(current_user)
    db.commit()
    return Response(status_code=204)


@app.delete("/auth/me", status_code=204)
def delete_account(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Documents are now owned (documents.user_id, see CLAUDE.md's Per-user
    # document isolation section) — deleting the account deletes everything it
    # owns too, matching how most SaaS "delete my account" flows work. Handled
    # explicitly here (Storage cleanup, then DB rows, then the user row) rather
    # than relying solely on the FK's ON DELETE CASCADE, which would silently
    # skip the Storage cleanup and leave orphaned objects in the bucket.
    owned_documents = db.query(Document).filter(Document.user_id == current_user.id).all()
    for document in owned_documents:
        try:
            delete_file_from_storage(document.storage_path)
        except Exception:
            logger.warning(
                "Storage delete failed for document %s during account deletion",
                document.id,
                exc_info=True,
            )
        db.delete(document)

    # signed_documents has no retention sweep (unlike tool_files — see
    # CLAUDE.md's E-signature section), so its Storage objects must be
    # cleaned up explicitly here or they'd be orphaned forever once the DB
    # row cascade-deletes with the user row.
    owned_signed_documents = db.query(SignedDocument).filter(SignedDocument.user_id == current_user.id).all()
    for signed_document in owned_signed_documents:
        try:
            delete_file_from_storage(signed_document.storage_path)
        except Exception:
            logger.warning(
                "Storage delete failed for signed document %s during account deletion",
                signed_document.id,
                exc_info=True,
            )
        db.delete(signed_document)

    # tool_files (see CLAUDE.md's Document tools section) normally rely on the
    # opportunistic retention sweep (cleanup_expired_tool_files) to delete
    # both the DB row and its Storage object once expires_at passes. That
    # sweep only ever looks at rows still IN the table — but tool_files.user_id
    # has ON DELETE CASCADE, so db.delete(current_user) below would silently
    # remove every one of this user's ToolFile rows at the DB level before the
    # sweep could ever see them again, permanently orphaning their Storage
    # objects (a real gap found in a security audit: "deletion" wasn't
    # actually deleting this data, just the row that pointed at it). Cleaned
    # up explicitly here, same pattern as documents/signed_documents above.
    owned_tool_files = db.query(ToolFile).filter(ToolFile.user_id == current_user.id).all()
    for tool_file in owned_tool_files:
        try:
            delete_file_from_storage(tool_file.storage_path)
        except Exception:
            logger.warning(
                "Storage delete failed for tool file %s during account deletion",
                tool_file.id,
                exc_info=True,
            )
        db.delete(tool_file)

    if current_user.signature_storage_path:
        try:
            delete_file_from_storage(current_user.signature_storage_path)
        except Exception:
            logger.warning(
                "Storage delete failed for signature during account deletion for user %s",
                current_user.id,
                exc_info=True,
            )

    record_audit_log(
        db,
        current_user,
        "delete_account",
        "account",
        current_user.id,
        detail=f"{len(owned_documents)} document(s), {len(owned_signed_documents)} signed document(s), {len(owned_tool_files)} tool file(s)",
    )
    db.delete(current_user)
    db.commit()
    if owned_documents:
        invalidate_document_list_caches()
    return Response(status_code=204)


@app.get("/documents", response_model=List[DocumentListOut])
def get_documents(
    category: Optional[str] = None,
    upload_date_from: Optional[date] = None,
    upload_date_to: Optional[date] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cache_key = (str(current_user.id), category, upload_date_from, upload_date_to)
    cached = documents_cache.get(cache_key)
    if cached is not None:
        return cached

    query = db.query(Document).filter(Document.user_id == current_user.id)
    if category:
        query = query.filter(Document.category == category)
    if upload_date_from:
        query = query.filter(Document.upload_date >= upload_date_from)
    if upload_date_to:
        query = query.filter(Document.upload_date < upload_date_to + timedelta(days=1))
    documents = [DocumentListOut.model_validate(doc) for doc in query.all()]
    documents_cache.set(cache_key, documents)
    return documents


@app.get("/documents/deadlines", response_model=List[DocumentListOut])
def get_document_deadlines(
    urgency: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if urgency is not None and urgency not in ("overdue", "due-soon", "all"):
        raise HTTPException(
            status_code=400, detail="urgency must be one of: overdue, due-soon, all."
        )

    cache_key = (str(current_user.id), urgency)
    cached = deadlines_cache.get(cache_key)
    if cached is not None:
        return cached

    query = db.query(Document).filter(
        Document.user_id == current_user.id, Document.deadline_date.isnot(None)
    )

    today = date.today()
    if urgency == "overdue":
        query = query.filter(Document.deadline_date < today)
    elif urgency == "due-soon":
        query = query.filter(
            Document.deadline_date >= today,
            Document.deadline_date <= today + timedelta(days=30),
        )

    documents = [
        DocumentListOut.model_validate(doc)
        for doc in query.order_by(Document.deadline_date.asc()).all()
    ]
    deadlines_cache.set(cache_key, documents)
    return documents


def _get_owned_document(db: Session, document_id: uuid.UUID, current_user: User) -> Document:
    """Shared by every single-document endpoint below. Filtering by id and
    user_id in the same query means a document belonging to someone else is
    indistinguishable from one that doesn't exist at all — a 404 either way,
    never a 403, so a document's existence isn't leaked to a user who doesn't
    own it (see CLAUDE.md's Per-user document isolation section)."""
    document = (
        db.query(Document)
        .filter(Document.id == document_id, Document.user_id == current_user.id)
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    return document


@app.get("/documents/{document_id}", response_model=DocumentOut)
def get_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    document = _get_owned_document(db, document_id, current_user)
    record_audit_log(db, current_user, "view", "document", document.id)
    return document


def _process_document_in_background(document_id: uuid.UUID) -> None:
    """Runs the OCR -> classify -> structure pipeline (processing.py) outside
    the upload request — see the Background processing section in CLAUDE.md for
    why and how this is wired up. Opens its own DB session rather than reusing
    the request's: the request's session (from Depends(get_db)) is closed once
    the response has been sent, and FastAPI's own docs warn against handing a
    request-scoped session to a background task for exactly that reason."""
    db = SessionLocal()
    try:
        document = db.query(Document).filter(Document.id == document_id).first()
        if document is None:
            # Deleted between the upload response being sent and this task
            # running — nothing left to process.
            logger.warning("Background processing skipped: document %s no longer exists", document_id)
            return
        process_document(document, db)
    except Exception:
        # process_document/classify_and_structure already catch and record
        # every expected failure mode (Storage/OCR/Groq errors) as a *_failed
        # status — this is a last-resort net for anything unexpected (e.g. a DB
        # connectivity issue) so a background task failure is never silently
        # lost, consistent with the rest of this app's error handling.
        logger.exception("Unexpected error in background processing for document %s", document_id)
    finally:
        db.close()
        invalidate_document_list_caches()


@app.post("/documents/upload", response_model=DocumentUploadOut)
@limiter.limit("20/hour", key_func=user_or_ip_key)
async def upload_document(
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    extension = Path(file.filename or "").suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Allowed: PDF, JPG, PNG, TIFF.",
        )

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(contents) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(status_code=400, detail="File exceeds the 20MB limit.")
    if not matches_declared_type(contents, extension):
        raise HTTPException(
            status_code=400,
            detail=(
                "File content doesn't match its extension. Please upload a genuine "
                "PDF, JPG, PNG, or TIFF file."
            ),
        )

    object_key = f"{uuid.uuid4()}{extension}"
    upload_file_to_storage(contents, object_key, content_type=ALLOWED_EXTENSIONS[extension])

    # status="uploaded" is now a real, observable state a client can see and
    # poll on (see CLAUDE.md's Background processing section) — previously it
    # existed only for an instant before the pipeline ran synchronously in the
    # same request.
    document = Document(
        filename=file.filename, storage_path=object_key, status="uploaded", user_id=current_user.id
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    # Runs after this response has been sent to the client (Starlette offloads
    # a plain `def` background task to a worker thread, so it doesn't block the
    # event loop from serving other requests meanwhile either) — see CLAUDE.md
    # for how this was verified.
    background_tasks.add_task(_process_document_in_background, document.id)
    invalidate_document_list_caches()

    return document


@app.delete("/documents/{document_id}", status_code=204)
def delete_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    document = _get_owned_document(db, document_id, current_user)
    document_filename = document.filename

    try:
        delete_file_from_storage(document.storage_path)
    except Exception:
        # best-effort — don't block removing the DB row over a storage hiccup
        logger.warning("Storage delete failed for document %s", document_id, exc_info=True)

    db.delete(document)
    db.commit()
    invalidate_document_list_caches()
    record_audit_log(db, current_user, "delete", "document", document_id, detail=document_filename)
    return Response(status_code=204)


@app.get("/documents/{document_id}/download-url", response_model=DownloadUrlOut)
def get_document_download_url(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    document = _get_owned_document(db, document_id, current_user)
    record_audit_log(db, current_user, "download", "document", document.id)

    cached = signed_url_cache.get(document_id)
    if cached is not None:
        return cached

    try:
        url = create_signed_url(document.storage_path, expires_in=DOWNLOAD_URL_EXPIRES_SECONDS)
    except Exception:
        logger.exception("Signed URL generation failed for document %s", document_id)
        raise HTTPException(status_code=502, detail="Could not generate a download link right now.")

    result = DownloadUrlOut(url=url, expires_in=DOWNLOAD_URL_EXPIRES_SECONDS)
    signed_url_cache.set(document_id, result)
    return result


@app.post("/documents/{document_id}/chat", response_model=ChatResponse)
@limiter.limit("20/hour", key_func=user_or_ip_key)
def chat_with_document(
    request: Request,
    response: Response,
    document_id: uuid.UUID,
    payload: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    document = _get_owned_document(db, document_id, current_user)
    if not payload.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")
    if not document.raw_text:
        raise HTTPException(
            status_code=400,
            detail="This document has no extracted text to answer questions about yet.",
        )

    try:
        answer, truncated = answer_question(document.raw_text, payload.question)
    except Exception:
        logger.exception("Chat answer failed for document %s", document_id)
        raise HTTPException(
            status_code=502, detail="Could not get an answer right now. Please try again."
        )

    return ChatResponse(answer=answer, truncated=truncated)


@app.post("/documents/{document_id}/reprocess", response_model=DocumentOut)
@limiter.limit("20/hour", key_func=user_or_ip_key)
def reprocess_document(
    request: Request,
    response: Response,
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    document = _get_owned_document(db, document_id, current_user)
    if not document.raw_text:
        raise HTTPException(
            status_code=400,
            detail="Document has no extracted text yet — re-upload it instead of reprocessing.",
        )

    result = classify_and_structure(document, db)
    invalidate_document_list_caches()
    return result
