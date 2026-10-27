"""initial schema: users, problems, tags, study plans, submissions

Revision ID: 8c07680f515d
Revises:
Create Date: 2026-03-15 16:00:33.431084

Until v2 this revision was empty and the tables were created with
Base.metadata.create_all(). Existing databases are already stamped with this
revision and keep working; fresh databases now get the schema from here.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8c07680f515d'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('tags',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=50), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('users',
    sa.Column('id', sa.String(length=128), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('problems',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.String(length=128), nullable=True),
    sa.Column('problem_image_path', sa.String(length=512), nullable=True),
    sa.Column('problem_text', sa.Text(), nullable=True),
    sa.Column('source', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('problem_tag',
    sa.Column('problem_id', sa.Integer(), nullable=True),
    sa.Column('tag_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['problem_id'], ['problems.id'], ),
    sa.ForeignKeyConstraint(['tag_id'], ['tags.id'], )
    )
    op.create_table('study_plans',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('problem_id', sa.Integer(), nullable=False),
    sa.Column('scheduled_date', sa.Date(), nullable=False),
    sa.Column('is_completed', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['problem_id'], ['problems.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('submissions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('answer_image_path', sa.String(length=512), nullable=True),
    sa.Column('score', sa.Integer(), nullable=True),
    sa.Column('ai_feedback', sa.Text(), nullable=True),
    sa.Column('explanation', sa.Text(), nullable=True),
    sa.Column('mistake', sa.Text(), nullable=True),
    sa.Column('user_memo', sa.Text(), nullable=True),
    sa.Column('solved_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('problem_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['problem_id'], ['problems.id'], ),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('submissions')
    op.drop_table('study_plans')
    op.drop_table('problem_tag')
    op.drop_table('problems')
    op.drop_table('users')
    op.drop_table('tags')
