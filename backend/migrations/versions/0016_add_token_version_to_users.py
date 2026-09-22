"""add token_version to users

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-22

Security audit fix (see CLAUDE.md's Security section): existing tokens
previously stayed valid for their full ~24h lifetime even after a user
changed their password, since JWTs weren't tied to anything that changes on
a password change. This column, embedded in every issued token's "tv" claim
and checked in auth.get_current_user(), lets POST /auth/change-password
invalidate every existing token by bumping it.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
