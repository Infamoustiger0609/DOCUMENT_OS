"""add source column to documents

Distinguishes an ordinary uploaded document (POST /documents/upload) from a
template-learning sample document (POST /templates/learn-from-sample) and a
template-generated document (POST /templates/{id}/generate) — see CLAUDE.md's
"Document source separation" section. Both of the latter two are ordinary
`documents` rows, and until now nothing marked them as such, which is why they
were leaking into the main "My Documents" list (GET /documents).

Backfills existing rows from two signals that already existed but encoded
this fact indirectly: `generated_from_template_id` (non-null only on a
generated document) and `templates.created_from_document_id` (points back at
the sample document's id). Everything else defaults to "upload".

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("source", sa.Text(), nullable=False, server_default="upload"),
    )
    op.create_index("ix_documents_source", "documents", ["source"])

    op.execute(
        """
        UPDATE documents
        SET source = 'generated'
        WHERE generated_from_template_id IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE documents
        SET source = 'template_sample'
        WHERE generated_from_template_id IS NULL
          AND id IN (
              SELECT created_from_document_id FROM templates
              WHERE created_from_document_id IS NOT NULL
          )
        """
    )


def downgrade() -> None:
    op.drop_index("ix_documents_source", table_name="documents")
    op.drop_column("documents", "source")
