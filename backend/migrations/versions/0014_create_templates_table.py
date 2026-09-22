"""create templates table

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-22

Phase 32 (see CLAUDE.md's Document generation section) — a reusable field
STRUCTURE (not values) learned from a sample Invoice/Agreement, used by
POST /templates/{id}/generate to produce brand-new documents with the same
shape but new content. `created_from_document_id` is nullable with
ON DELETE SET NULL: deleting the source sample document later shouldn't
delete an already-learned, independently useful template — it just loses
that one traceability link.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "templates",
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
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("field_schema", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_from_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )
    op.create_index("ix_templates_user_id", "templates", ["user_id"])
    op.create_index("ix_templates_created_from_document_id", "templates", ["created_from_document_id"])


def downgrade() -> None:
    op.drop_index("ix_templates_created_from_document_id", table_name="templates")
    op.drop_index("ix_templates_user_id", table_name="templates")
    op.drop_table("templates")
