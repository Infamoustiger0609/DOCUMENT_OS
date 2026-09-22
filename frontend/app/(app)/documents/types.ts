import {
  Banknote,
  ClipboardList,
  FileCheck2,
  FileSignature,
  File as FileIcon,
  Landmark,
  Receipt,
  type LucideIcon,
} from "lucide-react";

export type Category =
  | "Agreement"
  | "Invoice"
  | "GST Document"
  | "GST Filing"
  | "Bank Statement"
  | "Purchase Order"
  | "Other";

// Keep in sync with backend/classification.py's CURRENT_CLASSIFICATION_VERSION.
// Used to decide whether an "Other"-classified document predates a category that
// might actually fit it (see needsReprocessing() in DocumentDetailModal.tsx).
// Bumped to 3 in Phase 30 for the addition of "GST Filing".
export const CURRENT_CLASSIFICATION_VERSION = 3;

export interface DocumentRow {
  id: string;
  filename: string;
  category: Category | null;
  upload_date: string;
  storage_path: string;
  // Omitted by the list endpoints (GET /documents, GET /documents/deadlines) —
  // only the single-document detail fetch (GET /documents/{id}) includes it.
  raw_text?: string | null;
  extracted_json: Record<string, unknown> | null;
  deadline_date: string | null;
  status: string;
  error_message: string | null;
  classification_reasoning: string | null;
  classification_version: number | null;
  // Phase 32 — see CLAUDE.md's Document generation section. Set only on a
  // document produced by POST /templates/{id}/generate. Only present on the
  // detail fetch (DocumentOut), like raw_text above.
  generated_from_template_id?: string | null;
}

export const CATEGORIES: Category[] = [
  "Agreement",
  "Invoice",
  "GST Document",
  "GST Filing",
  "Bank Statement",
  "Purchase Order",
  "Other",
];

export const CATEGORY_ICONS: Record<string, LucideIcon> = {
  Agreement: FileSignature,
  Invoice: Receipt,
  "GST Document": Landmark,
  "GST Filing": FileCheck2,
  "Bank Statement": Banknote,
  "Purchase Order": ClipboardList,
  Other: FileIcon,
};

export const FIELD_LABELS: Record<string, Record<string, string>> = {
  Agreement: {
    party_names: "Party Names",
    effective_date: "Effective Date",
    expiry_date: "Expiry Date",
    auto_renewal: "Auto-Renewal",
    renewal_notice_period: "Renewal Notice Period",
    termination_clause_summary: "Termination Clause Summary",
    key_obligations: "Key Obligations",
    penalty_clauses: "Penalty Clauses",
    // India-specific (Phase 30) — only ever populated when detect_region()
    // found this to be an Indian agreement; see CLAUDE.md.
    party_pan: "Party PAN",
    party_pan_valid: "Party PAN Valid",
    party_gstin: "Party GSTIN",
    party_gstin_valid: "Party GSTIN Valid",
    stamp_duty: "Stamp Duty",
  },
  Invoice: {
    vendor_name: "Vendor Name",
    invoice_number: "Invoice Number",
    invoice_date: "Invoice Date",
    due_date: "Due Date",
    gst_number: "GST Number",
    gst_number_valid: "GST Number Valid",
    line_items: "Line Items",
    subtotal: "Subtotal",
    tax_breakdown: "Tax Breakdown",
    amount: "Total Amount",
    // India-specific (Phase 30). pan/place_of_supply/tds_amount/tcs_amount are
    // only ever populated for a document detected as Indian; cgst/sgst/igst/
    // tax_type are derived deterministically from tax_breakdown, so they only
    // populate when a matching CGST/SGST/IGST-labeled tax line was found.
    pan: "PAN",
    pan_valid: "PAN Valid",
    place_of_supply: "Place of Supply",
    cgst_amount: "CGST",
    sgst_amount: "SGST",
    igst_amount: "IGST",
    tax_type: "Tax Type",
    tds_amount: "TDS Amount",
    tcs_amount: "TCS Amount",
  },
  "GST Document": {
    gstin: "GSTIN",
    gstin_valid: "GSTIN Valid",
    period: "Period",
    tax_amount: "Tax Amount",
    filing_date: "Filing Date",
  },
  "GST Filing": {
    gstin: "GSTIN",
    gstin_valid: "GSTIN Valid",
    return_type: "Return Type",
    period: "Period",
    filing_date: "Filing Date",
    arn: "ARN",
    cgst_amount: "CGST",
    sgst_amount: "SGST",
    igst_amount: "IGST",
    tax_type: "Tax Type",
    tax_liability: "Tax Liability",
    late_fee: "Late Fee",
  },
  "Purchase Order": {
    po_number: "PO Number",
    vendor: "Vendor",
    buyer: "Buyer",
    delivery_date: "Delivery Date",
    line_items: "Line Items",
    subtotal: "Subtotal",
    tax_breakdown: "Tax Breakdown",
    total_amount: "Total Amount",
    // India-specific (Phase 30) — see the Invoice entry above for the same
    // populate-only-when-detected reasoning.
    vendor_gstin: "Vendor GSTIN",
    vendor_gstin_valid: "Vendor GSTIN Valid",
    buyer_gstin: "Buyer GSTIN",
    buyer_gstin_valid: "Buyer GSTIN Valid",
    place_of_supply: "Place of Supply",
    cgst_amount: "CGST",
    sgst_amount: "SGST",
    igst_amount: "IGST",
    tax_type: "Tax Type",
  },
};

export type FieldRenderAs = "list" | "prose" | "boolean";

export interface FieldSection {
  title: string;
  fields: string[];
  renderAs?: Partial<Record<string, FieldRenderAs>>;
}

// Groups extracted_json fields into labeled sections for the document detail view,
// instead of a flat field dump.
export const FIELD_SECTIONS: Record<string, FieldSection[]> = {
  Agreement: [
    {
      title: "Parties & dates",
      fields: ["party_names", "effective_date", "expiry_date"],
      renderAs: { party_names: "list" },
    },
    {
      title: "Renewal",
      fields: ["auto_renewal", "renewal_notice_period"],
      renderAs: { auto_renewal: "boolean" },
    },
    {
      title: "Key obligations",
      fields: ["key_obligations"],
      renderAs: { key_obligations: "list" },
    },
    {
      title: "Penalty clauses",
      fields: ["termination_clause_summary", "penalty_clauses"],
      renderAs: { termination_clause_summary: "prose", penalty_clauses: "prose" },
    },
    {
      title: "Indian compliance",
      fields: ["party_pan", "party_pan_valid", "party_gstin", "party_gstin_valid", "stamp_duty"],
      renderAs: { party_pan_valid: "boolean", party_gstin_valid: "boolean" },
    },
  ],
  Invoice: [
    {
      title: "Billing details",
      fields: ["vendor_name", "invoice_number", "invoice_date", "due_date", "gst_number", "gst_number_valid"],
      renderAs: { gst_number_valid: "boolean" },
    },
    { title: "Line items", fields: ["line_items"] },
    { title: "Amount & tax", fields: ["subtotal", "tax_breakdown", "amount"] },
    {
      title: "Indian tax & compliance",
      fields: [
        "pan",
        "pan_valid",
        "place_of_supply",
        "cgst_amount",
        "sgst_amount",
        "igst_amount",
        "tax_type",
        "tds_amount",
        "tcs_amount",
      ],
      renderAs: { pan_valid: "boolean" },
    },
  ],
  "GST Document": [
    { title: "Filing details", fields: ["gstin", "gstin_valid", "period", "filing_date"], renderAs: { gstin_valid: "boolean" } },
    { title: "Tax", fields: ["tax_amount"] },
  ],
  "GST Filing": [
    {
      title: "Filing details",
      fields: ["gstin", "gstin_valid", "return_type", "period", "filing_date", "arn"],
      renderAs: { gstin_valid: "boolean" },
    },
    {
      title: "Tax",
      fields: ["cgst_amount", "sgst_amount", "igst_amount", "tax_type", "tax_liability", "late_fee"],
    },
  ],
  "Purchase Order": [
    { title: "Order details", fields: ["po_number", "vendor", "buyer", "delivery_date"] },
    { title: "Line items", fields: ["line_items"] },
    { title: "Amount & tax", fields: ["subtotal", "tax_breakdown", "total_amount"] },
    {
      title: "Indian tax & compliance",
      fields: [
        "vendor_gstin",
        "vendor_gstin_valid",
        "buyer_gstin",
        "buyer_gstin_valid",
        "place_of_supply",
        "cgst_amount",
        "sgst_amount",
        "igst_amount",
        "tax_type",
      ],
      renderAs: { vendor_gstin_valid: "boolean", buyer_gstin_valid: "boolean" },
    },
  ],
};

// Falls back to this wherever a caller doesn't have the real user preference in
// scope yet (e.g. before /auth/me resolves) — matches the backend column's default.
export const DEFAULT_DUE_SOON_THRESHOLD_DAYS = 30;

export type DeadlineUrgency = "overdue" | "upcoming" | "none";

export function getDeadlineUrgency(
  deadlineDate: string | null,
  thresholdDays: number = DEFAULT_DUE_SOON_THRESHOLD_DAYS
): DeadlineUrgency {
  if (!deadlineDate) return "none";

  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const deadline = new Date(deadlineDate);
  deadline.setHours(0, 0, 0, 0);

  const diffDays = Math.round((deadline.getTime() - today.getTime()) / (1000 * 60 * 60 * 24));
  if (diffDays < 0) return "overdue";
  if (diffDays <= thresholdDays) return "upcoming";
  return "none";
}

export function sortByDeadlineAscending(docs: DocumentRow[]): DocumentRow[] {
  return [...docs].sort((a, b) => {
    if (!a.deadline_date && !b.deadline_date) return 0;
    if (!a.deadline_date) return 1;
    if (!b.deadline_date) return -1;
    return a.deadline_date.localeCompare(b.deadline_date);
  });
}

export function formatDate(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function formatRelativeDeadline(
  deadlineDate: string | null,
  thresholdDays: number = DEFAULT_DUE_SOON_THRESHOLD_DAYS
): string {
  if (!deadlineDate) return "";

  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const deadline = new Date(deadlineDate);
  deadline.setHours(0, 0, 0, 0);

  const diffDays = Math.round((deadline.getTime() - today.getTime()) / (1000 * 60 * 60 * 24));

  if (diffDays < 0) {
    const n = Math.abs(diffDays);
    return `${n} day${n === 1 ? "" : "s"} overdue`;
  }
  if (diffDays === 0) return "Due today";
  if (diffDays <= thresholdDays) return `Due in ${diffDays} day${diffDays === 1 ? "" : "s"}`;
  return `Renews in ${diffDays} day${diffDays === 1 ? "" : "s"}`;
}

// Finer-grained than DeadlineUrgency, for the /deadlines page's grouped sections
// (Overdue / Due this week / Due soon). "due-this-week" is a fixed 7-day
// sub-window; "due-this-month"'s upper bound is the configurable threshold, not
// a hardcoded 30 — if threshold <= 7, that bucket is simply always empty, which
// is correct (there's no room left in the window for a second bucket).
// "later" (beyond the threshold) and documents with no deadline_date are
// excluded from the /deadlines page entirely, not shown as a fourth bucket.
export type DeadlineBucket = "overdue" | "due-this-week" | "due-this-month" | "later";

export function getDeadlineBucket(
  deadlineDate: string | null,
  thresholdDays: number = DEFAULT_DUE_SOON_THRESHOLD_DAYS
): DeadlineBucket {
  if (!deadlineDate) return "later";

  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const deadline = new Date(deadlineDate);
  deadline.setHours(0, 0, 0, 0);

  const diffDays = Math.round((deadline.getTime() - today.getTime()) / (1000 * 60 * 60 * 24));
  if (diffDays < 0) return "overdue";
  if (diffDays <= 7) return "due-this-week";
  if (diffDays <= thresholdDays) return "due-this-month";
  return "later";
}

// A short "party/vendor" line for list rows (e.g. the /deadlines page), derived
// from whatever extracted_json already has for the document's category — never
// fabricated, just returns null if there's nothing suitable to show.
export function getPartySummary(doc: DocumentRow): string | null {
  const extracted = doc.extracted_json;
  if (!extracted) return null;

  switch (doc.category) {
    case "Agreement": {
      const names = extracted.party_names;
      return Array.isArray(names) && names.length > 0
        ? names.map((name) => String(name)).join(" & ")
        : null;
    }
    case "Invoice":
      return typeof extracted.vendor_name === "string" ? extracted.vendor_name : null;
    case "Purchase Order":
      return typeof extracted.vendor === "string" ? extracted.vendor : null;
    case "GST Document":
    case "GST Filing":
      return typeof extracted.gstin === "string" ? extracted.gstin : null;
    default:
      return null;
  }
}
