"""Shared plumbing for every /tools/* endpoint (tools_router.py): where an
output file lives in Supabase Storage, how a ToolFile row gets created, and
the retention sweep that deletes both once expires_at has passed. See
CLAUDE.md's Document tools section for the full design and why this is a
separate area from the main documents table/bucket prefix."""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from config import TOOLS_RETENTION_HOURS
from models import ToolFile, User
from storage import delete_file_from_storage, upload_file_to_storage

logger = logging.getLogger(__name__)

# Separate Supabase Storage prefix from the main documents bucket's flat
# {uuid}{ext} keys — keeps tool outputs visibly distinct and lets a bucket
# listing/lifecycle rule target just this prefix later if needed.
TOOLS_STORAGE_PREFIX = "tool-outputs"

# Shared by tools_router.py's direct /tools/* endpoints and editor_router.py's
# /editor/chat (Phase 23) — larger than main.py's MAX_FILE_SIZE_BYTES (20MB)
# since these tools exist specifically to handle large files (compress-pdf's
# whole purpose is shrinking a big PDF).
TOOLS_MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024


def _build_tool_storage_key(user_id: uuid.UUID, output_filename: str) -> str:
    """The storage KEY must never embed user-controlled filename content
    directly. Verified live against the real Supabase Storage project: a key
    containing "../" segments is genuinely resolved (the object lands outside
    the intended prefix, not treated as an opaque literal string) — so the old
    scheme (uuid4()_{output_filename}, where output_filename is derived from
    the uploaded file's own name, e.g. "resized_{file.filename}") let a
    crafted upload filename write outside this user's own tool-outputs/
    prefix. Only the extension is taken from output_filename here — and
    Path.suffix is itself safe to use for this even on a hostile input,
    since it only ever returns the last path segment's trailing extension
    (never a string containing "/"), regardless of how many "../" segments
    precede it. The rest of the key is a fresh UUID; output_filename is still
    stored as-is in ToolFile.output_filename for display/download purposes,
    it's just never used to build a Storage path anymore."""
    extension = Path(output_filename).suffix
    return f"{TOOLS_STORAGE_PREFIX}/{user_id}/{uuid.uuid4()}{extension}"


def save_tool_output(
    db: Session,
    current_user: User,
    tool_name: str,
    original_filename: str,
    output_filename: str,
    data: bytes,
    mime_type: str,
) -> ToolFile:
    """Uploads `data` to this user's tool-outputs prefix and records it as a
    ToolFile row with an expiry TOOLS_RETENTION_HOURS from now. Called once per
    output file — split-pdf calls this once per page/range produced."""
    storage_path = _build_tool_storage_key(current_user.id, output_filename)
    upload_file_to_storage(data, storage_path, content_type=mime_type)

    tool_file = ToolFile(
        user_id=current_user.id,
        tool_name=tool_name,
        original_filename=original_filename,
        output_filename=output_filename,
        storage_path=storage_path,
        mime_type=mime_type,
        size_bytes=len(data),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=TOOLS_RETENTION_HOURS),
    )
    db.add(tool_file)
    db.commit()
    db.refresh(tool_file)
    return tool_file


def get_owned_tool_file(db: Session, tool_file_id: uuid.UUID, current_user: User) -> ToolFile:
    """Same pattern as main.py's _get_owned_document: filtering by id, user_id,
    AND expires_at in one query means someone else's file, a nonexistent id,
    and an already-expired file are all indistinguishable — a 404 either way."""
    tool_file = (
        db.query(ToolFile)
        .filter(
            ToolFile.id == tool_file_id,
            ToolFile.user_id == current_user.id,
            ToolFile.expires_at >= datetime.now(timezone.utc),
        )
        .first()
    )
    if tool_file is None:
        raise HTTPException(status_code=404, detail="Tool output not found or has expired.")
    return tool_file


def cleanup_expired_tool_files(db: Session, limit: int = 50) -> int:
    """Opportunistic retention sweep, run as a dependency on every /tools/*
    request (see tools_router.py) so the table and Storage prefix stay roughly
    bounded without a real task scheduler — Render's free tier has none (same
    constraint noted throughout CLAUDE.md for BackgroundTasks/slowapi/caching).
    Capped at `limit` rows per call so one request never pays for an unbounded
    sweep. scripts/cleanup_tool_files.py runs an uncapped sweep for when
    there's no traffic to trigger this lazily (e.g. an idle weekend)."""
    now = datetime.now(timezone.utc)
    expired = (
        db.query(ToolFile)
        .filter(ToolFile.expires_at < now)
        .order_by(ToolFile.expires_at.asc())
        .limit(limit)
        .all()
    )
    for tool_file in expired:
        try:
            delete_file_from_storage(tool_file.storage_path)
        except Exception:
            logger.warning(
                "Storage delete failed for expired tool file %s", tool_file.id, exc_info=True
            )
        db.delete(tool_file)
    if expired:
        db.commit()
    return len(expired)
