"""Add mem_limit to blueprints

Revision ID: a3c9e1f52d07
Revises: 684f267b0e8b
Create Date: 2026-04-08 12:42:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3c9e1f52d07'
down_revision: Union[str, Sequence[str], None] = '684f267b0e8b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add mem_limit column to blueprints table."""
    op.add_column('blueprints', sa.Column('mem_limit', sa.String(length=50), nullable=True))


def downgrade() -> None:
    """Remove mem_limit column from blueprints table."""
    op.drop_column('blueprints', 'mem_limit')
