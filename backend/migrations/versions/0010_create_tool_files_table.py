"""create tool_files table

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-22

Backs the new document-editing tools (Phase 21, see CLAUDE.md's Document tools
section) — merge/split/compress PDF, PDF<->DOCX, image convert/resize, and
OCR-to-searchable-PDF. Deliberately separate from `documents`: these are
one-shot file transformations with a retention window, not part of the
document-intelligence pipeline (no category/raw_text/deadline_date columns).
expires_at is indexed since the retention sweep (tools_common.py) queries on
it directly on every /tools/* request.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tool_files",
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
        sa.Column("tool_name", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("output_filename", sa.Text(), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_tool_files_user_id", "tool_files", ["user_id"])
    op.create_index("ix_tool_files_expires_at", "tool_files", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_tool_files_expires_at", table_name="tool_files")
    op.drop_index("ix_tool_files_user_id", table_name="tool_files")
    op.drop_table("tool_files")
