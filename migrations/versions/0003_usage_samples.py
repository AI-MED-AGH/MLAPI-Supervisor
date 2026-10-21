"""usage samples (model telemetry)

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('usage_samples',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('model', sa.String(length=40), nullable=False),
    sa.Column('tier', sa.String(length=3), nullable=False),
    sa.Column('ts', sa.BigInteger(), nullable=False),
    sa.Column('cpu_m_avg', sa.Float(), nullable=False),
    sa.Column('cpu_m_max', sa.Float(), nullable=False),
    sa.Column('mem_avg', sa.Float(), nullable=False),
    sa.Column('mem_max', sa.Float(), nullable=False),
    sa.Column('n', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('model', 'tier', 'ts', name='uq_usage_sample')
    )


def downgrade() -> None:
    op.drop_table('usage_samples')
