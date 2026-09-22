"""rename documents.r2_file_path to storage_path

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-18

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("documents", "r2_file_path", new_column_name="storage_path")


def downgrade() -> None:
    op.alter_column("documents", "storage_path", new_column_name="r2_file_path")
