"""latex resume columns on documents

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-27
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("documents") as b:
        b.add_column(sa.Column("tex_source", sa.Text(), nullable=False, server_default=""))
        b.add_column(sa.Column("tex_origin", sa.String(length=20), nullable=False, server_default=""))
        b.add_column(sa.Column("page_count", sa.Integer(), nullable=True))
        b.add_column(sa.Column("compile_log", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("documents") as b:
        for c in ("compile_log", "page_count", "tex_origin", "tex_source"):
            b.drop_column(c)
