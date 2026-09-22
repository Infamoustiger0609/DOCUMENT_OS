"""add indexes on documents.category, deadline_date, upload_date

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-21

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_documents_category", "documents", ["category"])
    op.create_index("ix_documents_deadline_date", "documents", ["deadline_date"])
    op.create_index("ix_documents_upload_date", "documents", ["upload_date"])


def downgrade() -> None:
    op.drop_index("ix_documents_upload_date", table_name="documents")
    op.drop_index("ix_documents_deadline_date", table_name="documents")
    op.drop_index("ix_documents_category", table_name="documents")
