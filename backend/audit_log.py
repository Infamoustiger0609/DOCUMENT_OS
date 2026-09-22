"""Records who accessed/downloaded/deleted/signed/generated which document,
when (security audit finding — see CLAUDE.md's Security section). A thin,
deliberately dumb wrapper: one function, called from the handful of
endpoints that touch a specific document/signed-document/tool-file/template,
right after the ownership check has already succeeded (so a 404 for someone
else's resource is never itself logged as an access).

Best-effort, same convention as this app's own Storage-cleanup calls
elsewhere: a failure to write the audit row must never fail the real
request it's describing.
"""

import logging
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from models import AuditLog, User

logger = logging.getLogger(__name__)


def record(
    db: Session,
    user: User,
    action: str,
    resource_type: str,
    resource_id: Optional[uuid.UUID] = None,
    detail: Optional[str] = None,
) -> None:
    try:
        db.add(
            AuditLog(
                user_id=user.id,
                user_email=user.email,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                detail=detail,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.warning(
            "Audit log write failed (user=%s action=%s resource_type=%s resource_id=%s)",
            user.id,
            action,
            resource_type,
            resource_id,
            exc_info=True,
        )
