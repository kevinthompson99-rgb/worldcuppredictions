"""add round stake amount

Revision ID: b8059953b427
Revises: 4361e0a4c373
Create Date: 2026-06-08 21:20:46.462591

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b8059953b427'
down_revision = '4361e0a4c373'
branch_labels = None
depends_on = None


def upgrade():
    # server_default backfills existing rounds with the previous hardcoded £5 stake
    # so the NOT NULL constraint can apply retroactively without breaking them.
    with op.batch_alter_table('rounds', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('stake_amount', sa.Numeric(precision=8, scale=2), nullable=False, server_default='5.00')
        )
    with op.batch_alter_table('rounds', schema=None) as batch_op:
        batch_op.alter_column('stake_amount', server_default=None)


def downgrade():
    with op.batch_alter_table('rounds', schema=None) as batch_op:
        batch_op.drop_column('stake_amount')
