"""Phase 28 — the Analytics page's backend logic (see CLAUDE.md's Analytics
section). Two things live here, deliberately kept separate:

1. `compute_schema_summary()` — inspects the current user's REAL stored
   `extracted_json` data and reports which categories have documents and
   which fields are actually populated in each, with an inferred type
   (numeric/string/boolean/date/array/object). This is derived empirically
   from real rows every time, never from a fixed schema constant — a field
   that's never populated (or a category with no documents) simply doesn't
   appear.

2. The Groq function-calling tools and agentic loop that decide which
   metrics to show. The core safety property, worth stating plainly: **the
   model never executes a query of any kind.** It can only call one of three
   narrow, parameterized Python functions (`aggregate_numeric_field`,
   `group_and_count`, `date_field_breakdown`), each of which re-validates its
   `category`/`field_name` arguments against the schema summary before
   touching the database at all — an unlisted or wrongly-typed field is
   rejected before any query runs. There is no raw SQL, no AI-generated query
   string, and no code path where a model-supplied value is ever interpolated
   into a query. Every proposed metric's `value` is additionally checked
   server-side (`_validate_metrics`) against the actual numbers those tool
   calls returned this run — a value that doesn't trace back to a real tool
   result is dropped, not trusted on the model's word alone.
"""

import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field as dc_field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from groq import Groq, RateLimitError
from sqlalchemy import func
from sqlalchemy.orm import Session

from config import GROQ_API_KEY
from models import Document, User

logger = logging.getLogger(__name__)

MODEL = "openai/gpt-oss-120b"
MAX_RETRIES = 3
# Was 6 — with 3+ real categories each offering several numeric/date/boolean
# fields, the model can come close to exhausting 6 rounds exploring just one
# or two categories' richer numeric fields before ever touching a sparser one
# (see the coverage bug this was raised alongside, below). A little extra
# headroom gives it a better chance to reach every category on its own,
# though fill_missing_category_metrics() is the actual guarantee, not this.
MAX_TOOL_ROUNDS = 8
MAX_GROUPS = 15
MAX_METRICS = 8

client = Groq(api_key=GROQ_API_KEY)

FieldType = str  # "numeric" | "string" | "boolean" | "date" | "array" | "object"


# ---------------------------------------------------------------------------
# Schema summary — "what data do we actually have"
# ---------------------------------------------------------------------------


def _looks_like_date(value: Any) -> Optional[date]:
    """Same shape as structured_extraction.get_deadline_date's own date
    parsing (value[:10] as YYYY-MM-DD), reused here so a field counts as
    "date" type by the same rule the rest of the app already uses for
    deadline_date — generalized to any field, not just the one each
    category's schema happens to map to deadline_date."""
    if not isinstance(value, str) or not (10 <= len(value) <= 30):
        return None
    if value[10:11] not in ("", "T", " "):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _infer_type(value: Any) -> FieldType:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "numeric"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, str) and _looks_like_date(value) is not None:
        return "date"
    return "string"


def compute_schema_summary(db: Session, user: User) -> Dict[str, Any]:
    total_documents = db.query(Document).filter(Document.user_id == user.id).count()

    # Every document per category (including ones never structured-extracted
    # yet) — so the AI can see a category "exists" even before every document
    # in it has real extracted fields.
    document_counts_by_category = dict(
        db.query(Document.category, func.count(Document.id))
        .filter(Document.user_id == user.id, Document.category.isnot(None))
        .group_by(Document.category)
        .all()
    )

    rows = (
        db.query(Document.category, Document.extracted_json)
        .filter(Document.user_id == user.id, Document.extracted_json.isnot(None))
        .all()
    )

    # category -> field name -> {type: occurrence_count, ..., "populated": n}
    field_stats: Dict[str, Dict[str, Dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    structured_counts: Dict[str, int] = defaultdict(int)

    for category, extracted in rows:
        if not category or not isinstance(extracted, dict) or extracted.get("parse_error"):
            continue
        structured_counts[category] += 1
        for key, value in extracted.items():
            if value is None:
                continue
            stats = field_stats[category][key]
            stats["populated"] += 1
            stats[_infer_type(value)] += 1

    categories_summary = []
    for category, fields in field_stats.items():
        field_list = []
        for name, stats in fields.items():
            populated = stats.get("populated", 0)
            if populated == 0:
                continue
            inferred = max(("numeric", "string", "boolean", "date", "array", "object"), key=lambda t: stats.get(t, 0))
            field_list.append({"name": name, "type": inferred, "populated_count": populated})
        field_list.sort(key=lambda f: f["name"])
        categories_summary.append(
            {
                "category": category,
                "document_count": document_counts_by_category.get(category, structured_counts[category]),
                "structured_document_count": structured_counts[category],
                "fields": field_list,
            }
        )

    categories_summary.sort(key=lambda c: c["category"])
    return {"total_documents": total_documents, "categories": categories_summary}


def _find_category(schema_summary: Dict[str, Any], category: str) -> Optional[Dict[str, Any]]:
    return next((c for c in schema_summary["categories"] if c["category"] == category), None)


def _find_field(category_entry: Dict[str, Any], field_name: str) -> Optional[Dict[str, Any]]:
    return next((f for f in category_entry["fields"] if f["name"] == field_name), None)


class AnalyticsToolError(Exception):
    """A recoverable tool-argument problem (unknown category/field, wrong
    type, no populated values) — message is safe to feed back to the model as
    the tool's result."""


def _validate_field(schema_summary: Dict[str, Any], category: str, field_name: str, allowed_types: Set[str]) -> Dict[str, Any]:
    category_entry = _find_category(schema_summary, category)
    if category_entry is None:
        raise AnalyticsToolError(f"'{category}' has no documents in this registry.")
    field_entry = _find_field(category_entry, field_name)
    if field_entry is None:
        raise AnalyticsToolError(
            f"'{field_name}' is not a real, populated field for {category} in this registry's schema summary."
        )
    if field_entry["type"] not in allowed_types:
        raise AnalyticsToolError(
            f"'{field_name}' is a {field_entry['type']} field — not usable for this operation."
        )
    return field_entry


# ---------------------------------------------------------------------------
# The 3 real aggregation tools — plain Python over already-fetched rows, never
# a query string built from model input. `field_name`/`category` are only ever
# used as dict-key lookups after _validate_field has confirmed they're real.
# ---------------------------------------------------------------------------


def _fetch_structured_rows(db: Session, user: User, category: str) -> List[Dict[str, Any]]:
    docs = (
        db.query(Document.extracted_json)
        .filter(Document.user_id == user.id, Document.category == category, Document.extracted_json.isnot(None))
        .all()
    )
    return [d[0] for d in docs if isinstance(d[0], dict) and not d[0].get("parse_error")]


def _aggregate_numeric_field(db: Session, user: User, schema_summary: Dict[str, Any], args: Dict[str, Any]) -> Tuple[dict, List[float]]:
    category = str(args.get("category", ""))
    field_name = str(args.get("field_name", ""))
    operation = str(args.get("operation", ""))
    _validate_field(schema_summary, category, field_name, {"numeric"})
    if operation not in ("sum", "avg", "count", "min", "max"):
        raise AnalyticsToolError(f"'{operation}' isn't a supported operation (sum|avg|count|min|max).")

    values = [
        float(row[field_name])
        for row in _fetch_structured_rows(db, user, category)
        if isinstance(row.get(field_name), (int, float)) and not isinstance(row.get(field_name), bool)
    ]
    if not values:
        raise AnalyticsToolError(f"No populated numeric values found for '{field_name}' in {category}.")

    if operation == "sum":
        result = sum(values)
    elif operation == "avg":
        result = sum(values) / len(values)
    elif operation == "count":
        result = float(len(values))
    elif operation == "min":
        result = min(values)
    else:
        result = max(values)
    result = round(result, 2)

    content = {
        "category": category,
        "field_name": field_name,
        "operation": operation,
        "result": result,
        "sample_size": len(values),
    }
    return content, [result]


def _group_and_count(db: Session, user: User, schema_summary: Dict[str, Any], args: Dict[str, Any]) -> Tuple[dict, List[float]]:
    category = str(args.get("category", ""))
    field_name = str(args.get("field_name", ""))
    _validate_field(schema_summary, category, field_name, {"numeric", "string", "boolean"})

    counts: Dict[str, int] = defaultdict(int)
    total = 0
    for row in _fetch_structured_rows(db, user, category):
        value = row.get(field_name)
        if value is None:
            continue
        counts[str(value)] += 1
        total += 1

    if total == 0:
        raise AnalyticsToolError(f"No populated values found for '{field_name}' in {category}.")

    groups = [
        {"value": value, "count": count, "percent": round(count / total * 100, 1)}
        for value, count in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    ][:MAX_GROUPS]

    numbers = [float(g["count"]) for g in groups] + [g["percent"] for g in groups] + [float(total)]
    content = {"category": category, "field_name": field_name, "total": total, "groups": groups}
    return content, numbers


def _date_field_breakdown(db: Session, user: User, schema_summary: Dict[str, Any], args: Dict[str, Any]) -> Tuple[dict, List[float]]:
    category = str(args.get("category", ""))
    date_field_name = str(args.get("date_field_name", ""))
    _validate_field(schema_summary, category, date_field_name, {"date"})

    today = date.today()
    threshold_days = user.due_soon_threshold_days
    buckets = {"overdue": 0, "due_soon": 0, "future": 0, "no_date": 0}

    for row in _fetch_structured_rows(db, user, category):
        parsed = _looks_like_date(row.get(date_field_name))
        if parsed is None:
            buckets["no_date"] += 1
        elif parsed < today:
            buckets["overdue"] += 1
        elif parsed <= today + timedelta(days=threshold_days):
            buckets["due_soon"] += 1
        else:
            buckets["future"] += 1

    content = {
        "category": category,
        "date_field_name": date_field_name,
        "threshold_days": threshold_days,
        "buckets": buckets,
    }
    numbers = [float(v) for v in buckets.values()]
    return content, numbers


def _execute_tool(name: str, args: Dict[str, Any], db: Session, user: User, schema_summary: Dict[str, Any]) -> Tuple[dict, List[float]]:
    try:
        if name == "aggregate_numeric_field":
            return _aggregate_numeric_field(db, user, schema_summary, args)
        if name == "group_and_count":
            return _group_and_count(db, user, schema_summary, args)
        if name == "date_field_breakdown":
            return _date_field_breakdown(db, user, schema_summary, args)
        raise AnalyticsToolError(f"'{name}' isn't a tool I have available.")
    except AnalyticsToolError as exc:
        return {"error": str(exc)}, []
    except Exception:
        logger.exception("Analytics tool '%s' failed", name)
        return {"error": f"Running '{name}' failed unexpectedly."}, []


# ---------------------------------------------------------------------------
# Tool + system prompt definitions for the agentic loop
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "aggregate_numeric_field",
            "description": (
                "Compute a real aggregate (sum, average, count, min, or max) over a numeric field "
                "of one document category's extracted data. `field_name` MUST be one of the "
                "numeric fields reported for that category in the schema summary you were given — "
                "anything else is rejected before any data is touched."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "A category from the schema summary."},
                    "field_name": {"type": "string", "description": "A numeric field from that category's schema summary."},
                    "operation": {"type": "string", "enum": ["sum", "avg", "count", "min", "max"]},
                },
                "required": ["category", "field_name", "operation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "group_and_count",
            "description": (
                "Count documents in one category grouped by the distinct values of one field (e.g. "
                "group Invoice by vendor_name, or Agreement by auto_renewal). `field_name` MUST be a "
                "field from that category's schema summary (numeric, string, or boolean type — not "
                "an array/object field like line_items). The result already includes each group's "
                "count AND its percent of the category total — use those numbers directly rather "
                "than computing your own percentage."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "field_name": {"type": "string"},
                },
                "required": ["category", "field_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "date_field_breakdown",
            "description": (
                "Break down one date-typed field of one category into overdue / due_soon / future / "
                "no_date counts, using the user's own configured due-soon threshold — the same "
                "bucketing logic the Tasks page and Copilot's get_deadline_summary already use, "
                "generalized here to any date field, not just deadline_date. `date_field_name` MUST "
                "be a date-type field from that category's schema summary."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "date_field_name": {"type": "string"},
                },
                "required": ["category", "date_field_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_metrics",
            "description": (
                "Submit the final 4-8 metrics to display on the Analytics page. Call this exactly "
                "once, as your last action, after gathering real numbers via the tools above. Every "
                "`value` must be a number one of those tool calls actually returned this session "
                "(a sum/avg/count/min/max result, a group's count or percent, or a date "
                "breakdown's bucket count) — copied exactly, never estimated, rounded differently, "
                "or computed by you. A value that doesn't match an actual tool result will be "
                "discarded, so don't guess."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "metrics": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": MAX_METRICS,
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string", "description": "Short, human-readable label, e.g. 'Average invoice value'."},
                                "value": {"type": "number"},
                                "format": {"type": "string", "enum": ["currency", "count", "percent"]},
                                "category": {"type": "string", "description": "The document category this metric is about."},
                            },
                            "required": ["label", "value", "format", "category"],
                        },
                    }
                },
                "required": ["metrics"],
            },
        },
    },
]

SYSTEM_PROMPT = """You generate the Analytics page for DocumentOS — a set of real, data-driven \
metrics about the user's own document registry. You've been given a schema summary (as JSON) of \
what categories and fields ACTUALLY exist in their real stored data.

Rules:
- Only propose a metric for a category that actually has documents, using a field that actually \
appears (and is populated) in that category's entry in the schema summary — never invent a field \
or category that isn't listed there. If Agreement has no `auto_renewal` field in the summary, \
don't propose an auto-renewal metric; if no category has anything resembling a payment-status \
field, don't propose a paid/pending split — that would be inventing data that doesn't exist.
- **Every category listed in the schema summary must get at least one metric in your final \
proposal — this is a hard requirement, not a preference.** Work through the categories in the \
order they appear in the summary and make at least one tool call for each before calling \
propose_metrics, even a category whose only usable fields are a single boolean or date (e.g. \
Agreement's `auto_renewal` or `expiry_date`) rather than a rich numeric one — a category with \
fewer/less "interesting" fields is not lower priority than one with more. Do not spend your \
whole tool budget on one or two numeric-rich categories and skip a sparser one.
- Every metric's value must come from an actual call to aggregate_numeric_field, group_and_count, \
or date_field_breakdown — never a number you computed or estimated yourself. Make the tool calls \
first, then call propose_metrics with the real results.
- Choose 4-8 metrics that are genuinely meaningful given what data exists, sticking to the \
per-category coverage requirement above — favor variety across categories over several \
near-duplicate metrics on the same field.
- Use "currency" format for money amounts, "count" for plain counts, "percent" for a \
group_and_count group's own percent value.
- Call propose_metrics exactly once, as your last action. If the schema summary has nothing \
usable (no categories, or no populated fields anywhere), call it with an empty metrics array \
rather than inventing something.
"""


@dataclass
class AnalyticsResult:
    metrics: List[Dict[str, Any]] = dc_field(default_factory=list)


def _call_groq(messages: List[Dict[str, Any]]):
    return client.chat.completions.create(
        model=MODEL,
        messages=messages,
        tools=TOOL_DEFINITIONS,
        tool_choice="auto",
        temperature=0,
        max_tokens=1200,
        reasoning_effort="low",
    )


def _call_groq_with_retry(messages: List[Dict[str, Any]]):
    for attempt in range(MAX_RETRIES + 1):
        try:
            return _call_groq(messages)
        except RateLimitError:
            if attempt == MAX_RETRIES:
                raise
            time.sleep(2**attempt)


def _validate_metrics(raw_metrics: Any, known_values: Dict[str, Set[float]]) -> List[Dict[str, Any]]:
    validated: List[Dict[str, Any]] = []
    if not isinstance(raw_metrics, list):
        return validated

    for item in raw_metrics[:MAX_METRICS]:
        if not isinstance(item, dict):
            continue
        try:
            label = str(item["label"]).strip()
            value = float(item["value"])
            fmt = str(item["format"])
            category = str(item["category"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if not label or fmt not in ("currency", "count", "percent"):
            continue

        candidates = known_values.get(category, set())
        # A value must trace back to an actual tool result for that category —
        # small tolerance only for float round-tripping through JSON, not for
        # a materially different (i.e. invented) number.
        if not any(abs(value - known) <= max(0.01, abs(known) * 0.005) for known in candidates):
            logger.warning(
                "Analytics: dropped an unverifiable metric %r (value=%s, category=%r) — no matching tool result",
                label, value, category,
            )
            continue
        validated.append({"label": label, "value": value, "format": fmt, "category": category})

    return validated


def fill_missing_category_metrics(
    metrics: List[Dict[str, Any]], schema_summary: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Guarantees every category with real documents ends up with at least
    one metric, regardless of which categories the model's own tool-calling
    exploration happened to reach this run.

    This exists because the agentic loop's category coverage is NOT reliably
    deterministic in practice — verified directly: 4 of 5 real back-to-back
    runs against the same real schema summary (temperature=0 notwithstanding)
    each omitted a different category entirely (Purchase Order in most runs,
    Agreement in one) after spending most of their tool-call budget on one or
    two categories with richer numeric fields. The system prompt asks the
    model to cover every category, but a prompt is a request, not a
    guarantee — this is the actual, code-enforced backstop, same "verify in
    code, don't just trust the model's compliance" philosophy as this
    module's own known_values check in _validate_metrics.

    Uses `document_count`, already present in schema_summary — no extra
    Groq call or DB query needed, so this never costs anything even when
    every category was already covered (the common case)."""
    covered = {m["category"] for m in metrics}
    filled = list(metrics)
    for category_entry in schema_summary.get("categories", []):
        category = category_entry.get("category")
        count = category_entry.get("document_count", 0)
        if not category or category in covered or count <= 0:
            continue
        filled.append(
            {
                "label": f"{category} documents",
                "value": float(count),
                "format": "count",
                "category": category,
            }
        )
    return filled


def generate_analytics(schema_summary: Dict[str, Any], db: Session, user: User) -> AnalyticsResult:
    if not schema_summary.get("categories"):
        # Nothing to work with — don't spend a Groq call finding that out.
        return AnalyticsResult(metrics=[])

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Schema summary (real data, JSON):\n{json.dumps(schema_summary)}"},
    ]

    known_values: Dict[str, Set[float]] = defaultdict(set)
    tool_calls_made = 0

    for _ in range(MAX_TOOL_ROUNDS):
        response = _call_groq_with_retry(messages)
        assistant_message = response.choices[0].message

        if not assistant_message.tool_calls:
            # Didn't call propose_metrics either — no model-proposed metrics
            # to show, but still fall back to a real per-category document
            # count rather than telling the user there's "not enough data"
            # when there plainly is (see fill_missing_category_metrics).
            return AnalyticsResult(metrics=fill_missing_category_metrics([], schema_summary))

        messages.append(
            {
                "role": "assistant",
                "content": assistant_message.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in assistant_message.tool_calls
                ],
            }
        )

        proposed_metrics = None
        for tool_call in assistant_message.tool_calls:
            try:
                args = json.loads(tool_call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            if tool_call.function.name == "propose_metrics":
                proposed_metrics = args.get("metrics")
                continue

            content, numbers = _execute_tool(tool_call.function.name, args, db, user, schema_summary)
            if numbers:
                category = str(args.get("category", ""))
                known_values[category].update(numbers)
                tool_calls_made += 1
            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": json.dumps(content)})

        if proposed_metrics is not None:
            if tool_calls_made == 0:
                # Refuses to trust model-proposed metrics with zero real
                # aggregation behind them — but a bare per-category document
                # count is real, code-computed data (never the model's own
                # number), so it's still safe and worth showing here.
                return AnalyticsResult(metrics=fill_missing_category_metrics([], schema_summary))
            validated = _validate_metrics(proposed_metrics, known_values)
            return AnalyticsResult(metrics=fill_missing_category_metrics(validated, schema_summary))

    # Ran out of rounds without ever calling propose_metrics — same fallback
    # rather than an empty result when real documents do exist.
    return AnalyticsResult(metrics=fill_missing_category_metrics([], schema_summary))
