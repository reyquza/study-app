"""password accounts and grading jobs

Replaces Firebase sign-in with email + password and guest accounts, and adds the
grading_jobs table that the background worker uses as its queue.

Revision ID: bdf4cdc5d998
Revises: 8c07680f515d
Create Date: 2026-10-27 17:16:30.230344

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bdf4cdc5d998'
down_revision: Union[str, Sequence[str], None] = '8c07680f515d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('grading_jobs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.String(length=128), nullable=False),
    sa.Column('problem_id', sa.Integer(), nullable=False),
    sa.Column('answer_image_path', sa.String(length=512), nullable=False),
    sa.Column('status', sa.Enum('queued', 'running', 'succeeded', 'failed', name='jobstatus', native_enum=False, length=16), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('run_after', sa.DateTime(timezone=True), nullable=False),
    sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('grader_model', sa.String(length=100), nullable=True),
    sa.Column('submission_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['problem_id'], ['problems.id'], ),
    sa.ForeignKeyConstraint(['submission_id'], ['submissions.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('grading_jobs', schema=None) as batch_op:
        batch_op.create_index('ix_grading_jobs_status_run_after', ['status', 'run_after'], unique=False)
        batch_op.create_index(batch_op.f('ix_grading_jobs_user_id'), ['user_id'], unique=False)

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('password_hash', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('is_guest', sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.create_unique_constraint('uq_users_email', ['email'])



def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint('uq_users_email', type_='unique')
        batch_op.drop_column('is_guest')
        batch_op.drop_column('password_hash')

    with op.batch_alter_table('grading_jobs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_grading_jobs_user_id'))
        batch_op.drop_index('ix_grading_jobs_status_run_after')

    op.drop_table('grading_jobs')
