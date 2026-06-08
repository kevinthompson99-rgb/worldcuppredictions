"""add user display name

Revision ID: 760005299371
Revises: b8059953b427
Create Date: 2026-06-08 21:24:39.536200

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '760005299371'
down_revision = 'b8059953b427'
branch_labels = None
depends_on = None


def upgrade():
    # Add nullable first so existing rows aren't rejected, backfill from `username`
    # (their only public name up to now), then tighten to NOT NULL.
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('display_name', sa.String(length=64), nullable=True))

    op.execute("UPDATE users SET display_name = username WHERE display_name IS NULL")

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.alter_column('display_name', existing_type=sa.String(length=64), nullable=False)


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('display_name')
