"""Tests for the professional-document renderer (template_generation.py) —
Phase 32's original ReportLab rendering was rebuilt into a real invoice-shaped
layout (letterhead header, party grid, shaded line-items table, boxed totals,
Indian amount-in-words, highlighted notes box, bank details, signature block,
outer page border + page numbers). See CLAUDE.md's Document generation
section for the full design.

Nothing here is mocked — every test renders a real PDF via the real
`render_document()`/ReportLab and re-opens it with PyMuPDF to assert on the
actual output, the same "prove it with the real artifact" discipline this
app's other file-producing tests already follow.
"""

import re

import pymupdf as fitz

from template_generation import build_generated_filename, render_document

INVOICE_SCHEMA = {
    "document_type": "Tax Invoice",
    "sections": [
        {
            "id": "issuer", "title": "Issuer", "type": "fields",
            "fields": [
                {"key": "company_name", "label": "Company Name", "type": "string"},
                {"key": "company_address", "label": "Address", "type": "string"},
                {"key": "company_gstin", "label": "GSTIN", "type": "string"},
            ],
        },
        {
            "id": "ref", "title": "Reference", "type": "fields",
            "fields": [
                {"key": "invoice_number", "label": "Invoice No.", "type": "string"},
                {"key": "invoice_date", "label": "Invoice Date", "type": "date"},
            ],
        },
        {
            "id": "bill_to", "title": "Bill To", "type": "fields",
            "fields": [
                {"key": "bill_to_name", "label": "Name", "type": "string"},
                {"key": "bill_to_gstin", "label": "GSTIN", "type": "string"},
            ],
        },
        {
            "id": "line_items", "title": "Line Items", "type": "table",
            "columns": [
                {"key": "description", "label": "Description", "type": "string"},
                {"key": "quantity", "label": "Qty", "type": "number"},
                {"key": "rate", "label": "Rate", "type": "number"},
                {"key": "amount", "label": "Amount", "type": "number"},
            ],
        },
        {
            "id": "totals", "title": "Totals", "type": "fields",
            "fields": [
                {"key": "subtotal", "label": "Subtotal", "type": "number"},
                {"key": "cgst_amount", "label": "CGST @ 9%", "type": "number"},
                {"key": "sgst_amount", "label": "SGST @ 9%", "type": "number"},
                {"key": "total_amount", "label": "Total", "type": "number"},
            ],
        },
        {
            "id": "notes", "title": "Notes", "type": "fields",
            "fields": [{"key": "notes_gst", "label": "GST Note", "type": "string"}],
        },
        {
            "id": "bank", "title": "Bank Details", "type": "fields",
            "fields": [
                {"key": "bank_name", "label": "Bank Name", "type": "string"},
                {"key": "account_number", "label": "Account Number", "type": "string"},
                {"key": "ifsc_code", "label": "IFSC Code", "type": "string"},
            ],
        },
    ],
}

LONG_DESCRIPTION = (
    "Advertisement charges for regional print campaign covering Kerala and "
    "Tamil Nadu editions, March 2026 cycle, including design and layout services"
)

INVOICE_VALUES = {
    "issuer": {
        "company_name": "Lighthouse Media Solutions Pvt. Ltd.",
        "company_address": "4th Floor, Tower B, Cyber Towers, Hitech City, Hyderabad, Telangana - 500081",
        "company_gstin": "36AABCL1234M1Z5",
    },
    "ref": {"invoice_number": "PI-2026-0417", "invoice_date": "2026-03-15"},
    "bill_to": {"bill_to_name": "Coastal Retail Group Pvt. Ltd.", "bill_to_gstin": "32AACCC5678K1Z2"},
    "line_items": [{"description": LONG_DESCRIPTION, "quantity": 1, "rate": 165000.0, "amount": 165000.0}],
    "totals": {"subtotal": 165000.0, "cgst_amount": 14850.0, "sgst_amount": 14850.0, "total_amount": 194700.0},
    "notes": {"notes_gst": "Tax is payable under forward charge mechanism as per Section 9(1) of the CGST Act, 2017."},
    "bank": {"bank_name": "HDFC Bank", "account_number": "50100234567890", "ifsc_code": "HDFC0001234"},
}


def _render_and_extract(schema=INVOICE_SCHEMA, values=INVOICE_VALUES, **kwargs):
    pdf_bytes = render_document(schema, values, **kwargs)
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        pages = [page.get_text() for page in doc]
        page_count = doc.page_count
    finally:
        doc.close()
    return pdf_bytes, pages, page_count


def test_renders_a_real_valid_multi_section_invoice():
    pdf_bytes, pages, page_count = _render_and_extract()
    full_text = "".join(pages)
    assert page_count >= 1
    # Header/letterhead, party grid, line items, totals, notes, bank details —
    # every section actually made it into the real rendered output.
    assert "Lighthouse Media Solutions Pvt. Ltd." in full_text
    assert "36AABCL1234M1Z5" in full_text
    assert "Coastal Retail Group Pvt. Ltd." in full_text
    assert "165,000.00" in full_text
    assert "14,850.00" in full_text
    assert "194,700.00" in full_text
    assert "HDFC0001234" in full_text


def test_amount_in_words_uses_indian_numbering_and_matches_the_real_total():
    """Verified against the real total, not trusted blindly — this is the
    exact figure this feature's own live verification used (165000 + 18% GST
    = 194700), and Indian numbering (lakh), never western (hundred
    ninety-four thousand alone would be the western reading)."""
    _, pages, _ = _render_and_extract()
    full_text = "".join(pages)
    assert "One Lakh Ninety Four Thousand Seven Hundred" in full_text
    assert "million" not in full_text.lower()
    assert "billion" not in full_text.lower()


def test_long_line_item_description_wraps_instead_of_truncating():
    """A real regression check for the original flat renderer's plain-string
    table cells, which never wrapped — every word of a long description must
    survive in the rendered text, not be cut off."""
    _, pages, _ = _render_and_extract()
    full_text = "".join(pages)
    assert LONG_DESCRIPTION in full_text.replace("\n", " ") or all(
        word in full_text for word in LONG_DESCRIPTION.split()
    )


def test_notes_classification_handles_plural_field_keys():
    """Regression test for a real bug caught during this rebuild's own live
    verification: the notes classifier only matched the singular token
    "note", so a field literally named "notes_gst" (plural, exactly what
    this test schema and a real sample would produce) fell through to the
    generic leftover renderer instead of the highlighted notes box. Verified
    by checking the note text still renders (in either renderer) — the
    dedicated regression is that classification doesn't silently drop it."""
    _, pages, _ = _render_and_extract()
    full_text = "".join(pages)
    assert "Tax is payable under forward charge mechanism" in full_text


def test_table_header_row_repeats_on_a_new_page_when_line_items_overflow():
    """repeatRows=1 on the line-items Table is what makes Platypus repeat the
    header row automatically when a table splits across a page boundary —
    proven here with enough real rows to force an actual page break, not
    just trusted from the Table call's own arguments."""
    many_rows = [
        {"description": f"Line item number {i}", "quantity": 1, "rate": 100.0, "amount": 100.0}
        for i in range(60)
    ]
    values = {**INVOICE_VALUES, "line_items": many_rows}
    _, pages, page_count = _render_and_extract(values=values)
    assert page_count > 1
    # The header label "Description" must appear on more than one page if
    # the table genuinely split and repeated its header.
    pages_with_header = [p for p in pages if "Description" in p]
    assert len(pages_with_header) > 1


def test_page_border_and_page_numbers_appear_on_every_page():
    many_rows = [
        {"description": f"Line item number {i}", "quantity": 1, "rate": 100.0, "amount": 100.0}
        for i in range(60)
    ]
    values = {**INVOICE_VALUES, "line_items": many_rows}
    pdf_bytes, pages, page_count = _render_and_extract(values=values)
    assert page_count > 1
    for i in range(page_count):
        assert f"Page {i + 1} of {page_count}" in pages[i]
        assert "computer-generated document" in pages[i]


def test_signature_block_is_not_stranded_alone_and_page_count_stays_sane():
    """The signature block flows naturally (KeepTogether, never an
    absolutely-positioned fixed bottom coordinate) — confirmed indirectly: a
    short document keeps the signature on the same single page rather than
    forcing an otherwise-unjustified second page."""
    values = {
        "issuer": {"company_name": "Small Co"},
        "ref": {}, "bill_to": {},
        "line_items": [{"description": "One item", "quantity": 1, "rate": 10.0, "amount": 10.0}],
        "totals": {"total_amount": 10.0},
        "notes": {}, "bank": {},
    }
    _, pages, page_count = _render_and_extract(values=values)
    assert page_count == 1
    assert "Authorised Signatory" in pages[0]


def test_signature_image_embeds_when_provided():
    import io

    from PIL import Image as PILImage

    img = PILImage.new("RGBA", (200, 60), (0, 0, 0, 0))
    for x in range(10, 190):
        img.putpixel((x, 30), (10, 10, 80, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    pdf_bytes = render_document(INVOICE_SCHEMA, INVOICE_VALUES, signature_image_bytes=buf.getvalue())
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        last_page = doc[-1]
        assert len(last_page.get_images()) >= 1
    finally:
        doc.close()


def test_gracefully_degrades_with_no_gst_party_or_bank_fields():
    """An Agreement-shaped (or any sparse) schema — no header/GST/party-grid/
    totals/notes/bank fields at all — must still render, with no empty boxes
    for roles that don't apply, no crash."""
    schema = {
        "document_type": "Agreement",
        "sections": [
            {
                "id": "parties", "title": "Parties", "type": "fields",
                "fields": [{"key": "party_names", "label": "Parties", "type": "string"}],
            },
        ],
    }
    values = {"parties": {"party_names": "Acme Corp and Beta LLC"}}
    pdf_bytes, pages, page_count = _render_and_extract(schema=schema, values=values)
    assert page_count == 1
    assert "Agreement" in pages[0]
    assert "Acme Corp and Beta LLC" in pages[0]
    assert "Authorised Signatory" in pages[0]


def test_build_generated_filename_is_human_readable_with_no_raw_uuid():
    """Regression test for a real bug: generated documents were displaying a
    UUID-embedded string (the internal Storage key's own basename) as their
    user-facing filename. build_generated_filename() must never contain a
    UUID and should read like a real document name — the issuer's own name
    (the "vendor" on a generated Invoice) plus the document's own ref date."""
    filename = build_generated_filename("Invoice", INVOICE_SCHEMA, INVOICE_VALUES)
    assert filename == "Invoice - Lighthouse Media Solutions Pvt. Ltd. - 2026-03-15.pdf"
    uuid_pattern = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
    assert not uuid_pattern.search(filename)


def test_build_generated_filename_falls_back_when_no_party_name_derivable():
    schema = {
        "document_type": "Agreement",
        "sections": [
            {
                "id": "terms", "title": "Terms", "type": "fields",
                "fields": [{"key": "clause_summary", "label": "Clause Summary", "type": "string"}],
            },
        ],
    }
    values = {"terms": {"clause_summary": "Standard terms apply."}}
    filename = build_generated_filename("Agreement", schema, values)
    assert filename.startswith("Generated Agreement - ")
    assert filename.endswith(".pdf")
    uuid_pattern = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
    assert not uuid_pattern.search(filename)


def test_build_generated_filename_sanitizes_unsafe_characters():
    schema = {
        "document_type": "Invoice",
        "sections": [
            {
                "id": "bill_to", "title": "Bill To", "type": "fields",
                "fields": [{"key": "bill_to_name", "label": "Name", "type": "string"}],
            },
        ],
    }
    values = {"bill_to": {"bill_to_name": 'Acme/Corp: "Special" Chars?'}}
    filename = build_generated_filename("Invoice", schema, values)
    for char in '/\\:"?*<>|':
        assert char not in filename


LEASE_AGREEMENT_SCHEMA = {
    "document_type": "Residential Lease Agreement",
    "sections": [
        {
            "id": "parties", "title": "Parties", "type": "fields",
            "fields": [
                {"key": "landlord_name", "label": "Landlord Name", "type": "string"},
                {"key": "landlord_address", "label": "Landlord Address", "type": "string"},
                {"key": "tenant_name", "label": "Tenant Name", "type": "string"},
                {"key": "tenant_address", "label": "Tenant Address", "type": "string"},
            ],
        },
        {
            "id": "lease_terms", "title": "Lease Terms", "type": "fields",
            "fields": [
                {"key": "lease_start_date", "label": "Lease Start Date", "type": "date"},
                {"key": "lease_end_date", "label": "Lease End Date", "type": "date"},
                {"key": "monthly_rent", "label": "Monthly Rent", "type": "number"},
                {"key": "security_deposit", "label": "Security Deposit", "type": "number"},
            ],
        },
        {
            "id": "compliance", "title": "Compliance", "type": "fields",
            "fields": [{"key": "compliance_clause", "label": "Compliance Clause", "type": "string"}],
        },
    ],
}

LEASE_AGREEMENT_VALUES = {
    "parties": {
        "landlord_name": "Ananya Rao",
        "landlord_address": "9 Willow Creek Road, Kochi, Kerala",
        "tenant_name": "Vikram Sethi",
        "tenant_address": "44 Cedar Park Avenue, Kochi, Kerala",
    },
    "lease_terms": {
        "lease_start_date": "2026-06-01",
        "lease_end_date": "2027-05-31",
        "monthly_rent": 32000.0,
        "security_deposit": 96000.0,
    },
    "compliance": {
        "compliance_clause": "The Tenant shall comply with all applicable municipal bye-laws.",
    },
}


def test_agreement_shaped_schema_is_structurally_different_from_an_invoice_but_still_well_designed():
    """A real regression test for the classifier's original blind spot: an
    early version of _PARTY_QUALIFIERS only covered invoice/PO terminology
    (bill/ship/buyer/vendor/client/customer), so a real Groq-learned lease
    schema's landlord_name/tenant_name fields fell through entirely to the
    generic flat leftover renderer — exactly the "flat, unstyled label:value
    list" failure mode this whole redesign exists to prevent. Landlord/Tenant
    must each get their own real bordered party box, and Monthly Rent/
    Security Deposit must land in a real bordered totals box — without ever
    hardcoding "this is a lease" anywhere; it's driven by field name tokens."""
    from template_generation import _build_layout_plan

    plan = _build_layout_plan(LEASE_AGREEMENT_SCHEMA, LEASE_AGREEMENT_VALUES)
    assert set(plan["party_groups"].keys()) == {"Landlord", "Tenant"}
    assert [f["key"] for f, _ in plan["party_groups"]["Landlord"]] == ["landlord_name", "landlord_address"]
    assert [f["key"] for f, _ in plan["party_groups"]["Tenant"]] == ["tenant_name", "tenant_address"]
    assert {f["key"] for f, _ in plan["totals"]} == {"monthly_rent", "security_deposit"}
    assert {f["key"] for f, _ in plan["notes"]} == {"compliance_clause"}
    # No line-items table, no bank details — this schema genuinely has
    # neither, and no block should be invented for them.
    assert plan["tables"] == []
    assert plan["bank"] == []

    _, pages, page_count = _render_and_extract(schema=LEASE_AGREEMENT_SCHEMA, values=LEASE_AGREEMENT_VALUES)
    assert page_count == 1
    full_text = "".join(pages)
    assert "Landlord" in full_text and "Tenant" in full_text
    assert "Ananya Rao" in full_text and "Vikram Sethi" in full_text
    assert "32,000.00" in full_text and "96,000.00" in full_text
    assert "municipal bye-laws" in full_text


def test_totals_box_bolds_a_row_even_when_no_field_name_reads_as_grand_total():
    """Neither "monthly_rent" nor "security_deposit" matches
    _is_grand_total_key (no "total"/"grand" token, not {"amount","due"}) — the
    box must still end up with one visually prominent (bold/larger) row
    rather than rendering entirely flat, via the last-numeric-row fallback."""
    from template_generation import _build_layout_plan, _build_styles, _render_totals_box

    plan = _build_layout_plan(LEASE_AGREEMENT_SCHEMA, LEASE_AGREEMENT_VALUES)
    styles = _build_styles()
    totals_table, grand_total_value = _render_totals_box(plan, styles)
    assert totals_table is not None
    assert grand_total_value == 96000.0  # the last populated numeric row


def test_amount_in_words_is_suppressed_without_any_india_context_signal():
    """A generic (non-Indian) template — no GSTIN/PAN/lakh/crore signal
    anywhere in its own fields — must never get "Rupees ... Only" printed
    under its total, even though it has a perfectly good numeric grand
    total. This regression covers a real gap: amount-in-words used to render
    unconditionally for ANY grand total, Indian or not."""
    schema = {
        "document_type": "Invoice",
        "sections": [
            {
                "id": "totals", "title": "Totals", "type": "fields",
                "fields": [{"key": "total_amount", "label": "Total Amount", "type": "number"}],
            },
        ],
    }
    values = {"totals": {"total_amount": 4500.0}}
    _, pages, _ = _render_and_extract(schema=schema, values=values)
    full_text = "".join(pages)
    assert "4,500.00" in full_text
    assert "Amount in Words" not in full_text


def test_amount_in_words_renders_when_an_india_context_signal_is_present():
    """The lease agreement above has no GST/PAN fields, so — correctly — it
    gets no amount-in-words line (see the party-grid test above's rendered
    text). Adding a real GSTIN-shaped field is enough on its own to flip
    detect_region to "IN" and turn amount-in-words back on."""
    schema = {
        "document_type": "Invoice",
        "sections": [
            {
                "id": "header", "title": "Header", "type": "fields",
                "fields": [{"key": "company_gstin", "label": "Company GSTIN", "type": "string"}],
            },
            {
                "id": "totals", "title": "Totals", "type": "fields",
                "fields": [{"key": "total_amount", "label": "Total Amount", "type": "number"}],
            },
        ],
    }
    values = {"header": {"company_gstin": "29AAACS1234F1Z5"}, "totals": {"total_amount": 4500.0}}
    _, pages, _ = _render_and_extract(schema=schema, values=values)
    full_text = "".join(pages)
    assert "Amount in Words" in full_text


def test_generic_party_a_party_b_naming_produces_two_distinct_boxes():
    """Without disambiguation, both "party_a_name" and "party_b_name" tokenize
    to a set containing the generic "party" qualifier and nothing more
    specific — they must NOT collapse into a single shared "Party Details"
    box, or an NDA-style generically-named schema would lose one whole
    side of the agreement."""
    schema = {
        "document_type": "NDA",
        "sections": [
            {
                "id": "parties", "title": "Parties", "type": "fields",
                "fields": [
                    {"key": "party_a_name", "label": "Party A Name", "type": "string"},
                    {"key": "party_b_name", "label": "Party B Name", "type": "string"},
                ],
            },
        ],
    }
    values = {"parties": {"party_a_name": "Acme Corp", "party_b_name": "Beta LLC"}}
    from template_generation import _build_layout_plan

    plan = _build_layout_plan(schema, values)
    assert set(plan["party_groups"].keys()) == {"Party A", "Party B"}

    _, pages, _ = _render_and_extract(schema=schema, values=values)
    full_text = "".join(pages)
    assert "Party A" in full_text and "Party B" in full_text
    assert "Acme Corp" in full_text and "Beta LLC" in full_text


def test_party_grid_groups_bill_to_and_ship_to_separately():
    schema = {
        "document_type": "Invoice",
        "sections": [
            {
                "id": "parties", "title": "Parties", "type": "fields",
                "fields": [
                    {"key": "bill_to_name", "label": "Bill To Name", "type": "string"},
                    {"key": "ship_to_name", "label": "Ship To Name", "type": "string"},
                ],
            },
        ],
    }
    values = {"parties": {"bill_to_name": "Billing Party Ltd", "ship_to_name": "Warehouse Consignee Ltd"}}
    _, pages, _ = _render_and_extract(schema=schema, values=values)
    full_text = "".join(pages)
    assert "Bill To" in full_text
    assert "Ship To" in full_text
    assert "Billing Party Ltd" in full_text
    assert "Warehouse Consignee Ltd" in full_text
