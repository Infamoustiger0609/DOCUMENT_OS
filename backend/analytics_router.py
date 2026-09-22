"""GET /analytics/schema-summary + POST /analytics/generate (Phase 28, see
CLAUDE.md's Analytics section). The router owns request handling and the
10-minute per-user cache; analytics.py owns the schema introspection, the
whitelisted aggregation tools, and the agentic loop that decides what to show.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from analytics import compute_schema_summary, fill_missing_category_metrics, generate_analytics
from auth import get_current_user
from cache import TTLCache
from database import get_db
from models import User
from rate_limit import limiter, user_or_ip_key
from schemas import AnalyticsGenerateResponse, AnalyticsSchemaSummary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analytics", tags=["analytics"])

# Longer than the other in-process caches (documents_cache/deadlines_cache are
# 60s — see CLAUDE.md's Caching section) since generating this is meaningfully
# more expensive: several sequential Groq round trips per generation, not one
# cheap DB query. Keyed by user id, same reasoning as every other cache in
# this app — see CLAUDE.md's Caching section.
ANALYTICS_CACHE_TTL_SECONDS = 600
analytics_cache = TTLCache(ttl_seconds=ANALYTICS_CACHE_TTL_SECONDS)

# Same "compute-heavy, multiple Groq round trips" justification as /tools/*,
# /editor/chat, and /copilot/chat — see CLAUDE.md's Rate limiting section.
ANALYTICS_RATE_LIMIT = "20/hour"


@router.get("/schema-summary", response_model=AnalyticsSchemaSummary)
def get_schema_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return compute_schema_summary(db, current_user)


@router.post("/generate", response_model=AnalyticsGenerateResponse)
@limiter.limit(ANALYTICS_RATE_LIMIT, key_func=user_or_ip_key)
def generate_analytics_endpoint(
    request: Request,
    response: Response,
    force: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    cache_key = str(current_user.id)
    if not force:
        cached = analytics_cache.get(cache_key)
        if cached is not None:
            return AnalyticsGenerateResponse(metrics=cached.metrics, generated_at=cached.generated_at, cached=True)

    schema_summary = compute_schema_summary(db, current_user)
    try:
        result = generate_analytics(schema_summary, db, current_user)
        payload = AnalyticsGenerateResponse(
            metrics=result.metrics, generated_at=datetime.now(timezone.utc), cached=False
        )
        analytics_cache.set(cache_key, payload)
        return payload
    except Exception:
        # Deliberately NOT cached — a transient Groq hiccup (e.g. a rate
        # limit, seen live while verifying this exact fallback — see
        # CLAUDE.md's Analytics section) shouldn't lock the user out of a
        # real result for the full 10-minute TTL; let the next request (a
        # page reload, or Regenerate) try again immediately once Groq
        # recovers, rather than being stuck seeing this fallback for the
        # rest of the cache window. Still falls back to real per-category
        # document counts (fill_missing_category_metrics, given an empty
        # starting list) rather than an empty page — a total Groq failure is
        # the same "show real data anyway" case as generate_analytics()'s own
        # internal fallback paths, just one level further out.
        logger.exception("Analytics generation failed for user %s", current_user.id)
        metrics = fill_missing_category_metrics([], schema_summary)
        return AnalyticsGenerateResponse(metrics=metrics, generated_at=datetime.now(timezone.utc), cached=False)
