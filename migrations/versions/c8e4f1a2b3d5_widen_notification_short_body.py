"""Widen notifications.short_body to 200 characters

Revision ID: c8e4f1a2b3d5
Revises: b7c3e9a41f20
Create Date: 2026-11-03 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c8e4f1a2b3d5'
down_revision = 'b7c3e9a41f20'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('notifications') as batch_op:
        batch_op.alter_column('short_body', existing_type=sa.String(length=100), type_=sa.String(length=200), existing_nullable=False)


def downgrade():
    with op.batch_alter_table('notifications') as batch_op:
        batch_op.alter_column('short_body', existing_type=sa.String(length=200), type_=sa.String(length=100), existing_nullable=False)
