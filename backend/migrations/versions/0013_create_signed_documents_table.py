"""create signed_documents table

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-22

Phase 31 (see CLAUDE.md's E-signature section) — one row per signed PDF
produced by POST /documents/{id}/sign. Deliberately a separate table from
`documents`, not new columns on it: a signed PDF is a derived output linked
to a source document, not itself something that gets classified/extracted,
and a document can be (re-)signed more than once, so this is a one-to-many
relationship, not a single nullable column. Unlike tool_files, there is no
expires_at/retention sweep here — a signed document is a durable artifact
the user deliberately created to keep, not a transient working file.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "signed_documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )
    op.create_index("ix_signed_documents_user_id", "signed_documents", ["user_id"])
    op.create_index("ix_signed_documents_document_id", "signed_documents", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_signed_documents_document_id", table_name="signed_documents")
    op.drop_index("ix_signed_documents_user_id", table_name="signed_documents")
    op.drop_table("signed_documents")
