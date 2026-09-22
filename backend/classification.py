import json
import time

from groq import Groq, RateLimitError

from config import GROQ_API_KEY

MODEL = "openai/gpt-oss-120b"
CATEGORIES = [
    "Agreement",
    "Invoice",
    "GST Document",
    "GST Filing",
    "Bank Statement",
    "Purchase Order",
    "Other",
]
MAX_CHARS = 4000
MAX_RETRIES = 3

# Bumped whenever CATEGORIES changes. Documents classified under an older version
# that landed on "Other" may actually match a category that didn't exist yet at the
# time — see processing.classify_and_structure() (which stamps this onto every
# document it classifies) and the frontend's needsReprocessing() (which only
# suggests reprocessing an "Other" document when its stored version is behind this).
# Bumped to 3 in Phase 30 for the addition of "GST Filing" (see CLAUDE.md's
# India-specific document intelligence section).
CURRENT_CLASSIFICATION_VERSION = 3

client = Groq(api_key=GROQ_API_KEY)

SYSTEM_PROMPT = (
    "You are a document classifier. Read the document text the user provides and classify it "
    "into exactly one of these categories: " + ", ".join(CATEGORIES) + ". "
    "\"GST Filing\" is specifically a periodic GST return filing (e.g. GSTR-3B, GSTR-1, GSTR-9) "
    "that shows a filing period, a filing/return date, and a tax liability — use \"GST Document\" "
    "instead for other GST-related paperwork that isn't a periodic return (e.g. a GST "
    "registration certificate or a standalone GST challan). "
    "Return a single JSON object with exactly two keys: \"category\" (one of the labels above, "
    "verbatim) and \"reasoning\" (one short sentence explaining why, based on what's actually in "
    "the text). If the document doesn't clearly match any specific category, use \"Other\" and "
    "use the reasoning to say what the document actually appears to be instead."
)


def _call_groq(text: str) -> str:
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        temperature=0,
        max_tokens=300,
        reasoning_effort="low",
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content or ""


def _call_groq_with_retry(text: str) -> str:
    for attempt in range(MAX_RETRIES + 1):
        try:
            return _call_groq(text)
        except RateLimitError:
            if attempt == MAX_RETRIES:
                raise
            time.sleep(2**attempt)


def parse_classification(raw_response: str) -> tuple[str, str]:
    try:
        data = json.loads(raw_response)
    except (json.JSONDecodeError, TypeError):
        data = {}

    raw_category = str(data.get("category") or "").strip()
    reasoning = str(data.get("reasoning") or "").strip()

    normalized = raw_category.lower()
    matched = next((c for c in CATEGORIES if c.lower() == normalized), None)
    if matched is None:
        matched = next((c for c in CATEGORIES if c.lower() in normalized), None)

    if matched is None:
        matched = "Other"
        if not reasoning:
            reasoning = (
                f"The classifier's response could not be parsed: {raw_response[:200]!r}"
            )

    if not reasoning:
        reasoning = "No reasoning was provided."

    return matched, reasoning


def classify_text(raw_text: str) -> tuple[str, str]:
    truncated = (raw_text or "")[:MAX_CHARS]
    raw_response = _call_groq_with_retry(truncated)
    return parse_classification(raw_response)
