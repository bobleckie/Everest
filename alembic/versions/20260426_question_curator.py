"""question curator columns

Revision ID: 20260426_question_curator
Revises: 20260425_competitor_intel
Create Date: 2026-04-26

Adds the strategic-curator output columns to rfp_questions. The curator is
a post-reconcile LLM pass that decides whether a candidate question is
actually appropriate to send to the agency in a clarification-question
submission (vs. answer-already-in-the-RFP, reveals-strategy, etc.).
"""
from alembic import op
import sqlalchemy as sa


revision = "20260426_question_curator"
down_revision = "20260425_competitor_intelligence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite-friendly: use op.add_column one at a time. Indices added
    # explicitly on the columns the triage UI will filter by.
    with op.batch_alter_table("rfp_questions") as batch:
        batch.add_column(sa.Column("curator_score", sa.Integer, nullable=True))
        batch.add_column(sa.Column("curator_recommended", sa.Boolean, nullable=True))
        batch.add_column(sa.Column("curator_reason", sa.String, nullable=True))
        batch.add_column(sa.Column("curator_improved_text", sa.Text, nullable=True))
        batch.add_column(sa.Column("curator_run_at", sa.DateTime, nullable=True))
    op.create_index("ix_rfp_questions_curator_score", "rfp_questions", ["curator_score"])
    op.create_index("ix_rfp_questions_curator_recommended", "rfp_questions", ["curator_recommended"])
    op.create_index("ix_rfp_questions_curator_reason", "rfp_questions", ["curator_reason"])


def downgrade() -> None:
    op.drop_index("ix_rfp_questions_curator_reason", table_name="rfp_questions")
    op.drop_index("ix_rfp_questions_curator_recommended", table_name="rfp_questions")
    op.drop_index("ix_rfp_questions_curator_score", table_name="rfp_questions")
    with op.batch_alter_table("rfp_questions") as batch:
        batch.drop_column("curator_run_at")
        batch.drop_column("curator_improved_text")
        batch.drop_column("curator_reason")
        batch.drop_column("curator_recommended")
        batch.drop_column("curator_score")
