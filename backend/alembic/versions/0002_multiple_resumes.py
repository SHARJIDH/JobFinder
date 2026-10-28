"""multiple labelled resumes

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-27
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("master_resumes") as b:
        b.add_column(sa.Column("label", sa.String(length=200), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("master_resumes") as b:
        b.drop_column("label")
