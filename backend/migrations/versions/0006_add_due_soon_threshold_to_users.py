"""add due_soon_threshold_days to users

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-22

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "due_soon_threshold_days", sa.Integer(), nullable=False, server_default="30"
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "due_soon_threshold_days")
