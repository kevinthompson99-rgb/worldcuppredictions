"""add round force_locked

Revision ID: d1a2b3c4e5f6
Revises: 9defc92b1097
Create Date: 2026-06-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd1a2b3c4e5f6'
down_revision = '9defc92b1097'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('rounds', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('force_locked', sa.Boolean(), nullable=False, server_default=sa.false())
        )
    with op.batch_alter_table('rounds', schema=None) as batch_op:
        batch_op.alter_column('force_locked', server_default=None)


def downgrade():
    with op.batch_alter_table('rounds', schema=None) as batch_op:
        batch_op.drop_column('force_locked')
