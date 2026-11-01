"""grading job language

The language the written feedback is generated in: "ja" (the evaluated setting) or "en"
for English-language visitors.

Revision ID: 9b4d1f6a2c87
Revises: 5e2b7c41a9d3
Create Date: 2026-11-02 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9b4d1f6a2c87'
down_revision: Union[str, Sequence[str], None] = '5e2b7c41a9d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('grading_jobs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('language', sa.String(length=8), server_default='ja', nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('grading_jobs', schema=None) as batch_op:
        batch_op.drop_column('language')
