"""create documents table and enable pgvector

Revision ID: 0001
Revises:
Create Date: 2026-09-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column(
            "upload_date",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("r2_file_path", sa.Text(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("extracted_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("deadline_date", sa.Date(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="uploaded"),
    )


def downgrade() -> None:
    op.drop_table("documents")
