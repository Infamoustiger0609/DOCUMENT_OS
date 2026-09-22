"""add nullable documents.user_id column (FK to users.id)

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-22

Step 1 of 2 for per-user document isolation (see CLAUDE.md's Per-user document
isolation section). Nullable here on purpose: the real DB already has rows
with no owner, so this has to land before the one-time backfill assigns them
all to a real user, which in turn has to land before 0009 makes the column
NOT NULL.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_documents_user_id_users",
        "documents",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_documents_user_id", "documents", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_documents_user_id", table_name="documents")
    op.drop_constraint("fk_documents_user_id_users", "documents", type_="foreignkey")
    op.drop_column("documents", "user_id")
