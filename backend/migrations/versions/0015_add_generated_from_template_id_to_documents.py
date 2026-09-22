"""add generated_from_template_id to documents

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-22

Phase 32 (see CLAUDE.md's Document generation section) — POST
/templates/{id}/generate produces a brand-new, ordinary `documents` row
(not a separate table), so it's immediately visible/searchable/chattable
through every existing document feature. This column is just the reverse
traceability link back to the template that produced it — nullable and
ON DELETE SET NULL, matching `templates.created_from_document_id`'s own
reasoning: deleting the template later shouldn't delete a document someone
already generated and is using.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("generated_from_template_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    # A named constraint (rather than an inline sa.ForeignKey(), which Postgres
    # would name automatically) — matches models.py's Document.generated_from_template_id,
    # which sets use_alter=True + this same explicit name to break the
    # documents<->templates mutual-FK cycle for SQLAlchemy's own metadata
    # sorter (Base.metadata.create_all()/drop_all(), used by the test suite —
    # see conftest.py). Doesn't change how the constraint behaves at query time.
    op.create_foreign_key(
        "fk_documents_generated_from_template_id",
        "documents",
        "templates",
        ["generated_from_template_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_documents_generated_from_template_id", "documents", ["generated_from_template_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_documents_generated_from_template_id", table_name="documents")
    op.drop_constraint("fk_documents_generated_from_template_id", "documents", type_="foreignkey")
    op.drop_column("documents", "generated_from_template_id")
