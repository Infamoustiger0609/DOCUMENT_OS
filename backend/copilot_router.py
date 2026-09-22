"""POST /copilot/chat — the global, registry-wide assistant (Phase 25, see
CLAUDE.md's Copilot section). Unlike editor_router.py's /editor/chat, this
takes a plain JSON body (no files involved — it only ever reads the user's
existing documents, never touches Storage), so the endpoint stays a regular
sync `def` like most of this app's other simple JSON endpoints.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from auth import get_current_user
from copilot_chat import run_copilot_chat
from database import get_db
from models import User
from rate_limit import limiter, user_or_ip_key
from schemas import CopilotChatRequest, CopilotChatResponse, CopilotDocumentRef

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/copilot", tags=["copilot"])

MAX_MESSAGE_LENGTH = 2000
# Bounds how much prior conversation gets replayed to Groq on every turn —
# same spirit as editor_router.py's MAX_HISTORY_ENTRIES.
MAX_HISTORY_ENTRIES = 20
# Same "compute-heavy, multiple Groq round trips per call" justification as
# /tools/* and /editor/chat — see CLAUDE.md's Rate limiting section.
COPILOT_RATE_LIMIT = "20/hour"


@router.post("/chat", response_model=CopilotChatResponse)
@limiter.limit(COPILOT_RATE_LIMIT, key_func=user_or_ip_key)
def copilot_chat_endpoint(
    request: Request,
    response: Response,
    payload: CopilotChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
    if len(message) > MAX_MESSAGE_LENGTH:
        raise HTTPException(
            status_code=400, detail=f"Message is too long (max {MAX_MESSAGE_LENGTH} characters)."
        )

    history = [{"role": h.role, "content": h.content} for h in payload.history[-MAX_HISTORY_ENTRIES:]]

    try:
        result = run_copilot_chat(message, history, db, current_user)
    except Exception:
        logger.exception("Copilot chat failed for user %s", current_user.id)
        raise HTTPException(
            status_code=502, detail="Could not get a response right now. Please try again."
        )

    return CopilotChatResponse(
        reply=result.reply,
        citations=[CopilotDocumentRef.model_validate(doc) for doc in result.citations],
    )
