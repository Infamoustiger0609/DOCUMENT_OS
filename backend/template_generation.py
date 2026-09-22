"""Renders a brand-new PDF from a template's learned field_schema + filled-in
values (Phase 32 — see CLAUDE.md's Document generation section; redesigned to
a real professional business-document layout in a later pass, documented in
the same CLAUDE.md section).

Uses **ReportLab**, not WeasyPrint: ReportLab is pure Python with zero system
dependencies (no GTK/Pango/Cairo runtime to install, unlike WeasyPrint on
Windows — a real, previously-hit kind of friction in this project, see
CLAUDE.md's System dependencies section for Ghostscript/LibreOffice/Tesseract
already needed).

Deliberately NOT visual-layout cloning of the original sample (exact fonts,
logo position, spacing) — see CLAUDE.md for the explicit scope boundary. This
always produces the same clean, consistent professional design regardless of
what the sample looked like; only the field STRUCTURE (section order, table
columns) carries over.

**How a learned template's arbitrary field_schema becomes a real invoice-
shaped layout**: a template's fields are whatever a language model named them
when it learned the schema — there's no guarantee of a "company_name" or
"gstin" key existing at all, let alone in a fixed position. `_classify_field()`
buckets every flat field into a semantic role (issuer letterhead, ref/date,
one of N party groups, totals, notes, bank details, or "other") purely from
its own key name (tokenized on non-alphanumeric characters, so "vendor_gstin"
is judged on the tokens {"vendor","gstin"}, never a raw substring match that
could misfire on something like "expansion" containing "pan"). A category
with none of a given role's fields simply skips that visual block entirely —
an Agreement template (no line items, no GST fields) degrades gracefully to
header + party details + body sections + signature, no totals box, no empty
placeholder boxes for roles that don't apply.
"""

import re
from datetime import date, datetime, timezone
from io import BytesIO
from typing import Any, Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (
    HRFlowable,
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from india_validation import detect_region, number_to_indian_words

# Same design tokens as the rest of this app (see CLAUDE.md's Design system
# section) — a PDF is a different medium, but there's no reason for it to
# invent its own color language.
_INK = colors.HexColor("#1B1F3B")
_INK_SOFT = colors.HexColor("#5B5F79")
_LINE = colors.HexColor("#E3E0D6")
_SIDEBAR_BG = colors.HexColor("#F1EEE5")
_MUTED = colors.HexColor("#A9A597")
# A pale tint of the app's own "due-soon" amber token — used only for the
# highlighted note/compliance box, never for anything else, so it stays a
# genuinely distinct visual signal rather than decoration.
_NOTE_BG = colors.HexColor("#FBF3E6")
_NOTE_BORDER = colors.HexColor("#D9B679")

_EMPTY_PLACEHOLDER = "—"  # em dash, same "—" convention as the frontend uses for null fields

FOOTER_TEXT = "This is a computer-generated document."


def _format_value(value: Any, field_type: str) -> str:
    if value is None or value == "":
        return _EMPTY_PLACEHOLDER
    if field_type == "number" and isinstance(value, (int, float)):
        return f"{value:,.2f}"
    return str(value)


def _build_styles() -> dict[str, ParagraphStyle]:
    """A deliberate font-size scale — title > company name > section/field
    labels > body/table text > small notes/footer — rather than ad hoc
    per-element sizing."""
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "DocTitle", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=22, leading=26, textColor=_INK, alignment=TA_CENTER, spaceAfter=2,
        ),
        "issuer_name": ParagraphStyle(
            "IssuerName", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=13, leading=16, textColor=_INK,
        ),
        "issuer_address": ParagraphStyle(
            "IssuerAddress", parent=base["Normal"], fontSize=9.5, leading=13, textColor=_INK_SOFT,
        ),
        "issuer_ids": ParagraphStyle(
            "IssuerIds", parent=base["Normal"], fontSize=9, leading=12.5,
            textColor=_INK, alignment=TA_RIGHT,
        ),
        "section_title": ParagraphStyle(
            "SectionTitle", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=11, textColor=_INK, spaceBefore=10, spaceAfter=5,
        ),
        "ref_row": ParagraphStyle(
            "RefRow", parent=base["Normal"], fontSize=9.5, leading=13, textColor=_INK,
        ),
        "party_box_title": ParagraphStyle(
            "PartyBoxTitle", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=10, leading=13, textColor=_INK,
        ),
        "party_box_body": ParagraphStyle(
            "PartyBoxBody", parent=base["Normal"], fontSize=9.5, leading=13, textColor=_INK_SOFT,
        ),
        "table_header": ParagraphStyle(
            "TableHeader", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=9.5, leading=12, textColor=_INK,
        ),
        "table_cell": ParagraphStyle(
            "TableCell", parent=base["Normal"], fontSize=9.5, leading=12.5, textColor=_INK,
        ),
        "table_cell_right": ParagraphStyle(
            "TableCellRight", parent=base["Normal"], fontSize=9.5, leading=12.5,
            textColor=_INK, alignment=TA_RIGHT,
        ),
        "totals_label": ParagraphStyle(
            "TotalsLabel", parent=base["Normal"], fontSize=9.5, textColor=_INK_SOFT,
        ),
        "totals_value": ParagraphStyle(
            "TotalsValue", parent=base["Normal"], fontSize=9.5, textColor=_INK, alignment=TA_RIGHT,
        ),
        "totals_label_bold": ParagraphStyle(
            "TotalsLabelBold", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=11.5, textColor=_INK,
        ),
        "totals_value_bold": ParagraphStyle(
            "TotalsValueBold", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=11.5, textColor=_INK, alignment=TA_RIGHT,
        ),
        "amount_words": ParagraphStyle(
            "AmountWords", parent=base["Normal"], fontName="Helvetica-Oblique",
            fontSize=9.5, textColor=_INK_SOFT, spaceBefore=2,
        ),
        "note_body": ParagraphStyle(
            "NoteBody", parent=base["Normal"], fontSize=9, leading=12.5, textColor=_INK,
        ),
        "bank_body": ParagraphStyle(
            "BankBody", parent=base["Normal"], fontSize=9.5, leading=13, textColor=_INK_SOFT,
        ),
        "signature_company": ParagraphStyle(
            "SignatureCompany", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=10.5, textColor=_INK,
        ),
        "signature_label": ParagraphStyle(
            "SignatureLabel", parent=base["Normal"], fontSize=9, textColor=_INK_SOFT, spaceBefore=2,
        ),
        "body_label": ParagraphStyle(
            "BodyLabel", parent=base["Normal"], fontName="Helvetica-Bold",
            fontSize=9.5, textColor=_INK,
        ),
        "body_value": ParagraphStyle(
            "BodyValue", parent=base["Normal"], fontSize=9.5, textColor=_INK_SOFT,
        ),
    }


# ---------------------------------------------------------------------------
# Field classification — turning an arbitrary learned schema's flat fields
# into semantic roles (issuer letterhead, ref/date, party group(s), totals,
# notes, bank details, or leftover "other"), by tokenizing each field's own
# key name. Never touches table-section columns (line items keep their
# existing dedicated rendering).
# ---------------------------------------------------------------------------


def _tokens(key: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", key.lower()) if t}


def _has_any(tokens: set[str], *candidates: str) -> bool:
    return any(c in tokens for c in candidates)


# Invoice/PO-style qualifiers plus common two-party legal-document role
# names (lease, NDA, employment, loan, consignment) — this list is what lets
# a genuinely different-shaped template (an Agreement, not just an Invoice)
# still get a real bordered party grid instead of degrading to a flat leftover
# dump. Found empirically: an early version of this list only covered
# invoice/PO terminology, so a real Groq-learned lease-agreement schema
# (landlord_name/tenant_name/...) fell through entirely to the generic
# leftover renderer — see CLAUDE.md's Document generation section.
_PARTY_QUALIFIERS = (
    "bill", "ship", "buyer", "vendor", "client", "customer", "party",
    "landlord", "tenant", "lessor", "lessee", "licensor", "licensee",
    "employer", "employee", "contractor", "consultant", "borrower",
    "lender", "guarantor", "disclosing", "receiving", "recipient",
    "consignor", "consignee", "purchaser", "owner",
)

_PARTY_GROUP_LABELS = [
    ({"ship"}, "Ship To"),
    ({"bill"}, "Bill To"),
    ({"buyer"}, "Buyer"),
    ({"vendor"}, "Vendor"),
    ({"client"}, "Client"),
    ({"customer"}, "Customer"),
    ({"landlord"}, "Landlord"),
    ({"tenant"}, "Tenant"),
    ({"lessor"}, "Lessor"),
    ({"lessee"}, "Lessee"),
    ({"licensor"}, "Licensor"),
    ({"licensee"}, "Licensee"),
    ({"employer"}, "Employer"),
    ({"employee"}, "Employee"),
    ({"contractor"}, "Contractor"),
    ({"consultant"}, "Consultant"),
    ({"borrower"}, "Borrower"),
    ({"lender"}, "Lender"),
    ({"guarantor"}, "Guarantor"),
    ({"disclosing"}, "Disclosing Party"),
    ({"receiving"}, "Receiving Party"),
    ({"recipient"}, "Recipient"),
    ({"consignor"}, "Consignor"),
    ({"consignee"}, "Consignee"),
    ({"purchaser"}, "Purchaser"),
    ({"owner"}, "Owner"),
]


def _is_party_qualified(tokens: set[str]) -> bool:
    return _has_any(tokens, *_PARTY_QUALIFIERS)


def _party_group_label(tokens: set[str]) -> str:
    for qualifier_tokens, label in _PARTY_GROUP_LABELS:
        if tokens & qualifier_tokens:
            return label
    # Generic "party" naming (party_a/party_b, first_party/second_party)
    # still needs to distinguish the two sides — otherwise both collapse
    # into one shared "Party Details" box instead of two separate ones.
    if "a" in tokens:
        return "Party A"
    if "b" in tokens:
        return "Party B"
    if "first" in tokens:
        return "First Party"
    if "second" in tokens:
        return "Second Party"
    return "Party Details"


def _is_identifier_like(tokens: set[str]) -> bool:
    is_gstin = "gstin" in tokens or ("gst" in tokens and _has_any(tokens, "no", "number"))
    is_pan = "pan" in tokens
    is_cin = "cin" in tokens
    return is_gstin or is_pan or is_cin


def _classify_field(key: str) -> str:
    tokens = _tokens(key)
    party_qualified = _is_party_qualified(tokens)

    if _is_identifier_like(tokens):
        return "party_identifier" if party_qualified else "issuer"
    if _has_any(tokens, "company", "business", "issuer", "seller") and _has_any(tokens, "name", "address"):
        return "issuer"
    if _has_any(tokens, "bank", "ifsc", "swift", "iban") or ({"account"} <= tokens and _has_any(tokens, "no", "number")):
        return "bank"
    if _has_any(tokens, "note", "notes", "term", "terms", "compliance", "declaration", "remark", "remarks"):
        return "notes"
    if party_qualified:
        return "party"
    if _has_any(tokens, "number", "no") and not _has_any(tokens, "account", "phone", "mobile"):
        return "ref"
    if "date" in tokens and not _has_any(tokens, "due", "expiry", "delivery", "filing"):
        return "ref"
    if _has_any(
        tokens,
        "subtotal", "subtotals", "total", "totals", "tax", "taxes", "cgst", "sgst", "igst",
        "amount", "rent", "deposit", "fee", "fees", "charge", "charges", "premium", "price",
        "salary", "wages", "compensation", "consideration", "balance", "stamp",
    ):
        return "totals"
    return "other"


def _issuer_role(key: str) -> str:
    tokens = _tokens(key)
    if "name" in tokens:
        return "name"
    if "address" in tokens:
        return "address"
    return "identifier"


def _is_grand_total_key(key: str) -> bool:
    tokens = _tokens(key)
    if "total" in tokens or "grand" in tokens:
        return True
    return {"amount", "due"} <= tokens


def _build_layout_plan(field_schema: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "issuer": [], "ref": [], "party_groups": {}, "totals": [],
        "notes": [], "bank": [], "other": [], "tables": [],
    }
    for section in field_schema.get("sections", []):
        section_id = section["id"]
        if section["type"] == "table":
            plan["tables"].append((section, values.get(section_id) or []))
            continue

        section_values = values.get(section_id) or {}
        leftover: list[tuple[dict, Any]] = []
        for field in section["fields"]:
            value = section_values.get(field["key"])
            bucket = _classify_field(field["key"])
            entry = (field, value)
            if bucket == "issuer":
                plan["issuer"].append(entry)
            elif bucket in ("party", "party_identifier"):
                label = _party_group_label(_tokens(field["key"]))
                plan["party_groups"].setdefault(label, []).append(entry)
            elif bucket == "ref":
                plan["ref"].append(entry)
            elif bucket == "totals":
                plan["totals"].append(entry)
            elif bucket == "notes":
                plan["notes"].append(entry)
            elif bucket == "bank":
                plan["bank"].append(entry)
            else:
                leftover.append(entry)
        if leftover:
            plan["other"].append((section, leftover))
    return plan


# ---------------------------------------------------------------------------
# Per-block rendering — each returns None when it has nothing to show, so
# render_document() can skip that whole visual block (no empty bordered box
# for a role a given template simply doesn't have).
# ---------------------------------------------------------------------------


def _render_header(plan: dict[str, Any], styles: dict[str, ParagraphStyle]):
    issuer_name_value = None
    left_flow: list[Any] = []
    right_flow: list[Any] = []
    for field, value in plan["issuer"]:
        if value in (None, ""):
            continue
        role = _issuer_role(field["key"])
        if role == "name":
            issuer_name_value = value
            left_flow.insert(0, Paragraph(str(value), styles["issuer_name"]))
        elif role == "address":
            left_flow.append(Paragraph(str(value).replace("\n", "<br/>"), styles["issuer_address"]))
        else:
            right_flow.append(Paragraph(f"<b>{field['label']}:</b> {value}", styles["issuer_ids"]))

    if not left_flow and not right_flow:
        return None, None

    table = Table([[left_flow or "", right_flow or ""]], colWidths=[105 * mm, None])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return table, issuer_name_value


def _render_ref_row(plan: dict[str, Any], styles: dict[str, ParagraphStyle]):
    populated = [(f, v) for f, v in plan["ref"] if v not in (None, "")]
    if not populated:
        return None
    cells = [Paragraph(f"<b>{f['label']}:</b> {_format_value(v, f['type'])}", styles["ref_row"]) for f, v in populated]
    row = Table([cells], colWidths=[None] * len(cells))
    row.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("ALIGN", (0, 0), (0, 0), "LEFT"),
            ]
        )
    )
    return row


def _render_party_grid(plan: dict[str, Any], styles: dict[str, ParagraphStyle]):
    boxes = []
    for label, entries in plan["party_groups"].items():
        rows = [[Paragraph(label, styles["party_box_title"])]]
        for field, value in entries:
            rows.append([Paragraph(f"<b>{field['label']}:</b> {_format_value(value, field['type'])}", styles["party_box_body"])])
        box = Table(rows, colWidths=[None])
        box.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.75, _LINE),
                    ("BACKGROUND", (0, 0), (-1, 0), _SIDEBAR_BG),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.75, _LINE),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("LEFTPADDING", (0, 0), (-1, -1), 7),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ]
            )
        )
        boxes.append(box)

    if not boxes:
        return None
    if len(boxes) == 1:
        return boxes[0]

    outer = Table([boxes], colWidths=[None] * len(boxes))
    outer.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (1, 0), (1, 0), 0),
                ("RIGHTPADDING", (0, 0), (0, 0), 4 * mm),
            ]
        )
    )
    return outer


def _render_line_items_table(section: dict[str, Any], rows: list[dict[str, Any]], styles: dict[str, ParagraphStyle]) -> Table:
    """Every cell is a Paragraph, not a plain string — this is what lets
    ReportLab auto-wrap a long description and expand that row's height to
    fit, rather than truncating or overlapping adjacent rows."""
    columns = section["columns"]
    numeric_cols = {i for i, col in enumerate(columns) if col["type"] == "number"}

    header = [Paragraph(col["label"], styles["table_header"]) for col in columns]
    table_data = [header]
    for row in rows:
        cells = []
        for i, col in enumerate(columns):
            style = styles["table_cell_right"] if i in numeric_cols else styles["table_cell"]
            cells.append(Paragraph(_format_value(row.get(col["key"]), col["type"]), style))
        table_data.append(cells)
    if len(table_data) == 1:
        table_data.append([Paragraph(_EMPTY_PLACEHOLDER, styles["table_cell"])] * len(columns))

    table = Table(table_data, hAlign="LEFT", repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _SIDEBAR_BG),
                ("GRID", (0, 0), (-1, -1), 0.5, _LINE),
                ("LINEBELOW", (0, 0), (-1, 0), 1, _INK),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def _render_totals_box(plan: dict[str, Any], styles: dict[str, ParagraphStyle]):
    populated = [(f, v) for f, v in plan["totals"] if v not in (None, "")]
    if not populated:
        return None, None

    grand_flags = [_is_grand_total_key(field["key"]) for field, _ in populated]
    if not any(grand_flags):
        # No field's key obviously reads as "the final total" (e.g. a lease's
        # Monthly Rent + Security Deposit — neither is a "total" by name) —
        # still give the box at least one visually prominent line rather
        # than rendering it entirely flat. The last populated numeric row is
        # the most likely candidate (typically the final figure in reading
        # order, the way Subtotal/Tax/Total already reads top to bottom).
        for i in range(len(populated) - 1, -1, -1):
            if isinstance(populated[i][1], (int, float)):
                grand_flags[i] = True
                break

    rows = []
    grand_total_value = None
    for (field, value), is_grand in zip(populated, grand_flags):
        label_style = styles["totals_label_bold"] if is_grand else styles["totals_label"]
        value_style = styles["totals_value_bold"] if is_grand else styles["totals_value"]
        rows.append([Paragraph(field["label"], label_style), Paragraph(_format_value(value, field["type"]), value_style)])
        if is_grand and isinstance(value, (int, float)):
            grand_total_value = value

    style_commands = [
        ("BOX", (0, 0), (-1, -1), 1, _INK),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]
    if len(rows) > 1:
        style_commands.append(("LINEABOVE", (0, -1), (-1, -1), 1, _INK))
    table = Table(rows, colWidths=[45 * mm, 40 * mm], hAlign="RIGHT")
    table.setStyle(TableStyle(style_commands))
    return table, grand_total_value


def _render_amount_in_words(
    grand_total_value: Optional[float], is_india_context: bool, styles: dict[str, ParagraphStyle]
):
    if grand_total_value is None or not is_india_context:
        return None
    words = number_to_indian_words(grand_total_value)
    return Paragraph(f"<b>Amount in Words:</b> Rupees {words} Only", styles["amount_words"])


def _render_notes_box(plan: dict[str, Any], styles: dict[str, ParagraphStyle]):
    populated = [(f, v) for f, v in plan["notes"] if v not in (None, "")]
    if not populated:
        return None
    rows = [[Paragraph(f"<b>{f['label']}:</b> {_format_value(v, f['type'])}", styles["note_body"])] for f, v in populated]
    box = Table(rows, colWidths=[None])
    box.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.75, _NOTE_BORDER),
                ("BACKGROUND", (0, 0), (-1, -1), _NOTE_BG),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return box


def _render_bank_details(plan: dict[str, Any], styles: dict[str, ParagraphStyle]):
    populated = [(f, v) for f, v in plan["bank"] if v not in (None, "")]
    if not populated:
        return None
    rows = [[Paragraph(f"<b>{f['label']}:</b> {_format_value(v, f['type'])}", styles["bank_body"])] for f, v in populated]
    box = Table(rows, colWidths=[None])
    box.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.75, _LINE),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return box


def _render_signature_block(
    issuer_name_value: Optional[str],
    signature_image_bytes: Optional[bytes],
    styles: dict[str, ParagraphStyle],
) -> list[Any]:
    elements: list[Any] = []
    if issuer_name_value:
        elements.append(Paragraph(f"For {issuer_name_value}", styles["signature_company"]))

    if signature_image_bytes:
        try:
            reader = ImageReader(BytesIO(signature_image_bytes))
            img_width, img_height = reader.getSize()
            target_width = 40 * mm
            target_height = target_width * img_height / img_width if img_width else 15 * mm
            elements.append(Spacer(1, 3 * mm))
            elements.append(Image(BytesIO(signature_image_bytes), width=target_width, height=target_height))
        except Exception:
            # A corrupt/unreadable saved signature shouldn't fail the whole
            # document — fall back to blank space for a physical signature.
            elements.append(Spacer(1, 14 * mm))
    else:
        elements.append(Spacer(1, 14 * mm))  # room for a physical signature/stamp

    elements.append(Paragraph("Authorised Signatory", styles["signature_label"]))
    return elements


def _render_leftover_section(section: dict[str, Any], fields: list[tuple[dict, Any]], styles: dict[str, ParagraphStyle]):
    rows = [
        [Paragraph(f"{field['label']}:", styles["body_label"]), Paragraph(_format_value(value, field["type"]), styles["body_value"])]
        for field, value in fields
    ]
    table = Table(rows, colWidths=[45 * mm, None], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


# ---------------------------------------------------------------------------
# Page decoration: outer border + footer + "Page X of Y" — drawn in a
# deferred pass over every buffered page once the true final page count is
# known, the standard ReportLab pattern for anything that needs the total
# page count (a single in-progress page has no way to know how many pages
# will follow it). Never drawn eagerly per-page as content streams.
# ---------------------------------------------------------------------------


class _BorderedNumberedCanvas(pdfcanvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states: list[dict] = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_page_decoration(total_pages)
            super().showPage()
        super().save()

    def _draw_page_decoration(self, total_pages: int) -> None:
        width, height = A4
        margin = 10 * mm
        self.saveState()
        self.setStrokeColor(_LINE)
        self.setLineWidth(0.75)
        self.rect(margin, margin, width - 2 * margin, height - 2 * margin)

        self.setFont("Helvetica", 7.5)
        self.setFillColor(_MUTED)
        self.drawCentredString(width / 2, margin - 5, FOOTER_TEXT)
        self.drawRightString(width - margin - 2, margin - 5, f"Page {self._pageNumber} of {total_pages}")
        self.restoreState()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def render_document(
    field_schema: dict[str, Any],
    values: dict[str, Any],
    *,
    signature_image_bytes: Optional[bytes] = None,
) -> bytes:
    """`values` must already be normalized to field_schema's exact shape —
    see template_learning.normalize_values(). `signature_image_bytes` is the
    generating user's own saved Phase-31 signature image, if they have one —
    passed in by the caller (templates_router.py), which is the only place
    that talks to Storage/the DB; this module stays a pure renderer. Returns
    the new PDF's bytes."""
    styles = _build_styles()
    plan = _build_layout_plan(field_schema, values)
    # Same deterministic region check the main upload pipeline already uses
    # (structured_extraction.py, via india_validation.detect_region) — a real
    # GSTIN/PAN match or 2+ India-specific keyword hits, never guessed from
    # e.g. a city name alone. Gates amount-in-words: a non-Indian template
    # (or one with no GST/PAN/lakh/crore signal anywhere in its own fields)
    # should never get "Rupees ... Only" printed under its total.
    is_india_context = detect_region(values_to_text(field_schema, values)) == "IN"

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        topMargin=24 * mm,
        bottomMargin=24 * mm,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        title=field_schema.get("document_type", "Document"),
    )

    story: list[Any] = [Paragraph(field_schema.get("document_type", "Document"), styles["title"])]
    story.append(Spacer(1, 5 * mm))

    header, issuer_name_value = _render_header(plan, styles)
    if header is not None:
        story.append(header)
        story.append(Spacer(1, 3 * mm))
        story.append(HRFlowable(width="100%", thickness=1, color=_INK, spaceAfter=4))

    ref_row = _render_ref_row(plan, styles)
    if ref_row is not None:
        story.append(ref_row)
        story.append(Spacer(1, 5 * mm))

    party_grid = _render_party_grid(plan, styles)
    if party_grid is not None:
        story.append(party_grid)
        story.append(Spacer(1, 6 * mm))

    for section, rows in plan["tables"]:
        story.append(Paragraph(section["title"], styles["section_title"]))
        story.append(_render_line_items_table(section, rows, styles))
        story.append(Spacer(1, 4 * mm))

    totals_table, grand_total_value = _render_totals_box(plan, styles)
    if totals_table is not None:
        wrapper = Table([[totals_table]], colWidths=[None])
        wrapper.setStyle(TableStyle([("ALIGN", (0, 0), (0, 0), "RIGHT")]))
        story.append(wrapper)
        story.append(Spacer(1, 3 * mm))

    amount_words = _render_amount_in_words(grand_total_value, is_india_context, styles)
    if amount_words is not None:
        story.append(amount_words)
        story.append(Spacer(1, 5 * mm))

    notes_box = _render_notes_box(plan, styles)
    if notes_box is not None:
        story.append(notes_box)
        story.append(Spacer(1, 5 * mm))

    bank_box = _render_bank_details(plan, styles)
    if bank_box is not None:
        story.append(Paragraph("Bank Details", styles["section_title"]))
        story.append(bank_box)
        story.append(Spacer(1, 5 * mm))

    for section, leftover_fields in plan["other"]:
        story.append(Paragraph(section["title"], styles["section_title"]))
        story.append(_render_leftover_section(section, leftover_fields, styles))
        story.append(Spacer(1, 4 * mm))

    # Signature block: appended as an ordinary flowable at the natural end of
    # the story, wrapped in KeepTogether so its own lines never split across
    # a page break — but never absolutely positioned at a fixed page-bottom
    # coordinate. Platypus places it wherever it naturally fits: right after
    # the content above on the current page if there's room, or at the top
    # of a fresh page if not — it is never stranded alone in the middle of
    # an otherwise-blank page, and never overlaps the content above it.
    story.append(Spacer(1, 8 * mm))
    story.append(KeepTogether(_render_signature_block(issuer_name_value, signature_image_bytes, styles)))

    doc.build(story, canvasmaker=_BorderedNumberedCanvas)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Turning generated values into a plain-text summary and a best-effort
# deadline date, so a generated document is a fully ordinary Document row —
# usable by the per-document chat / Copilot full-text search / Tasks page
# exactly like any uploaded-and-processed document, with no separate code
# path needed anywhere else in the app.
# ---------------------------------------------------------------------------


def values_to_text(field_schema: dict[str, Any], values: dict[str, Any]) -> str:
    lines = [field_schema.get("document_type", "Document")]
    for section in field_schema.get("sections", []):
        section_id = section["id"]
        lines.append(f"\n{section['title']}:")
        if section["type"] == "table":
            for row in values.get(section_id) or []:
                parts = [
                    f"{col['label']}: {row.get(col['key'])}"
                    for col in section["columns"]
                    if row.get(col["key"]) is not None
                ]
                if parts:
                    lines.append("  - " + ", ".join(parts))
        else:
            section_values = values.get(section_id) or {}
            for field in section["fields"]:
                value = section_values.get(field["key"])
                if value is not None:
                    lines.append(f"  {field['label']}: {value}")
    return "\n".join(lines)


# Common deadline-like field keys, in priority order — same field names
# structured_extraction.DEADLINE_FIELD already maps per category, generalized
# here since a learned template's field keys aren't guaranteed to match those
# exactly (the model names them fresh from the sample each time).
_DEADLINE_FIELD_CANDIDATES = ("due_date", "expiry_date", "delivery_date", "filing_date")


def guess_deadline_date(values: dict[str, Any]) -> Optional[date]:
    for section_values in values.values():
        if not isinstance(section_values, dict):
            continue
        for candidate in _DEADLINE_FIELD_CANDIDATES:
            value = section_values.get(candidate)
            if isinstance(value, str):
                try:
                    return datetime.strptime(value[:10], "%Y-%m-%d").date()
                except ValueError:
                    continue
    return None


# ---------------------------------------------------------------------------
# A human-readable filename for a generated document. A UUID belongs only in
# the internal Storage key (templates_router.py's storage_path) — never in
# what a user sees as the file's name. Reuses the same field-classification
# machinery the renderer itself uses (_build_layout_plan/_classify_field), so
# this stays correct for an arbitrary learned schema instead of assuming any
# fixed field name exists.
# ---------------------------------------------------------------------------

_FILENAME_UNSAFE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sanitize_filename_part(text: str, max_len: int = 60) -> str:
    text = _FILENAME_UNSAFE_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len].strip()


def _find_party_name(plan: dict[str, Any]) -> Optional[str]:
    # Prefer the issuer's own name — the "vendor" on a generated Invoice —
    # since that's what an uploaded Invoice's own extraction already calls
    # vendor_name (see CLAUDE.md's Categories section).
    for field, value in plan["issuer"]:
        if value and _issuer_role(field["key"]) == "name":
            return str(value)
    # Otherwise, the first name-like field in any party group (Bill To, etc.).
    for entries in plan["party_groups"].values():
        for field, value in entries:
            if value and "name" in _tokens(field["key"]):
                return str(value)
    return None


def _find_document_date(plan: dict[str, Any]) -> Optional[str]:
    # The ref bucket (invoice number/date, etc.) is where a document-level
    # date field (as opposed to a due/expiry/delivery deadline) classifies to.
    for field, value in plan["ref"]:
        if value and field.get("type") == "date":
            return str(value)[:10]
    return None


def build_generated_filename(category: str, field_schema: dict[str, Any], values: dict[str, Any]) -> str:
    """E.g. "Invoice - Coastal Retail Group Pvt. Ltd. - 2026-03-15.pdf". Falls
    back to "Generated {category} - {timestamp}.pdf" only when no party name
    can be derived from the filled-in values at all (a schema with no
    party/vendor-shaped field, or none of them filled in)."""
    plan = _build_layout_plan(field_schema, values)
    party_name = _find_party_name(plan)

    if party_name:
        parts = [category, _sanitize_filename_part(party_name)]
        doc_date = _find_document_date(plan)
        if doc_date:
            parts.append(doc_date)
        # Only trim a trailing dot/dash/space off the fully-assembled name
        # (avoids an awkward "...pdf" right after the extension) — never
        # per-part, which would otherwise mangle a legitimate abbreviation
        # like "Pvt. Ltd." into "Pvt. Ltd" whenever something follows it.
        name = " - ".join(part for part in parts if part).rstrip(" .-")
        if name:
            return f"{name}.pdf"

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H%M%S")
    return f"Generated {category} - {timestamp}.pdf"
