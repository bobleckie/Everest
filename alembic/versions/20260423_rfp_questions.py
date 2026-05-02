"""rfp questions

Revision ID: 20260423_rfp_questions
Revises: 20260421_pricing_audit
Create Date: 2026-04-23
"""
from alembic import op
import sqlalchemy as sa


revision = "20260423_rfp_questions"
down_revision = "20260421_pricing_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rfp_questions",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("document_id", sa.Integer, sa.ForeignKey("ingested_documents.id"), nullable=True, index=True),
        sa.Column("proposal_id", sa.Integer, sa.ForeignKey("proposals.id"), nullable=True, index=True),
        sa.Column("source_section", sa.String, nullable=True),
        sa.Column("source_page", sa.Integer, nullable=True),
        sa.Column("category", sa.String, index=True),  # clarification, risk, pricing, scope, competitive, form
        sa.Column("priority", sa.String, nullable=True),  # critical, high, medium, low
        sa.Column("question_text", sa.Text),
        sa.Column("rationale", sa.Text, nullable=True),
        sa.Column("source_quote", sa.Text, nullable=True),
        sa.Column("related_requirement_ids", sa.Text, nullable=True),  # JSON array of RfpRequirement.id
        sa.Column("status", sa.String, default="draft", index=True),  # draft, reviewed, approved, rejected, submitted, answered
        sa.Column("answer_text", sa.Text, nullable=True),
        sa.Column("answered_date", sa.DateTime, nullable=True),
        sa.Column("submitted_date", sa.DateTime, nullable=True),
        sa.Column("reviewer_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("review_notes", sa.Text, nullable=True),
        sa.Column("created_by_ai", sa.Boolean, default=True),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
    )

    # Configurable approval workflow setting — stored as a JSON app_setting.
    # Default created here so UI has something to show.
    # Key: "question_approval_roles"  Value: JSON array of role names required.
    # No schema change for app_settings (it already exists).


def downgrade() -> None:
    op.drop_table("rfp_questions")
