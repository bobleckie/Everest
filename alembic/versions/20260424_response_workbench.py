"""response workbench tables

Revision ID: 20260424_response_workbench
Revises: 20260423_rfp_diff
Create Date: 2026-04-24
"""
from alembic import op
import sqlalchemy as sa


revision = "20260424_response_workbench"
down_revision = "20260423_rfp_diff"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── rfp_section_rubric_map ───────────────────────────────────────
    # Maps extracted RFP section_ids (e.g. "7.1") to rubric section_ids
    # (e.g. "technicalApproach") so per-section scores roll up to the
    # 5-criterion NJ T1628 rubric.
    op.create_table(
        "rfp_section_rubric_map",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("proposal_id", sa.Integer, sa.ForeignKey("proposals.id"), index=True),
        sa.Column("rfp_section_id", sa.String, index=True),
        sa.Column("rubric_section_id", sa.String, index=True),
        sa.Column("source", sa.String, default="auto"),  # auto | manual
        sa.Column("confidence", sa.String, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
    )
    op.create_index(
        "ix_rfp_section_rubric_map_unique",
        "rfp_section_rubric_map",
        ["proposal_id", "rfp_section_id"],
        unique=True,
    )

    # ── rfp_section_response ─────────────────────────────────────────
    # Per-extracted-section response narrative (Parsons or competitor).
    op.create_table(
        "rfp_section_response",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("proposal_id", sa.Integer, sa.ForeignKey("proposals.id"), index=True),
        sa.Column("rfp_section_id", sa.String, index=True),
        sa.Column("author_type", sa.String),  # parsons | competitor
        sa.Column("author_id", sa.Integer, sa.ForeignKey("competitors.id"), nullable=True),
        sa.Column("content", sa.Text),
        sa.Column("compliance_tags_json", sa.Text, nullable=True),
        sa.Column("status", sa.String, default="draft"),
        sa.Column("version", sa.Integer, default=1),
        sa.Column("last_edited_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
    )
    op.create_index(
        "ix_rfp_section_response_unique",
        "rfp_section_response",
        ["proposal_id", "rfp_section_id", "author_type", "author_id"],
        unique=True,
    )

    # ── section_cure_suggestion ──────────────────────────────────────
    # Cure Advisor "close the scoring gap" suggestions with approve/deny.
    op.create_table(
        "section_cure_suggestion",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("proposal_id", sa.Integer, sa.ForeignKey("proposals.id"), index=True),
        sa.Column("rfp_section_id", sa.String, index=True),
        sa.Column(
            "based_on_section_score_id",
            sa.Integer,
            sa.ForeignKey("section_scores.id"),
            nullable=True,
        ),
        sa.Column("competitor_id", sa.Integer, sa.ForeignKey("competitors.id"), nullable=True),
        sa.Column("suggestion_text", sa.Text),
        sa.Column("suggested_rewrite", sa.Text, nullable=True),
        sa.Column("status", sa.String, default="proposed", index=True),
        sa.Column("approved_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_at", sa.DateTime, nullable=True),
        sa.Column("applied_response_version", sa.Integer, nullable=True),
        sa.Column("rescore_delta", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
    )


def downgrade() -> None:
    op.drop_table("section_cure_suggestion")
    op.drop_index("ix_rfp_section_response_unique", table_name="rfp_section_response")
    op.drop_table("rfp_section_response")
    op.drop_index("ix_rfp_section_rubric_map_unique", table_name="rfp_section_rubric_map")
    op.drop_table("rfp_section_rubric_map")
