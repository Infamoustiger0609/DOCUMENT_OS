"""add signature_storage_path to users

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-22

Phase 31 (see CLAUDE.md's E-signature section) — a user's own saved
signature image (drawn or uploaded once in Settings), referenced by its
Supabase Storage key. Nullable: most users won't have saved one yet, and
POST /documents/{id}/sign 400s with a clear message when it's null instead
of treating "no signature" as an error state on the user row itself.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("signature_storage_path", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "signature_storage_path")
