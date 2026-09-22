"""add classification_reasoning and classification_version to documents

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-21

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("classification_reasoning", sa.Text(), nullable=True))
    # Existing rows were all classified before "Purchase Order" existed as a category
    # (classification version 1); default them to that so already-stored "Other"
    # documents are correctly flagged as possibly-misclassified by the new category list.
    op.add_column(
        "documents",
        sa.Column("classification_version", sa.Integer(), nullable=True, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("documents", "classification_version")
    op.drop_column("documents", "classification_reasoning")
