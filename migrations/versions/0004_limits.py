"""request limits on API keys and models

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('api_keys') as batch:
        batch.add_column(sa.Column('limits', sa.JSON(), nullable=True))
    with op.batch_alter_table('models') as batch:
        batch.add_column(sa.Column('declared_limits', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('models') as batch:
        batch.drop_column('declared_limits')
    with op.batch_alter_table('api_keys') as batch:
        batch.drop_column('limits')
