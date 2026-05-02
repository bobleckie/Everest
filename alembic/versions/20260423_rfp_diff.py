"""rfp requirement diffs

Revision ID: 20260423_rfp_diff
Revises: 20260423_rfp_questions
Create Date: 2026-04-23
"""
from alembic import op
import sqlalchemy as sa


revision = "20260423_rfp_diff"
down_revision = "20260423_rfp_questions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A diff "run" records one comparison between a baseline scope and a target
    # scope. Scope is expressed as either a proposal_id or a document_id list.
    op.create_table(
        "rfp_diff_runs",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("label", sa.String, nullable=True),  # e.g. "2021 vs 2026 T1628"
        sa.Column("baseline_proposal_id", sa.Integer, sa.ForeignKey("proposals.id"), nullable=True),
        sa.Column("baseline_document_ids", sa.Text, nullable=True),  # JSON array
        sa.Column("target_proposal_id", sa.Integer, sa.ForeignKey("proposals.id"), nullable=True, index=True),
        sa.Column("target_document_ids", sa.Text, nullable=True),    # JSON array
        sa.Column("status", sa.String, default="pending", index=True),  # pending, running, complete, failed
        sa.Column("progress_note", sa.String, nullable=True),
        sa.Column("counts_added", sa.Integer, default=0),
        sa.Column("counts_removed", sa.Integer, default=0),
        sa.Column("counts_changed", sa.Integer, default=0),
        sa.Column("counts_unchanged", sa.Integer, default=0),
        sa.Column("high_match_threshold", sa.Float, default=0.92),
        sa.Column("low_match_threshold", sa.Float, default=0.70),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime),
        sa.Column("completed_at", sa.DateTime, nullable=True),
    )

    # Each row is one matched pair (or unmatched added/removed).
    op.create_table(
        "rfp_requirement_diffs",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("diff_run_id", sa.Integer, sa.ForeignKey("rfp_diff_runs.id"), index=True),
        sa.Column("baseline_requirement_id", sa.Integer, sa.ForeignKey("rfp_requirements.id"), nullable=True, index=True),
        sa.Column("target_requirement_id", sa.Integer, sa.ForeignKey("rfp_requirements.id"), nullable=True, index=True),
        sa.Column("status", sa.String, index=True),  # added, removed, changed, unchanged
        sa.Column("similarity", sa.Float, nullable=True),
        sa.Column("match_method", sa.String, nullable=True),  # embedding, llm_adjudicated, manual
        sa.Column("change_summary", sa.Text, nullable=True),  # short "what changed"
        sa.Column("impact_blurb", sa.Text, nullable=True),    # "why it matters" — the agent explanation
        sa.Column("impact_severity", sa.String, nullable=True),  # critical, high, medium, low, informational
        sa.Column("reviewer_status", sa.String, default="new"),  # new, reviewed, dismissed, flagged
        sa.Column("reviewer_notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime),
    )


def downgrade() -> None:
    op.drop_table("rfp_requirement_diffs")
    op.drop_table("rfp_diff_runs")
