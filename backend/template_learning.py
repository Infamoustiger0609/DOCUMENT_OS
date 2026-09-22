"""Learns a document's field STRUCTURE (never its values) from a sample, and
parses a natural-language generation instruction into structured field
values matching a learned schema (Phase 32 — see CLAUDE.md's Document
generation section).

Same Groq client/retry conventions as classification.py/structured_extraction.py
(kept as its own copy rather than a shared helper — this project's established
"a little duplication over an abstraction with few call sites" convention, see
CLAUDE.md's Copilot section for the same reasoning applied elsewhere).

The learned `field_schema` shape (also the shape template_generation.py reads):

    {
      "document_type": "Invoice",
      "sections": [
        {"id": "header", "title": "Header", "type": "fields",
         "fields": [{"key": "vendor_name", "label": "Vendor Name", "type": "string"}, ...]},
        {"id": "line_items", "title": "Line Items", "type": "table",
         "columns": [{"key": "description", "label": "Description", "type": "string"}, ...]},
      ]
    }

`type` is one of "string"/"number"/"date" for a field or column. A "table"
section is only ever present if the sample document genuinely had one
(e.g. an invoice's or purchase order's line-item table) — an Agreement's
learned schema typically has none.
"""

import json
import time
from datetime import date
from typing import Any, Optional

from groq import Groq, RateLimitError

from config import GROQ_API_KEY

MODEL = "openai/gpt-oss-120b"
MAX_CHARS = 4000
MAX_INSTRUCTION_CHARS = 2000
MAX_RETRIES = 3

client = Groq(api_key=GROQ_API_KEY)

_VALID_FIELD_TYPES = {"string", "number", "date"}

SCHEMA_SHAPE_DESCRIPTION = (
    "{\n"
    '  "document_type": string — a short label, e.g. "Invoice" or "Agreement",\n'
    '  "sections": [\n'
    "    {\n"
    '      "id": string — a short snake_case identifier, unique within this schema,\n'
    '      "title": string — a human-readable section heading,\n'
    '      "type": "fields" or "table",\n'
    '      // if type is "fields":\n'
    '      "fields": [ {"key": snake_case string, "label": string, "type": "string"|"number"|"date"}, ... ],\n'
    '      // if type is "table" (a line-item-style table, only if the sample genuinely has one):\n'
    '      "columns": [ {"key": snake_case string, "label": string, "type": "string"|"number"|"date"}, ... ]\n'
    "    }\n"
    "  ]\n"
    "}"
)

def _build_learn_system_prompt(category: str) -> str:
    # Built with an f-string (only {category} is ever substituted) rather
    # than str.format() on a string that already has SCHEMA_SHAPE_DESCRIPTION
    # concatenated in — SCHEMA_SHAPE_DESCRIPTION's own literal JSON braces
    # would otherwise be mis-parsed as .format() replacement fields (a real
    # bug hit and fixed here: raw_text and category are the only two things
    # this app ever safely interpolates into a prompt string that also
    # contains literal braces, and this function is the fix for that
    # specific gotcha).
    return (
        f"You are a document structure analyst. You will be given the text of a sample {category} "
        "document. Identify its FIELD STRUCTURE ONLY — the header fields, any line-item table "
        "columns, and footer/signature fields — in the same order they appear in the document.\n\n"
        "Do NOT include any of the actual data from this specific sample (no real names, numbers, "
        "dates, or amounts) — only the field keys/labels/types that describe the document's SHAPE, "
        "so the same structure could be reused later to generate a completely different document "
        "with different values.\n\n"
        "Return a single JSON object with exactly this shape:\n" + SCHEMA_SHAPE_DESCRIPTION + "\n\n"
        "Include a \"table\" section only if the document genuinely has a line-item table (e.g. an "
        "invoice's or purchase order's items table) — otherwise omit it entirely. Return ONLY the "
        "JSON object, no markdown, no commentary."
    )

GENERATE_VALUES_SYSTEM_PROMPT_TEMPLATE = (
    "You are filling in a document template from a plain-language instruction. Today's date is "
    "{today}. The template has this field structure:\n{schema_json}\n\n"
    "Read the user's instruction and produce a single JSON object with exactly one key per "
    "section id shown above. For a \"fields\"-type section, the value is an object mapping each "
    "field's key to a filled-in value (or null if not mentioned/inferable). For a \"table\"-type "
    "section, the value is an ARRAY of row objects, each mapping the section's column keys to "
    "values — infer one row per distinct line item mentioned; use an empty array if none are "
    "mentioned. Dates must be ISO format (YYYY-MM-DD) — compute a relative date (e.g. \"due in 30 "
    "days\") from today's date above. Numbers must be plain numeric values with no currency "
    "symbols or commas. Use null for anything not mentioned and not reasonably inferable — do not "
    "invent unrelated details. Return ONLY the JSON object, no markdown, no commentary."
)

_STRICT_RETRY_NOTE = (
    "\n\nYour previous response was not valid JSON. Return ONLY a single valid JSON object, with "
    "no markdown code fences, no commentary, and no trailing text."
)


def _call_groq(system_prompt: str, user_content: str) -> str:
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=0,
        max_tokens=1500,
        reasoning_effort="low",
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content or ""


def _call_groq_with_retry(system_prompt: str, user_content: str) -> str:
    for attempt in range(MAX_RETRIES + 1):
        try:
            return _call_groq(system_prompt, user_content)
        except RateLimitError:
            if attempt == MAX_RETRIES:
                raise
            time.sleep(2**attempt)


def _try_parse_json_object(raw: str) -> Optional[dict[str, Any]]:
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _call_json_with_strict_retry(system_prompt: str, user_content: str) -> Optional[dict[str, Any]]:
    raw = _call_groq_with_retry(system_prompt, user_content)
    parsed = _try_parse_json_object(raw)
    if parsed is not None:
        return parsed
    raw = _call_groq_with_retry(system_prompt + _STRICT_RETRY_NOTE, user_content)
    return _try_parse_json_object(raw)


# ---------------------------------------------------------------------------
# Learning a schema from a sample document
# ---------------------------------------------------------------------------


def _normalize_field(raw: Any) -> Optional[dict[str, str]]:
    if not isinstance(raw, dict):
        return None
    key = raw.get("key")
    if not isinstance(key, str) or not key.strip():
        return None
    label = raw.get("label")
    label = label.strip() if isinstance(label, str) and label.strip() else key.replace("_", " ").title()
    field_type = raw.get("type") if raw.get("type") in _VALID_FIELD_TYPES else "string"
    return {"key": key.strip(), "label": label, "type": field_type}


def _normalize_section(raw: Any) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    section_id = raw.get("id")
    if not isinstance(section_id, str) or not section_id.strip():
        return None
    section_id = section_id.strip()
    title = raw.get("title")
    title = title.strip() if isinstance(title, str) and title.strip() else section_id.replace("_", " ").title()

    if raw.get("type") == "table":
        columns = [c for c in (_normalize_field(c) for c in (raw.get("columns") or [])) if c]
        if not columns:
            return None
        return {"id": section_id, "title": title, "type": "table", "columns": columns}

    fields = [f for f in (_normalize_field(f) for f in (raw.get("fields") or [])) if f]
    if not fields:
        return None
    return {"id": section_id, "title": title, "type": "fields", "fields": fields}


def normalize_schema(raw: Any, category: str) -> dict[str, Any]:
    """Defensive normalization of whatever Groq returned — drops malformed
    sections/fields rather than trusting the model's output shape blindly
    (same "validate, don't just trust the model" pattern as every other
    Groq-facing module in this app, e.g. analytics.py's known_values check).
    """
    document_type = category
    raw_sections: list[Any] = []
    if isinstance(raw, dict):
        if isinstance(raw.get("document_type"), str) and raw["document_type"].strip():
            document_type = raw["document_type"].strip()
        raw_sections = raw.get("sections") or []

    sections = [s for s in (_normalize_section(s) for s in raw_sections) if s]

    # De-duplicate section ids (the model could repeat one) — keep the first.
    seen_ids: set[str] = set()
    unique_sections = []
    for section in sections:
        if section["id"] in seen_ids:
            continue
        seen_ids.add(section["id"])
        unique_sections.append(section)

    return {"document_type": document_type, "sections": unique_sections}


def learn_field_schema(category: str, raw_text: str) -> dict[str, Any]:
    text = (raw_text or "")[:MAX_CHARS]
    system_prompt = _build_learn_system_prompt(category)
    parsed = _call_json_with_strict_retry(system_prompt, text)
    schema = normalize_schema(parsed, category)
    if not schema["sections"]:
        raise ValueError("Could not learn a usable field structure from this sample.")
    return schema


# ---------------------------------------------------------------------------
# Parsing a natural-language instruction into schema-shaped values
# ---------------------------------------------------------------------------


def normalize_values(field_schema: dict[str, Any], raw_values: Any) -> dict[str, Any]:
    """Forces whatever came in (from the model, or directly from a client's
    own request body) into EXACTLY the shape field_schema describes — every
    section id present, a "fields" section always a dict of its known keys, a
    "table" section always a list of dicts of its known keys. Never trusts
    the input's own keys/shape; this is the actual safety boundary between
    "the model or the client sent something" and what template_generation.py
    ever renders."""
    if not isinstance(raw_values, dict):
        raw_values = {}

    result: dict[str, Any] = {}
    for section in field_schema.get("sections", []):
        section_id = section["id"]
        raw_section_value = raw_values.get(section_id)

        if section["type"] == "table":
            rows = []
            if isinstance(raw_section_value, list):
                for row in raw_section_value:
                    if isinstance(row, dict):
                        rows.append({col["key"]: row.get(col["key"]) for col in section["columns"]})
            result[section_id] = rows
        else:
            row = raw_section_value if isinstance(raw_section_value, dict) else {}
            result[section_id] = {f["key"]: row.get(f["key"]) for f in section["fields"]}

    return result


def parse_instruction_to_values(field_schema: dict[str, Any], instruction: str) -> dict[str, Any]:
    schema_json = json.dumps(field_schema)
    system_prompt = GENERATE_VALUES_SYSTEM_PROMPT_TEMPLATE.format(
        today=date.today().isoformat(), schema_json=schema_json
    )
    text = (instruction or "")[:MAX_INSTRUCTION_CHARS]
    parsed = _call_json_with_strict_retry(system_prompt, text)
    if parsed is None:
        raise ValueError("Could not parse the instruction into structured field values.")
    return normalize_values(field_schema, parsed)
