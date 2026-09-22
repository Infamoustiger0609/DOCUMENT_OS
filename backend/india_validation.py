"""Region-aware, India-specific validation and parsing utilities (Phase 30 —
see CLAUDE.md's "India-specific document intelligence" section).

Every check here is deterministic Python, never delegated to the LLM — same
reasoning as analytics.py's aggregation tools: a checksum or a word-to-number
conversion is exactly the kind of thing a language model can get subtly wrong
and sound confident anyway, so it's computed in code and only ever *validated*
against what the model extracted, not trusted from the model's own output.

The GSTIN checksum algorithm below was cross-checked against a real,
independently-sourced GSTIN (33AAACC1206D1ZN, Central Warehousing
Corporation) and against the two real GSTINs already present in this app's
own live documents (29AAKCK4469L1ZQ, 27AAACO8897J1ZO) before being trusted —
see CLAUDE.md for the verification note.
"""

import re
from decimal import Decimal
from typing import Any, Optional

GSTIN_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# 2-digit state code + 10-char PAN + 1-char entity/registration code + fixed
# 'Z' + 1-char checksum = 15 characters.
_GSTIN_CORE = r"[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]"
_PAN_CORE = r"[A-Z]{5}[0-9]{4}[A-Z]"

GSTIN_STRUCTURE = re.compile(f"^{_GSTIN_CORE}$")
PAN_STRUCTURE = re.compile(f"^{_PAN_CORE}$")

# Same shapes, but unanchored with word boundaries — for scanning a match
# anywhere inside a larger block of raw document text (detect_region below),
# as opposed to validating a single already-extracted field value.
GSTIN_PATTERN = re.compile(rf"\b{_GSTIN_CORE}\b")
PAN_PATTERN = re.compile(rf"\b{_PAN_CORE}\b")

INDIA_KEYWORDS = re.compile(r"\b(GSTIN|CGST|SGST|IGST|HSN|SAC)\b", re.IGNORECASE)
INDIA_CURRENCY_HINTS = re.compile(r"₹|\brs\.?\s*\d|\blakh|\bcrore", re.IGNORECASE)


def _gstin_check_digit(gstin_14: str) -> Optional[str]:
    """The GSTIN check-digit algorithm (ISO 7064 MOD 37-36 family): a 36-char
    alphabet (0-9 then A-Z), alternating x1/x2 position weights over the
    first 14 characters, each product's quotient+remainder (mod 36) summed,
    then the final digit is (36 - sum % 36) % 36 in the same alphabet."""
    total = 0
    factor = 1
    for char in gstin_14:
        if char not in GSTIN_ALPHABET:
            return None
        code_point = GSTIN_ALPHABET.index(char)
        product = factor * code_point
        total += product // 36 + product % 36
        factor = 2 if factor == 1 else 1
    return GSTIN_ALPHABET[(36 - total % 36) % 36]


def validate_gstin(value: str) -> bool:
    """Full validation: the 15-character structure (state code + PAN + entity
    code + literal 'Z' + checksum) *and* a correct checksum digit — flags a
    malformed value instead of accepting anything shaped like a GSTIN."""
    if not isinstance(value, str):
        return False
    candidate = value.strip().upper()
    if not GSTIN_STRUCTURE.match(candidate):
        return False
    return _gstin_check_digit(candidate[:14]) == candidate[14]


def validate_pan(value: str) -> bool:
    """PAN has no public checksum digit (unlike GSTIN, which embeds a PAN
    internally) — structural validation only: 5 letters, 4 digits, 1 letter."""
    if not isinstance(value, str):
        return False
    return bool(PAN_STRUCTURE.match(value.strip().upper()))


def detect_region(raw_text: Optional[str]) -> str:
    """Deterministic, regex-based region detection — no extra Groq call, and
    keeps the signal auditable/testable rather than asking a model to judge
    something regex can already answer reliably (same "compute, don't ask the
    model" philosophy as analytics.py). Routes extraction through the
    India-aware prompt variant in structured_extraction.py only when this
    returns "IN"; a "generic" document's schema/prompt is left completely
    unchanged so non-Indian extraction quality is unaffected.

    A single real (structurally exact) GSTIN or PAN match is a strong enough
    signal on its own. Otherwise falls back to requiring two or more
    India-specific keyword/currency-symbol hits, so one coincidental
    PAN-shaped substring (5 letters + 4 digits + 1 letter can occur by chance
    in ALL-CAPS headers) doesn't alone flip a genuinely generic document.
    """
    text = raw_text or ""
    if GSTIN_PATTERN.search(text) or PAN_PATTERN.search(text):
        return "IN"
    hits = len(INDIA_KEYWORDS.findall(text)) + len(INDIA_CURRENCY_HINTS.findall(text))
    return "IN" if hits >= 2 else "generic"


_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
# "hundred" is handled separately below (it multiplies the current segment
# rather than adding a new segment to the total, unlike thousand/lakh/crore).
_SCALE_WORDS = {
    "thousand": 1_000, "lakh": 100_000, "lakhs": 100_000,
    "crore": 10_000_000, "crores": 10_000_000,
}
_FILLER_WORDS = {"rupees", "rupee", "only", "and", "inr"}


def _parse_number_words(text: str) -> Optional[float]:
    tokens = [t for t in re.findall(r"[a-zA-Z]+", text.lower()) if t not in _FILLER_WORDS]
    if not tokens:
        return None

    total = 0.0
    current = 0.0
    matched_any = False
    for token in tokens:
        if token in _NUMBER_WORDS:
            current += _NUMBER_WORDS[token]
            matched_any = True
        elif token == "hundred":
            current = (current or 1) * 100
            matched_any = True
        elif token in _SCALE_WORDS:
            total += (current or 1) * _SCALE_WORDS[token]
            current = 0.0
            matched_any = True
        else:
            # An unrecognized word means this isn't a clean number-words
            # phrase (e.g. it's a sentence, not an amount-in-words line) —
            # bail out rather than guess at a partial number.
            return None

    if not matched_any:
        return None
    return total + current


def parse_indian_number(text: Optional[str]) -> Optional[float]:
    """Parses an Indian-formatted amount into a clean float — either digit
    form ("₹5,40,000", "Rs. 5,40,000.50", "540000") or word form ("five
    lakh forty thousand", optionally with "rupees"/"only"). Returns None if
    the text can't be made sense of as a number at all; never raises."""
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped:
        return None

    numeric_candidate = re.sub(r"[₹,]|\brs\.?|\binr\b", "", stripped, flags=re.IGNORECASE).strip()
    try:
        return float(numeric_candidate)
    except ValueError:
        pass

    return _parse_number_words(stripped)


# ---------------------------------------------------------------------------
# The inverse of parse_indian_number's word-parsing: a plain rupee amount ->
# Indian-numbering words (lakh/crore, NOT western million/billion). Used by
# template_generation.py for a generated invoice's "Amount in words" line.
# Deterministic, computed in Python — never asked of the LLM, same "don't
# trust the model with arithmetic" reasoning as every other India-specific
# check in this module (GSTIN checksum, lakh/crore parsing above).
# ---------------------------------------------------------------------------

_INDIAN_ONES = [
    "", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
    "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen",
    "Eighteen", "Nineteen",
]
_INDIAN_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _two_digit_words(n: int) -> str:
    if n < 20:
        return _INDIAN_ONES[n]
    tens, ones = divmod(n, 10)
    return _INDIAN_TENS[tens] + (f" {_INDIAN_ONES[ones]}" if ones else "")


def _three_digit_words(n: int) -> str:
    if n >= 100:
        hundreds, rest = divmod(n, 100)
        return f"{_INDIAN_ONES[hundreds]} Hundred" + (f" {_two_digit_words(rest)}" if rest else "")
    return _two_digit_words(n)


def number_to_indian_words(amount: Any) -> str:
    """A rupee amount (int/float/Decimal) -> words using the Indian numbering
    system: crore (10,000,000) / lakh (100,000) / thousand / hundred — e.g.
    194700 -> "One Lakh Ninety Four Thousand Seven Hundred" — never
    million/billion. Rounds to the nearest paisa; a nonzero fractional part
    is appended as "and N Paise". Verified, not assumed: cross-checked this
    exact 194700 -> "One Lakh Ninety Four Thousand Seven Hundred" example
    (165000 + 18% GST = 194700, the same figures used in this feature's live
    verification) before being trusted for real document generation."""
    decimal_amount = Decimal(str(amount)).quantize(Decimal("0.01"))
    negative = decimal_amount < 0
    decimal_amount = abs(decimal_amount)

    rupees = int(decimal_amount)
    paise = int((decimal_amount - rupees) * 100)

    crore, remainder = divmod(rupees, 10_000_000)
    lakh, remainder = divmod(remainder, 100_000)
    thousand, hundred_rest = divmod(remainder, 1000)

    parts = []
    if crore:
        parts.append(f"{_three_digit_words(crore)} Crore")
    if lakh:
        parts.append(f"{_three_digit_words(lakh)} Lakh")
    if thousand:
        parts.append(f"{_three_digit_words(thousand)} Thousand")
    if hundred_rest:
        parts.append(_three_digit_words(hundred_rest))

    words = " ".join(parts) if parts else "Zero"
    if paise:
        words += f" and {_two_digit_words(paise)} Paise"
    return ("Minus " if negative else "") + words
