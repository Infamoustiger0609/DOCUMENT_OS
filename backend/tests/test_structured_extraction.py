"""Unit tests for structured_extraction.py's Phase 30 additions — region
detection routing, the GSTIN/PAN validation post-processing step, the
deterministic CGST/SGST/IGST derivation from tax_breakdown, and Indian
numeric-string coercion. Groq is always mocked
(`structured_extraction.client.chat.completions.create`), same convention as
every other AI-calling module's tests in this app — these tests exercise the
post-processing logic, not Groq itself.
"""

from types import SimpleNamespace

import structured_extraction as se


def _fake_groq_response(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _mock_groq_once(monkeypatch, content: str):
    monkeypatch.setattr("structured_extraction.client.chat.completions.create", lambda **kwargs: _fake_groq_response(content))


def _mock_groq_capture_schema(monkeypatch, content: str):
    """Also captures the system prompt sent, so a test can assert on which
    fields were actually offered to the model for a given region."""
    captured = {}

    def fake_create(**kwargs):
        captured["system_prompt"] = kwargs["messages"][0]["content"]
        return _fake_groq_response(content)

    monkeypatch.setattr("structured_extraction.client.chat.completions.create", fake_create)
    return captured


# ---------------------------------------------------------------------------
# Region-gated schema: India-only fields only appear in the prompt when
# detect_region() finds this is an Indian document.
# ---------------------------------------------------------------------------


def test_generic_invoice_prompt_excludes_india_fields(monkeypatch):
    captured = _mock_groq_capture_schema(monkeypatch, '{"vendor_name": "Acme", "amount": 100.0}')
    generic_text = "Acme Corp Invoice. Bill to Example Ltd, London. Amount due: $100.00."

    result = se.extract_structured_data("Invoice", generic_text)

    assert "place_of_supply" not in captured["system_prompt"]
    assert "tds_amount" not in captured["system_prompt"]
    assert "_extraction_region" not in result  # generic doc's JSON shape is unchanged


def test_indian_invoice_prompt_includes_india_fields(monkeypatch):
    captured = _mock_groq_capture_schema(monkeypatch, '{"vendor_name": "KGraph", "amount": 540000.0, "gst_number": "29AAKCK4469L1ZQ"}')
    indian_text = "TAX INVOICE. GST No: 29AAKCK4469L1ZQ. HSN/SAC 995468. IGST 18%."

    result = se.extract_structured_data("Invoice", indian_text)

    assert "place_of_supply" in captured["system_prompt"]
    assert "tds_amount" in captured["system_prompt"]
    assert result["_extraction_region"] == "IN"


# ---------------------------------------------------------------------------
# GSTIN/PAN validation post-processing
# ---------------------------------------------------------------------------


def test_valid_real_gst_number_is_flagged_valid(monkeypatch):
    _mock_groq_once(monkeypatch, '{"vendor_name": "KGraph", "gst_number": "29AAKCK4469L1ZQ", "amount": 540000.0}')
    result = se.extract_structured_data("Invoice", "GST No: 29AAKCK4469L1ZQ")
    assert result["gst_number_valid"] is True


def test_malformed_gst_number_is_flagged_invalid(monkeypatch):
    _mock_groq_once(monkeypatch, '{"vendor_name": "Acme", "gst_number": "NOTAREALGSTIN12", "amount": 100.0}')
    result = se.extract_structured_data("Invoice", "GST No: NOTAREALGSTIN12 mentioned here for GSTIN testing")
    assert result["gst_number_valid"] is False


def test_null_gst_number_gets_no_validity_field(monkeypatch):
    _mock_groq_once(monkeypatch, '{"vendor_name": "Acme", "gst_number": null, "amount": 100.0}')
    result = se.extract_structured_data("Invoice", "A plain generic invoice with no GST number.")
    assert "gst_number_valid" not in result


def test_pan_field_validated_on_agreement_when_present(monkeypatch):
    _mock_groq_once(
        monkeypatch,
        '{"party_names": ["A", "B"], "effective_date": null, "expiry_date": null, '
        '"auto_renewal": false, "renewal_notice_period": null, '
        '"termination_clause_summary": null, "key_obligations": [], "penalty_clauses": null, '
        '"party_pan": "AAKCK4469L", "party_gstin": "29AAKCK4469L1ZQ"}',
    )
    text = "This Agreement references PAN: AAKCK4469L and GSTIN: 29AAKCK4469L1ZQ of the Service Provider."
    result = se.extract_structured_data("Agreement", text)
    assert result["party_pan_valid"] is True
    assert result["party_gstin_valid"] is True


# ---------------------------------------------------------------------------
# Deterministic CGST/SGST/IGST derivation + tax_type
# ---------------------------------------------------------------------------


def test_igst_derived_from_tax_breakdown_label(monkeypatch):
    _mock_groq_once(
        monkeypatch,
        '{"vendor_name": "KGraph", "amount": 540000.0, "gst_number": "29AAKCK4469L1ZQ", '
        '"tax_breakdown": [{"label": "IGST18 (18%)", "rate_percent": 18, "amount": 82372.7}]}',
    )
    text = "TAX INVOICE. GST No: 29AAKCK4469L1ZQ. IGST18 (18%) 82,372.7."
    result = se.extract_structured_data("Invoice", text)
    assert result["igst_amount"] == 82372.7
    assert result.get("cgst_amount") is None
    assert result.get("sgst_amount") is None
    assert result["tax_type"] == "inter_state"


def test_cgst_sgst_derived_and_summed_across_multiple_entries(monkeypatch):
    _mock_groq_once(
        monkeypatch,
        '{"po_number": "PO1", "vendor": "V", "buyer": "B", "delivery_date": null, '
        '"line_items": [], "subtotal": 1000.0, "total_amount": 1180.0, '
        '"tax_breakdown": [{"label": "CGST (9%)", "rate_percent": 9, "amount": 90.0}, '
        '{"label": "SGST (9%)", "rate_percent": 9, "amount": 90.0}]}',
    )
    text = "Purchase order with CGST and SGST split for an intra-state Indian supply. GSTIN 27AAACO8897J1ZO."
    result = se.extract_structured_data("Purchase Order", text)
    assert result["cgst_amount"] == 90.0
    assert result["sgst_amount"] == 90.0
    assert result.get("igst_amount") is None
    assert result["tax_type"] == "intra_state"


def test_no_gst_labeled_tax_breakdown_derives_nothing(monkeypatch):
    _mock_groq_once(
        monkeypatch,
        '{"vendor_name": "Acme", "amount": 118.0, "gst_number": null, '
        '"tax_breakdown": [{"label": "VAT", "rate_percent": 20, "amount": 18.0}]}',
    )
    text = "A UK invoice with VAT, not GST. Amount due 118.00 GBP."
    result = se.extract_structured_data("Invoice", text)
    assert "cgst_amount" not in result
    assert "sgst_amount" not in result
    assert "igst_amount" not in result
    assert "tax_type" not in result


def test_gst_filing_direct_fields_produce_tax_type_without_tax_breakdown(monkeypatch):
    _mock_groq_once(
        monkeypatch,
        '{"gstin": "29AAKCK4469L1ZQ", "return_type": "GSTR-3B", "period": "March 2026", '
        '"filing_date": "2026-04-20", "arn": "AA290326001234A", "cgst_amount": 5000.0, '
        '"sgst_amount": 5000.0, "igst_amount": 0, "tax_liability": 10000.0, "late_fee": null}',
    )
    result = se.extract_structured_data("GST Filing", "GSTR-3B return for March 2026. GSTIN 29AAKCK4469L1ZQ.")
    assert result["tax_type"] == "intra_state"
    assert result["gstin_valid"] is True


# ---------------------------------------------------------------------------
# Indian numeric-string coercion (belt-and-suspenders on top of the prompt
# instruction — proves the app doesn't just *hope* the model returns a
# number).
# ---------------------------------------------------------------------------


def test_string_amount_with_indian_grouping_is_coerced_to_float(monkeypatch):
    _mock_groq_once(
        monkeypatch,
        '{"vendor_name": "KGraph", "amount": "₹5,40,000", "gst_number": "29AAKCK4469L1ZQ"}',
    )
    result = se.extract_structured_data("Invoice", "GST No: 29AAKCK4469L1ZQ. Total ₹5,40,000.")
    assert result["amount"] == 540000.0
    assert isinstance(result["amount"], float)


def test_line_item_numeric_strings_are_coerced(monkeypatch):
    _mock_groq_once(
        monkeypatch,
        '{"vendor_name": "KGraph", "amount": 100.0, "gst_number": null, '
        '"line_items": [{"description": "Item", "amount": "1,000", "quantity": "2", '
        '"hsn_sac_code": null, "unit": null, "rate": null, "tax_percent": null}]}',
    )
    text = "GSTIN 29AAKCK4469L1ZQ mentioned so this routes through the India-aware path."
    result = se.extract_structured_data("Invoice", text)
    assert result["line_items"][0]["amount"] == 1000.0
    assert result["line_items"][0]["quantity"] == 2.0


# ---------------------------------------------------------------------------
# parse_error passthrough — post-processing must not touch a failed parse.
# ---------------------------------------------------------------------------


def test_unparseable_response_is_not_postprocessed(monkeypatch):
    monkeypatch.setattr(
        "structured_extraction.client.chat.completions.create",
        lambda **kwargs: _fake_groq_response("not valid json at all"),
    )
    result = se.extract_structured_data("Invoice", "some text")
    assert result["parse_error"] is True
    assert "gst_number_valid" not in result
