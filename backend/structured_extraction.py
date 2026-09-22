import json
import re
import time
from datetime import date, datetime
from typing import Any, Optional

from groq import Groq, RateLimitError

from config import GROQ_API_KEY
from india_validation import detect_region, parse_indian_number, validate_gstin, validate_pan

MODEL = "openai/gpt-oss-120b"
MAX_CHARS = 4000
MAX_RETRIES = 3

client = Groq(api_key=GROQ_API_KEY)

LINE_ITEMS_FIELD_DESC = (
    "array of objects, one per line item in the document's items table. Extract EVERY row "
    "separately — do not summarize, merge, or aggregate rows, even if some fields repeat. "
    "Each object has exactly these keys: \"description\" (string), \"hsn_sac_code\" (string "
    "or null, if present), \"quantity\" (number or null), \"unit\" (string or null), \"rate\" "
    "(number or null), \"tax_percent\" (number or null), \"amount\" (number or null). Empty "
    "array if no items table is present."
)

TAX_BREAKDOWN_FIELD_DESC = (
    "array of objects, one per tax line shown separately from the line items (e.g. CGST, "
    "SGST, IGST), each with keys: \"label\" (string), \"rate_percent\" (number or null), "
    "\"amount\" (number or null). Empty array if no separate tax breakdown is shown."
)

SCHEMAS: dict[str, dict[str, str]] = {
    "Agreement": {
        "party_names": "array of strings — names of the contracting parties",
        "effective_date": "string, ISO date (YYYY-MM-DD), or null",
        "expiry_date": "string, ISO date (YYYY-MM-DD), or null",
        "auto_renewal": "boolean",
        "renewal_notice_period": "string or null",
        "termination_clause_summary": "string or null",
        "key_obligations": "array of strings",
        "penalty_clauses": "string or null",
    },
    "Invoice": {
        "vendor_name": "string or null",
        "invoice_number": "string or null",
        "invoice_date": "string, ISO date (YYYY-MM-DD), or null",
        "due_date": "string, ISO date (YYYY-MM-DD), or null",
        "gst_number": "string or null",
        "line_items": LINE_ITEMS_FIELD_DESC,
        "subtotal": "number or null — sum of all line item amounts, before tax",
        "tax_breakdown": TAX_BREAKDOWN_FIELD_DESC,
        "amount": "number or null — final total amount due, after tax",
    },
    "GST Document": {
        "gstin": "string or null",
        "period": "string or null",
        "tax_amount": "number or null",
        "filing_date": "string, ISO date (YYYY-MM-DD), or null",
    },
    "Purchase Order": {
        "po_number": "string or null",
        "vendor": "string or null",
        "buyer": "string or null",
        "delivery_date": "string, ISO date (YYYY-MM-DD), or null",
        "line_items": LINE_ITEMS_FIELD_DESC,
        "subtotal": "number or null — sum of all line item amounts, before tax",
        "tax_breakdown": TAX_BREAKDOWN_FIELD_DESC,
        "total_amount": "number or null — final total amount, after tax",
    },
    # New in Phase 30 (see CLAUDE.md's India-specific document intelligence
    # section) — a periodic GST return filing (GSTR-3B/GSTR-1/GSTR-9), distinct
    # from the generic "GST Document" category. Always treated as an Indian
    # document regardless of detect_region() — see extract_structured_data.
    "GST Filing": {
        "gstin": "string or null",
        "return_type": "string or null — e.g. GSTR-3B, GSTR-1, GSTR-9",
        "period": "string or null — the filing period, e.g. 'March 2026' or 'Q4 2025-26'",
        "filing_date": "string, ISO date (YYYY-MM-DD), or null",
        "arn": "string or null — Application Reference Number for the filing, if shown",
        "cgst_amount": "number or null",
        "sgst_amount": "number or null",
        "igst_amount": "number or null",
        "tax_liability": "number or null — total tax liability/payable for the period",
        "late_fee": "number or null",
    },
}

# Categories whose schema includes a line_items array — the prompt gets an extra,
# explicit instruction (below) not to summarize or merge rows for these.
LINE_ITEM_CATEGORIES = {"Invoice", "Purchase Order"}

# Extra fields offered to the model ONLY when detect_region() finds this is an
# Indian document (Phase 30) — a generic/non-Indian document's schema and
# prompt are otherwise byte-identical to what existed before this phase, so
# its extraction quality is unaffected. These fields are genuinely new
# information the model has to find in the text; they're kept separate from
# derived fields (CGST/SGST/IGST split, tax_type) which are instead computed
# deterministically in Python from the already-extracted tax_breakdown — see
# _derive_gst_split_from_tax_breakdown below.
INDIA_EXTRA_FIELDS: dict[str, dict[str, str]] = {
    "Invoice": {
        "pan": "string or null — the vendor's PAN, if shown separately from the GSTIN",
        "place_of_supply": "string or null — the state/UT of supply for GST purposes, if shown",
        "tds_amount": "number or null — tax deducted at source, if shown",
        "tcs_amount": "number or null — tax collected at source, if shown",
    },
    "Purchase Order": {
        "vendor_gstin": "string or null — the vendor/supplier's GSTIN, if shown",
        "buyer_gstin": "string or null — the buyer's GSTIN, if shown",
        "place_of_supply": "string or null — the state/UT of supply for GST purposes, if shown",
    },
    "Agreement": {
        "party_pan": "string or null — a contracting party's PAN, if mentioned",
        "party_gstin": "string or null — a contracting party's GSTIN, if mentioned",
        "stamp_duty": "string or null — stamp duty amount/details, if mentioned",
    },
}

# Which extracted field feeds documents.deadline_date, per category.
DEADLINE_FIELD = {
    "Agreement": "expiry_date",
    "Invoice": "due_date",
    "GST Document": "filing_date",
    "Purchase Order": "delivery_date",
    "GST Filing": "filing_date",
}

INDIA_PROMPT_NOTE = (
    "\n\nThis appears to be an Indian document. Amounts may be written with Indian digit "
    "grouping (e.g. ₹5,40,000) or in words using lakh/crore (e.g. \"five lakh forty "
    "thousand\") — convert these to plain numeric values with no currency symbols, commas, "
    "or words. Extract the Indian-specific fields only where the document actually shows "
    "them; use null for any that aren't present."
)


def _build_messages(category: str, text: str, region: str = "generic", strict: bool = False) -> list[dict]:
    schema = dict(SCHEMAS[category])
    if region == "IN" and category in INDIA_EXTRA_FIELDS:
        schema.update(INDIA_EXTRA_FIELDS[category])

    schema_lines = "\n".join(f'  "{key}": {desc}' for key, desc in schema.items())
    line_item_note = (
        "\n\nPay special attention to line_items: go through the document's items table row "
        "by row. Extract EVERY line item as its own separate object — do not summarize, "
        "merge, or produce a single combined description covering multiple rows."
        if category in LINE_ITEM_CATEGORIES
        else ""
    )
    india_note = INDIA_PROMPT_NOTE if region == "IN" else ""
    strict_note = (
        "\n\nYour previous response was not valid JSON. Return ONLY a single valid JSON "
        "object, with no markdown code fences, no commentary, and no trailing text."
        if strict
        else ""
    )
    system_prompt = (
        f"You are a document data extractor for {category} documents. Extract the "
        "following fields from the document text and return them as a single JSON object "
        f"with exactly these keys:\n{{\n{schema_lines}\n}}\n"
        "Use null for any field you cannot find in the text." + line_item_note + india_note +
        " Return ONLY the JSON object — no markdown, no explanation." + strict_note
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": text},
    ]


# Categories with a tax_breakdown array to derive a CGST/SGST/IGST split from
# (as opposed to GST Filing, which has the model extract those three amounts
# directly since a return filing has no line-itemized tax_breakdown table).
_TAX_BREAKDOWN_SPLIT_CATEGORIES = {"Invoice", "Purchase Order"}
# Categories where a computed tax_type ("intra_state"/"inter_state"/"mixed")
# is meaningful at all.
_TAX_TYPE_CATEGORIES = {"Invoice", "Purchase Order", "GST Filing"}

# Leading word boundary only — real documents commonly run the rate straight
# into the label with no separator (e.g. "IGST18 (18%)", seen verbatim on a
# real uploaded invoice in this app), so a trailing \b would never match.
_GST_SPLIT_LABEL_PATTERNS = {
    "cgst_amount": re.compile(r"\bcgst", re.IGNORECASE),
    "sgst_amount": re.compile(r"\bsgst", re.IGNORECASE),
    "igst_amount": re.compile(r"\bigst", re.IGNORECASE),
}

# field_name -> "gstin" | "pan" — every identifier field across every
# category/region variant that gets a deterministic <field>_valid sibling
# whenever it's actually populated. Applied unconditionally (see
# _apply_identifier_validation) since these fields already exist in the base,
# region-independent schema for Invoice/GST Document, or are only ever
# populated at all when the India-aware prompt variant was used.
_IDENTIFIER_FIELDS = {
    "gst_number": "gstin",
    "gstin": "gstin",
    "vendor_gstin": "gstin",
    "buyer_gstin": "gstin",
    "party_gstin": "gstin",
    "pan": "pan",
    "party_pan": "pan",
}

_LINE_ITEM_NUMERIC_FIELDS = ("quantity", "rate", "tax_percent", "amount")
_TAX_BREAKDOWN_NUMERIC_FIELDS = ("rate_percent", "amount")


def _numeric_field_names(category: str) -> set[str]:
    names = {
        key
        for source in (SCHEMAS.get(category, {}), INDIA_EXTRA_FIELDS.get(category, {}))
        for key, desc in source.items()
        if desc.startswith("number")
    }
    # Fields added by derivation, not by the model itself, but still worth
    # coercing defensively if something upstream ever puts a string in one.
    names |= {"cgst_amount", "sgst_amount", "igst_amount"}
    return names


def _coerce_numeric_strings(category: str, extracted: dict[str, Any]) -> None:
    """Some Indian amounts arrive from the model as a string despite the
    schema asking for a number (e.g. it echoes "5,40,000" verbatim instead of
    540000). Belt-and-suspenders on top of the prompt's own instruction to
    convert these — deterministically re-parses any numeric-schema field (and
    line_items/tax_breakdown sub-fields) that came back as a string."""
    for key in _numeric_field_names(category):
        value = extracted.get(key)
        if isinstance(value, str):
            parsed = parse_indian_number(value)
            if parsed is not None:
                extracted[key] = parsed

    for array_key, sub_fields in (
        ("line_items", _LINE_ITEM_NUMERIC_FIELDS),
        ("tax_breakdown", _TAX_BREAKDOWN_NUMERIC_FIELDS),
    ):
        items = extracted.get(array_key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            for field_name in sub_fields:
                value = item.get(field_name)
                if isinstance(value, str):
                    parsed = parse_indian_number(value)
                    if parsed is not None:
                        item[field_name] = parsed


def _derive_gst_split_from_tax_breakdown(extracted: dict[str, Any]) -> None:
    """Derives cgst_amount/sgst_amount/igst_amount from the already-extracted
    tax_breakdown array (matching entries by label, e.g. "IGST18 (18%)")
    rather than asking the model to redundantly re-extract the same amounts a
    second time as parallel top-level fields — one source of truth, and a
    document with no CGST/SGST/IGST-labeled tax line simply gets no split, no
    schema/prompt change needed at all for this to work on generic invoices."""
    tax_breakdown = extracted.get("tax_breakdown")
    if not isinstance(tax_breakdown, list):
        return

    for field_name, pattern in _GST_SPLIT_LABEL_PATTERNS.items():
        total = None
        for entry in tax_breakdown:
            if not isinstance(entry, dict):
                continue
            label = entry.get("label")
            amount = entry.get("amount")
            if isinstance(label, str) and pattern.search(label) and isinstance(amount, (int, float)):
                total = (total or 0) + amount
        if total is not None:
            extracted[field_name] = total


def _derive_tax_type(extracted: dict[str, Any]) -> None:
    """intra-state GST (CGST+SGST) vs inter-state (IGST) — derived from
    whichever of cgst_amount/sgst_amount/igst_amount ended up populated
    (either by the model directly, for GST Filing, or by the derivation
    above, for Invoice/Purchase Order), never asked of the model itself."""
    def _positive(key: str) -> bool:
        value = extracted.get(key)
        return isinstance(value, (int, float)) and value > 0

    has_intra = _positive("cgst_amount") or _positive("sgst_amount")
    has_inter = _positive("igst_amount")

    if has_intra and has_inter:
        extracted["tax_type"] = "mixed"
    elif has_inter:
        extracted["tax_type"] = "inter_state"
    elif has_intra:
        extracted["tax_type"] = "intra_state"


def _apply_identifier_validation(extracted: dict[str, Any]) -> None:
    """Real validation, not just extraction — flags a malformed GSTIN/PAN
    instead of silently accepting any string in that field. Unconditional
    (not region-gated): these fields are either already part of the
    region-independent base schema (gst_number, gstin) or only ever populated
    when the India-aware variant ran in the first place, so a non-Indian
    document simply never has a value here to validate."""
    for field_name, kind in _IDENTIFIER_FIELDS.items():
        value = extracted.get(field_name)
        if not isinstance(value, str) or not value.strip():
            continue
        extracted[f"{field_name}_valid"] = validate_gstin(value) if kind == "gstin" else validate_pan(value)


def _postprocess_extracted(category: str, extracted: dict[str, Any], region: str) -> dict[str, Any]:
    if not isinstance(extracted, dict) or extracted.get("parse_error"):
        return extracted

    _coerce_numeric_strings(category, extracted)

    if category in _TAX_BREAKDOWN_SPLIT_CATEGORIES:
        _derive_gst_split_from_tax_breakdown(extracted)
    if category in _TAX_TYPE_CATEGORIES:
        _derive_tax_type(extracted)

    _apply_identifier_validation(extracted)

    # Only tagged when the India-aware path actually ran — a generic
    # document's extracted_json shape stays byte-for-byte what it was before
    # this phase, meeting the "don't degrade non-Indian quality" constraint
    # even at the level of the JSON shape itself, not just prompt content.
    if region == "IN":
        extracted["_extraction_region"] = "IN"

    return extracted


def _try_parse_json_object(raw_response: str) -> Optional[dict[str, Any]]:
    try:
        parsed = json.loads(raw_response)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _call_groq(messages: list[dict]) -> str:
    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=0,
        # 800 was enough for the old flat schema, but a multi-row line_items array
        # (plus tax_breakdown) can produce a meaningfully longer JSON response —
        # raised with headroom given this app's history of max_tokens being too
        # tight for this reasoning model (see CLAUDE.md's "Reasoning-model gotcha").
        max_tokens=2000,
        reasoning_effort="low",
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content or ""


def _call_groq_with_retry(messages: list[dict]) -> str:
    for attempt in range(MAX_RETRIES + 1):
        try:
            return _call_groq(messages)
        except RateLimitError:
            if attempt == MAX_RETRIES:
                raise
            time.sleep(2**attempt)


def extract_structured_data(category: str, raw_text: str) -> Optional[dict[str, Any]]:
    if category not in SCHEMAS:
        return None

    text = (raw_text or "")[:MAX_CHARS]
    # GST Filing is inherently an Indian document by definition (a GSTR-3B/
    # GSTR-1/GSTR-9 style return) — always routed through the India-aware
    # note regardless of what detect_region's heuristic would say on a
    # possibly-thin snippet of text. Every other category is only routed
    # through it when detect_region actually finds an Indian identifier/
    # keyword in the real document text.
    region = "IN" if category == "GST Filing" else detect_region(raw_text)

    raw_response = _call_groq_with_retry(_build_messages(category, text, region))
    parsed = _try_parse_json_object(raw_response)

    if parsed is None:
        raw_response = _call_groq_with_retry(_build_messages(category, text, region, strict=True))
        parsed = _try_parse_json_object(raw_response)

    if parsed is None:
        return {"parse_error": True, "raw_response": raw_response}

    return _postprocess_extracted(category, parsed, region)


def get_deadline_date(category: str, extracted: Optional[dict[str, Any]]) -> Optional[date]:
    if not extracted or extracted.get("parse_error"):
        return None

    field = DEADLINE_FIELD.get(category)
    if not field:
        return None

    value = extracted.get(field)
    if not value or not isinstance(value, str):
        return None

    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
