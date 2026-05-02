"""pricing audit log

Revision ID: 20260421_pricing_audit
Revises: 20260421_pricing
Create Date: 2026-04-21
"""
from alembic import op
import sqlalchemy as sa


revision = "20260421_pricing_audit"
down_revision = "20260421_pricing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pricing_audit_log",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("pricing_model_id", sa.Integer, sa.ForeignKey("pricing_models.id"), index=True),
        sa.Column("version", sa.Integer),
        sa.Column("action", sa.String),
        sa.Column("entity_type", sa.String),
        sa.Column("entity_id", sa.Integer, nullable=True),
        sa.Column("field", sa.String, nullable=True),
        sa.Column("old_value", sa.Text, nullable=True),
        sa.Column("new_value", sa.Text, nullable=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, index=True),
    )


def downgrade() -> None:
    op.drop_table("pricing_audit_log")
