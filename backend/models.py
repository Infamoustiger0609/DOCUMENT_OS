import uuid

from sqlalchemy import Column, Computed, Date, DateTime, ForeignKey, Index, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.sql import func

from database import Base


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_search_vector", "search_vector", postgresql_using="gin"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Added in 0008/0009 (nullable -> backfilled -> NOT NULL against the real,
    # already-populated DB — see CLAUDE.md's Per-user document isolation
    # section). ondelete="CASCADE" is a DB-level backstop; delete_account()
    # already deletes a user's documents (Storage objects included) explicitly
    # before deleting the users row, so this should never actually fire in
    # practice, but it means a document can never be silently orphaned even if
    # some future code path deletes a user without going through that endpoint.
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename = Column(Text, nullable=False)
    category = Column(Text, nullable=True, index=True)
    upload_date = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    storage_path = Column(Text, nullable=False)
    raw_text = Column(Text, nullable=True)
    extracted_json = Column(JSONB, nullable=True)
    deadline_date = Column(Date, nullable=True, index=True)
    status = Column(Text, nullable=False, default="uploaded")
    error_message = Column(Text, nullable=True)
    classification_reasoning = Column(Text, nullable=True)
    classification_version = Column(Integer, nullable=True)
    # Phase 32 (see CLAUDE.md's Document generation section) — set only on a
    # document produced by POST /templates/{id}/generate; the reverse link of
    # Template.created_from_document_id. Nullable, ON DELETE SET NULL:
    # deleting the template later doesn't delete a document already generated
    # from it. documents<->templates is a genuine mutual FK cycle (a template
    # references its source document; a generated document references its
    # template) — use_alter=True + an explicit constraint name tells
    # SQLAlchemy's metadata sorter this FK can be added/dropped as a separate
    # step, breaking the cycle for Base.metadata.create_all()/drop_all()
    # (used by the test suite — see conftest.py) without changing the real
    # Postgres schema at all.
    generated_from_template_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "templates.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_documents_generated_from_template_id",
        ),
        nullable=True,
        index=True,
    )
    # Phase 25 (see CLAUDE.md's Copilot section) — powers POST /copilot/chat's
    # search_document_text tool. GENERATED ALWAYS AS ... STORED: Postgres itself
    # recomputes this whenever raw_text changes, so nothing in the app ever
    # writes to it directly. See migration 0011 for the matching DDL.
    search_vector = Column(
        TSVECTOR,
        Computed("to_tsvector('english', coalesce(raw_text, ''))", persisted=True),
        nullable=True,
    )


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(Text, unique=True, nullable=False, index=True)
    hashed_password = Column(Text, nullable=False)
    name = Column(Text, nullable=False)
    role = Column(Text, nullable=False, default="user")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    due_soon_threshold_days = Column(Integer, nullable=False, default=30, server_default="30")
    # Phase 31 (see CLAUDE.md's E-signature section) — Storage key of the
    # user's own saved signature image (drawn or uploaded once in Settings).
    # Nullable: most users won't have saved one yet.
    signature_storage_path = Column(Text, nullable=True)
    # Security audit fix (see CLAUDE.md's Security section) — embedded in every
    # issued JWT's "tv" claim and checked in auth.get_current_user(). Bumped on
    # password change so a token stolen before the change stops working
    # immediately afterward, instead of remaining valid for its full ~24h
    # natural lifetime regardless of the password change.
    token_version = Column(Integer, nullable=False, default=0, server_default="0")

    @property
    def has_signature(self) -> bool:
        """Read by schemas.UserOut (Pydantic's from_attributes reads any
        attribute/property by name, not just Columns) — the API never
        exposes the raw storage path itself, just whether one exists."""
        return self.signature_storage_path is not None


class ToolFile(Base):
    """One output file from a /tools/* document-editing endpoint (merge/split/
    compress PDF, PDF<->DOCX, image convert/resize, OCR-to-searchable-PDF —
    see CLAUDE.md's Document tools section). Deliberately separate from
    Document/the main documents table: these are transient working files with
    a retention window (expires_at, swept by tools_common.cleanup_expired_tool_files),
    not part of the document-intelligence registry — no category, raw_text, or
    deadline_date, since no classification or structured extraction ever runs
    on them."""

    __tablename__ = "tool_files"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tool_name = Column(Text, nullable=False)
    original_filename = Column(Text, nullable=False)
    output_filename = Column(Text, nullable=False)
    storage_path = Column(Text, nullable=False)
    mime_type = Column(Text, nullable=False)
    size_bytes = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)


class SignedDocument(Base):
    """One signed PDF produced by POST /documents/{id}/sign (Phase 31 — see
    CLAUDE.md's E-signature section). Separate from `documents`: a signed PDF
    is a derived output linked to a source document (not itself classified/
    extracted), and a document can be signed more than once, so this is a
    one-to-many relationship rather than a column on Document. Unlike
    ToolFile, there is no expires_at/retention sweep — this is a durable
    artifact the user deliberately created to keep."""

    __tablename__ = "signed_documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id = Column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename = Column(Text, nullable=False)
    storage_path = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AuditLog(Base):
    """Who accessed/downloaded/deleted/signed/generated which document, when
    (security audit finding — see CLAUDE.md's Security section). `user_id` is
    ON DELETE SET NULL, not CASCADE, and `user_email` is captured at write
    time as a plain column (not read live off the User row) — deliberately,
    so an audit trail survives and stays meaningful after the account itself
    is deleted, which is the whole point of an audit trail. No IP address is
    captured, consistent with this app's existing Sentry `send_default_pii`
    convention of not collecting more than it needs."""

    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    user_email = Column(Text, nullable=True)
    action = Column(Text, nullable=False)
    resource_type = Column(Text, nullable=False)
    resource_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)


class Template(Base):
    """A reusable field STRUCTURE (not values) learned from a sample Invoice/
    Agreement (Phase 32 — see CLAUDE.md's Document generation section).
    POST /templates/{id}/generate reads `field_schema` to render a brand-new
    document with new values in the same shape. `field_schema` is JSONB — an
    ordered list of sections, each either a flat set of fields or a
    line-item-style table of columns; see template_learning.py for the exact
    shape and template_generation.py for how it's rendered."""

    __tablename__ = "templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name = Column(Text, nullable=False)
    category = Column(Text, nullable=False)
    field_schema = Column(JSONB, nullable=False)
    created_from_document_id = Column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
