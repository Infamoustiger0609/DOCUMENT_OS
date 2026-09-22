"""POST /editor/chat — the natural-language layer over the /tools/* endpoints
(Phase 23, see CLAUDE.md's Editor assistant section). This router only owns
request validation and translating the HTTP request into editor_chat.py's
ChatFileContext; the actual Groq tool-calling loop and tool execution live
there, reusing the exact same transform functions and tools_common.save_tool_output
as tools_router.py's direct endpoints — a file the assistant produces is a
completely ordinary ToolFile row.
"""

import json
import logging
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from editor_chat import ChatFileContext, run_editor_chat
from file_validation import MAGIC_SIGNATURES, matches_declared_type
from models import ToolFile, User
from rate_limit import limiter, user_or_ip_key
from schemas import EditorChatResponse, EditorChatToolRun, ToolFileOut
from tools_common import TOOLS_MAX_FILE_SIZE_BYTES, cleanup_expired_tool_files

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/editor", tags=["editor"])

ALLOWED_EXTENSIONS = set(MAGIC_SIGNATURES.keys())
MAX_FILES_PER_MESSAGE = 15
MAX_MESSAGE_LENGTH = 2000
# Bounds how much prior conversation gets replayed to Groq on every turn —
# same spirit as chat.py's MAX_CHARS truncation for document Q&A, just
# counted in turns instead of characters since history is structured, not raw text.
MAX_HISTORY_ENTRIES = 20
# Same compute-heavy justification as /tools/* (see CLAUDE.md's Rate limiting
# section) — if anything more so, since one chat turn can trigger several
# chained tool calls (each its own Ghostscript/LibreOffice/OCR subprocess)
# plus multiple Groq round trips.
EDITOR_CHAT_RATE_LIMIT = "20/hour"


def _parse_history(raw: Optional[str]) -> List[dict]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid history payload.")
    if not isinstance(parsed, list):
        raise HTTPException(status_code=400, detail="Invalid history payload.")

    cleaned = []
    for entry in parsed:
        if (
            isinstance(entry, dict)
            and entry.get("role") in ("user", "assistant")
            and isinstance(entry.get("content"), str)
        ):
            cleaned.append({"role": entry["role"], "content": entry["content"]})
    return cleaned[-MAX_HISTORY_ENTRIES:]


def _parse_context_tool_files(raw: Optional[str], db: Session, current_user: User) -> dict:
    """Resolves {id, filename} references from earlier turns in this
    conversation back to their ToolFile rows (scoped to this user, and only if
    still unexpired — get_owned_tool_file's same filter, inlined here since we
    need to silently skip a stale ref rather than 404 the whole chat request
    over one expired file)."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid context_tool_files payload.")
    if not isinstance(parsed, list):
        raise HTTPException(status_code=400, detail="Invalid context_tool_files payload.")

    refs: dict = {}
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        try:
            tool_file_id = uuid.UUID(str(entry.get("id")))
        except (ValueError, TypeError):
            continue
        tool_file = (
            db.query(ToolFile)
            .filter(ToolFile.id == tool_file_id, ToolFile.user_id == current_user.id)
            .first()
        )
        if tool_file is not None:
            refs[tool_file.output_filename] = tool_file
    return refs


@router.post("/chat", response_model=EditorChatResponse)
@limiter.limit(EDITOR_CHAT_RATE_LIMIT, key_func=user_or_ip_key)
async def editor_chat_endpoint(
    request: Request,
    response: Response,
    message: str = Form(...),
    files: List[UploadFile] = File(default=[]),
    history: Optional[str] = Form(None),
    context_tool_files: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cleanup_expired_tool_files(db)

    message = message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
    if len(message) > MAX_MESSAGE_LENGTH:
        raise HTTPException(
            status_code=400, detail=f"Message is too long (max {MAX_MESSAGE_LENGTH} characters)."
        )
    if len(files) > MAX_FILES_PER_MESSAGE:
        raise HTTPException(status_code=400, detail=f"Too many files (max {MAX_FILES_PER_MESSAGE}).")

    uploaded: dict = {}
    for f in files:
        filename = f.filename or "unnamed"
        extension = Path(filename).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"{filename}: unsupported file type.")
        contents = await f.read()
        if not contents:
            raise HTTPException(status_code=400, detail=f"{filename}: file is empty.")
        if len(contents) > TOOLS_MAX_FILE_SIZE_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"{filename}: exceeds the {TOOLS_MAX_FILE_SIZE_BYTES // (1024 * 1024)}MB limit.",
            )
        if not matches_declared_type(contents, extension):
            raise HTTPException(status_code=400, detail=f"{filename}: content doesn't match its extension.")
        uploaded[filename] = contents

    parsed_history = _parse_history(history)
    context_refs = _parse_context_tool_files(context_tool_files, db, current_user)

    ctx = ChatFileContext(current_user=current_user, db=db, uploaded=uploaded, context_refs=context_refs)

    try:
        result = run_editor_chat(message, parsed_history, ctx)
    except Exception:
        logger.exception("Editor chat failed for user %s", current_user.id)
        raise HTTPException(
            status_code=502, detail="Could not get a response right now. Please try again."
        )

    return EditorChatResponse(
        reply=result.reply,
        tool_runs=[
            EditorChatToolRun(
                tool_name=run.tool_name,
                files=[ToolFileOut.model_validate(tf) for tf in run.tool_files],
                error=run.error,
            )
            for run in result.tool_runs
        ],
    )
