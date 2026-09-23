import uuid
from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    category: Optional[str] = None
    upload_date: datetime
    storage_path: str
    raw_text: Optional[str] = None
    extracted_json: Optional[Any] = None
    deadline_date: Optional[date] = None
    status: str
    error_message: Optional[str] = None
    classification_reasoning: Optional[str] = None
    classification_version: Optional[int] = None
    # Phase 32 — see CLAUDE.md's Document generation section. Set only on a
    # document produced by POST /templates/{id}/generate.
    generated_from_template_id: Optional[uuid.UUID] = None
    # "upload" / "template_sample" / "generated" — see CLAUDE.md's "Document
    # source separation" section.
    source: str = "upload"


# Same as DocumentOut minus raw_text — used for list endpoints (GET /documents,
# GET /documents/deadlines). raw_text is the full extracted PDF/OCR text (often
# 10-25KB+ per document) and isn't read by any list view; only the single-document
# detail page needs it, and that fetches DocumentOut separately via GET /documents/{id}.
# extracted_json IS kept here — small (~1KB) and the deadlines/dashboard list views
# do read a few of its fields (getPartySummary() in the frontend).
class DocumentListOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    category: Optional[str] = None
    upload_date: datetime
    storage_path: str
    extracted_json: Optional[Any] = None
    deadline_date: Optional[date] = None
    status: str
    error_message: Optional[str] = None
    classification_reasoning: Optional[str] = None
    classification_version: Optional[int] = None
    # "upload" / "template_sample" / "generated" — see CLAUDE.md's "Document
    # source separation" section. GET /documents already filters to "upload"
    # server-side, but this is also reused (unfiltered) by GET /documents/templates
    # and GET /documents/generated for the Editing Workspace's own two lists.
    source: str = "upload"


class DocumentUploadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    category: Optional[str] = None
    raw_text: Optional[str] = None
    extracted_json: Optional[Any] = None
    deadline_date: Optional[date] = None
    error_message: Optional[str] = None
    classification_reasoning: Optional[str] = None
    classification_version: Optional[int] = None


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    name: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    name: str
    role: str
    created_at: datetime
    due_soon_threshold_days: int
    # Phase 31 — see CLAUDE.md's E-signature section. Read from User.has_signature
    # (a Python property, not a column) — the raw storage path is never exposed.
    has_signature: bool = False


class UserPreferencesUpdate(BaseModel):
    due_soon_threshold_days: Literal[7, 14, 30, 60]


class UserProfileUpdate(BaseModel):
    name: str
    email: EmailStr


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class DownloadUrlOut(BaseModel):
    url: str
    expires_in: int


class ChatRequest(BaseModel):
    question: str


class ChatResponse(BaseModel):
    answer: str
    truncated: bool


# One output file from a /tools/* endpoint (see CLAUDE.md's Document tools
# section) — mirrors DocumentOut's from_attributes pattern, just against the
# separate ToolFile model instead of Document.
class ToolFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tool_name: str
    original_filename: str
    output_filename: str
    mime_type: str
    size_bytes: int
    created_at: datetime
    expires_at: datetime


# One tool invocation the editor assistant made during a chat turn (Phase 23
# — see CLAUDE.md's Editor assistant section). `files` is empty when the tool
# call failed (see `error`); `tool_name` uses the function-calling name
# (e.g. "merge_pdf"), not the /tools/* URL slug (e.g. "merge-pdf").
class EditorChatToolRun(BaseModel):
    tool_name: str
    files: list[ToolFileOut] = []
    error: Optional[str] = None


class EditorChatResponse(BaseModel):
    reply: str
    tool_runs: list[EditorChatToolRun] = []


# Phase 25 — POST /copilot/chat (see CLAUDE.md's Copilot section). A global,
# registry-wide assistant, distinct from ChatRequest/ChatResponse above (which
# answer questions about one specific document's raw_text).
class CopilotHistoryEntry(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class CopilotChatRequest(BaseModel):
    message: str
    history: list[CopilotHistoryEntry] = []


# A lightweight reference to one of the user's own documents — enough for the
# frontend to render a clickable link to /documents/{id}, deliberately not the
# full DocumentOut/DocumentListOut shape (raw_text, extracted_json, etc. would
# be wasted payload for a citation list).
class CopilotDocumentRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    category: Optional[str] = None
    status: str
    deadline_date: Optional[date] = None


class CopilotChatResponse(BaseModel):
    reply: str
    citations: list[CopilotDocumentRef] = []


# Phase 28 — GET /analytics/schema-summary + POST /analytics/generate (see
# CLAUDE.md's Analytics section). AnalyticsFieldSummary/AnalyticsCategorySummary/
# AnalyticsSchemaSummary describe "what data actually exists" (derived from real
# stored `extracted_json`, not a fixed schema list) — this is the whitelist every
# aggregation tool call is validated against, so the AI can never touch a field
# that isn't both real and populated.
class AnalyticsFieldSummary(BaseModel):
    name: str
    type: Literal["numeric", "string", "boolean", "date", "array", "object"]
    populated_count: int


class AnalyticsCategorySummary(BaseModel):
    category: str
    document_count: int
    structured_document_count: int
    fields: list[AnalyticsFieldSummary]


class AnalyticsSchemaSummary(BaseModel):
    total_documents: int
    categories: list[AnalyticsCategorySummary]


class AnalyticsMetric(BaseModel):
    label: str
    value: float
    format: Literal["currency", "count", "percent"]
    category: str


class AnalyticsGenerateResponse(BaseModel):
    metrics: list[AnalyticsMetric] = []
    generated_at: datetime
    cached: bool


# Phase 31 — self-serve e-signature (see CLAUDE.md's E-signature section).
class SignatureStatusOut(BaseModel):
    has_signature: bool
    # Only populated when has_signature is true — a short-lived signed URL to
    # preview the saved signature image, same "generate on demand, don't
    # persist a long-lived link" pattern as DownloadUrlOut.
    download_url: Optional[str] = None
    expires_in: Optional[int] = None


# One signed PDF produced by POST /documents/{id}/sign — mirrors ToolFileOut's
# from_attributes pattern against the SignedDocument model.
class SignedDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    created_at: datetime


# Phase 32 — document generation from a learned template (see CLAUDE.md's
# Document generation section). `field_schema` is typed as `Any` (like
# `extracted_json` above) — it's JSONB, a straight pass-through, and its
# exact shape is documented (and validated at write time) in
# template_learning.py, not re-declared as a nested Pydantic model here.
class TemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    category: str
    field_schema: Any
    created_from_document_id: Optional[uuid.UUID] = None
    created_at: datetime


# Exactly one of values/instruction must be provided — enforced in
# templates_router.py, not here (a Pydantic validator would need to see both
# fields anyway, and the 400 message is clearer written by hand at the point
# where both are already in scope).
class TemplateGenerateRequest(BaseModel):
    # Keyed by the template's own section ids — a "fields"-type section maps
    # to {field_key: value}, a "table"-type section maps to a list of
    # {column_key: value} row objects. See template_learning.normalize_values().
    values: Optional[dict[str, Any]] = None
    instruction: Optional[str] = None
