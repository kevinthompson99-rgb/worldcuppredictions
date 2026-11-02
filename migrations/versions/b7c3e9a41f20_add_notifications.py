"""Add notifications inbox table

Revision ID: b7c3e9a41f20
Revises: ef261b4fe695
Create Date: 2026-11-03 09:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7c3e9a41f20'
down_revision = 'ef261b4fe695'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('notifications',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('short_body', sa.String(length=100), nullable=False),
        sa.Column('full_body', sa.Text(), nullable=False),
        sa.Column('url', sa.String(length=500), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('read_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_notifications_user_id'), ['user_id'], unique=False)


def downgrade():
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_notifications_user_id'))
    op.drop_table('notifications')
