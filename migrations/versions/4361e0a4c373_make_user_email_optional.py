"""Make user email optional and non-unique

Revision ID: 4361e0a4c373
Revises: 9747c48e2a40
Create Date: 2026-06-08 21:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4361e0a4c373'
down_revision = '9747c48e2a40'
branch_labels = None
depends_on = None


def upgrade():
    # Registration no longer collects email (username + password only) - only the
    # seeded admin account has one. Drop the uniqueness constraint and allow NULL
    # so existing/admin rows aren't disturbed but new users don't need one.
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index('ix_users_email')
        batch_op.alter_column('email', existing_type=sa.String(length=120), nullable=True)
        batch_op.create_index(batch_op.f('ix_users_email'), ['email'], unique=False)


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index('ix_users_email')
        batch_op.alter_column('email', existing_type=sa.String(length=120), nullable=False)
        batch_op.create_index(batch_op.f('ix_users_email'), ['email'], unique=True)
