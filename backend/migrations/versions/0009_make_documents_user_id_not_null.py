"""make documents.user_id NOT NULL

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-22

Step 2 of 2 for per-user document isolation. Only safe to run after every
existing row has been backfilled with a real user_id (see CLAUDE.md's Per-user
document isolation section for the backfill that was run against the real DB
before this migration) — alembic will otherwise fail applying the NOT NULL
constraint against any row still holding NULL.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("documents", "user_id", nullable=False)


def downgrade() -> None:
    op.alter_column("documents", "user_id", nullable=True)
