"""add documents.search_vector (full-text search)

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-22

Phase 25 — backs POST /copilot/chat's search_document_text tool (see
CLAUDE.md's Copilot section). A generated column Postgres itself recomputes
from raw_text (nothing in the app writes to it directly), plus a GIN index
for the @@ operator search_document_text's query uses.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('english', coalesce(raw_text, ''))", persisted=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_documents_search_vector", "documents", ["search_vector"], postgresql_using="gin"
    )


def downgrade() -> None:
    op.drop_index("ix_documents_search_vector", table_name="documents")
    op.drop_column("documents", "search_vector")
