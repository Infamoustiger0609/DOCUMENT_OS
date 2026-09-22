"""Unit tests for india_validation.py (Phase 30 — see CLAUDE.md's
India-specific document intelligence section). Pure functions, no DB/Groq
involved, so these run as plain assertions rather than against the test
Postgres container.

The GSTIN checksum test vectors are real, not invented: 33AAACC1206D1ZN is a
publicly documented real GSTIN (Central Warehousing Corporation), and the
other two are the actual vendor/buyer GSTINs already present in this app's
own live uploaded documents (see CLAUDE.md) — both were independently
verified against this same algorithm before being trusted here.
"""

from india_validation import detect_region, parse_indian_number, validate_gstin, validate_pan

REAL_VALID_GSTINS = [
    "33AAACC1206D1ZN",  # Central Warehousing Corporation (public reference GSTIN)
    "29AAKCK4469L1ZQ",  # KGraph AI Solutions — vendor GSTIN on a real uploaded invoice
    "27AAACO8897J1ZO",  # Oam Industries — buyer GSTIN on the same real documents
]


def test_validate_gstin_accepts_real_valid_gstins():
    for gstin in REAL_VALID_GSTINS:
        assert validate_gstin(gstin) is True, gstin


def test_validate_gstin_rejects_wrong_checksum():
    # Flip the real check digit on an otherwise-correct GSTIN.
    tampered = REAL_VALID_GSTINS[0][:-1] + ("A" if REAL_VALID_GSTINS[0][-1] != "A" else "B")
    assert validate_gstin(tampered) is False


def test_validate_gstin_rejects_malformed_structure():
    assert validate_gstin("not-a-gstin") is False
    assert validate_gstin("29AAKCK4469L1Z") is False  # too short (14 chars)
    assert validate_gstin("") is False
    assert validate_gstin(None) is False  # type: ignore[arg-type]


def test_validate_gstin_is_case_insensitive_and_trims_whitespace():
    lowered = REAL_VALID_GSTINS[0].lower()
    assert validate_gstin(f"  {lowered}  ") is True


def test_validate_pan_accepts_valid_structure():
    assert validate_pan("AAKCK4469L") is True  # real PAN embedded in the GSTINs above
    assert validate_pan("AAACO8897J") is True


def test_validate_pan_rejects_malformed_structure():
    assert validate_pan("AAKCK4469") is False  # 9 chars, too short
    assert validate_pan("12345ABCDE") is False  # wrong character pattern
    assert validate_pan(None) is False  # type: ignore[arg-type]


def test_detect_region_true_on_real_gstin_in_text():
    text = "Please remit payment. GST No: 29AAKCK4469L1ZQ. Thank you."
    assert detect_region(text) == "IN"


def test_detect_region_true_on_multiple_india_keywords():
    text = "Tax breakdown: CGST 9% and SGST 9% applied, HSN code 995468."
    assert detect_region(text) == "IN"


def test_detect_region_generic_on_plain_english_invoice():
    text = (
        "Acme Corp Invoice #1042. Bill to: Example Ltd, 221B Baker Street, London. "
        "Amount due: $1,200.00 within 30 days of receipt."
    )
    assert detect_region(text) == "generic"


def test_detect_region_generic_on_single_stray_keyword():
    # A single incidental "GST" mention shouldn't alone flip a generic doc.
    text = "This invoice does not include GST as the customer is based overseas."
    assert detect_region(text) == "generic"


def test_parse_indian_number_digit_form_with_symbol_and_commas():
    assert parse_indian_number("₹5,40,000") == 540000.0


def test_parse_indian_number_rs_prefix_with_decimal():
    assert parse_indian_number("Rs. 5,40,000.50") == 540000.5


def test_parse_indian_number_plain_numeral():
    assert parse_indian_number("540000") == 540000.0


def test_parse_indian_number_word_form_lakh_and_thousand():
    assert parse_indian_number("Five lakh forty thousand") == 540000.0


def test_parse_indian_number_word_form_with_rupees_and_only():
    assert parse_indian_number("Rupees Five Lakh Forty Thousand Only") == 540000.0


def test_parse_indian_number_crore_value():
    # two crore fifty lakh = 2*10,000,000 + 50*100,000 = 25,000,000
    assert parse_indian_number("two crore fifty lakh") == 25_000_000.0


def test_parse_indian_number_hundred_combined_with_scale():
    # one hundred twenty five thousand = 125 * 1000
    assert parse_indian_number("one hundred twenty five thousand") == 125_000.0


def test_parse_indian_number_returns_none_for_nonsense():
    assert parse_indian_number("not a number at all") is None
    assert parse_indian_number("") is None
    assert parse_indian_number(None) is None
