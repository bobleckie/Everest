"""wait-time A/B test tables

Revision ID: 20260429_wait_time_ab
Revises: 20260426_question_curator
Create Date: 2026-04-29
"""
from alembic import op
import sqlalchemy as sa


revision = "20260429_wait_time_ab"
down_revision = "20260426_question_curator"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wait_time_imports",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("proposal_id", sa.Integer, sa.ForeignKey("proposals.id"), nullable=True, index=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("source_filename", sa.String, nullable=True),
        sa.Column("metric_name_filter", sa.String, server_default="Facility Average Wait Time"),
        sa.Column("row_count", sa.Integer, server_default="0"),
        sa.Column("station_count", sa.Integer, server_default="0"),
        sa.Column("date_min", sa.DateTime, nullable=True),
        sa.Column("date_max", sa.DateTime, nullable=True),
        sa.Column("uploaded_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime, index=True),
    )
    op.create_table(
        "wait_time_observations",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("import_id", sa.Integer,
                  sa.ForeignKey("wait_time_imports.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("station_id", sa.String, index=True),
        sa.Column("station_name", sa.String, nullable=True),
        sa.Column("test_date", sa.DateTime, index=True),
        sa.Column("day_of_month", sa.Integer, nullable=True),
        sa.Column("metric_name", sa.String, index=True),
        sa.Column("hourly_values", sa.Text, nullable=False),  # JSON array of 14 nullable floats
        sa.Column("created_at", sa.DateTime),
    )
    op.create_table(
        "wait_time_ld_rules",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("proposal_id", sa.Integer, sa.ForeignKey("proposals.id"), nullable=True, index=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("is_baseline", sa.Boolean, server_default=sa.text("0")),
        sa.Column("threshold_minutes", sa.Float, nullable=False),
        sa.Column("mode", sa.String, nullable=False),
        sa.Column("dollars_per_unit", sa.Float, server_default="0.0"),
        sa.Column("daily_cap_usd", sa.Float, nullable=True),
        sa.Column("monthly_cap_usd", sa.Float, nullable=True),
        sa.Column("grace_period_minutes", sa.Float, server_default="0.0"),
        sa.Column("exclude_hours_csv", sa.String, nullable=True),
        sa.Column("count_null_hours_as_breach", sa.Boolean, server_default=sa.text("0")),
        sa.Column("tiers_json", sa.Text, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
    )
    op.create_table(
        "wait_time_ab_runs",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("proposal_id", sa.Integer, sa.ForeignKey("proposals.id"), nullable=True, index=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("import_id", sa.Integer,
                  sa.ForeignKey("wait_time_imports.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("old_rule_id", sa.Integer, sa.ForeignKey("wait_time_ld_rules.id"), nullable=False),
        sa.Column("new_rule_id", sa.Integer, sa.ForeignKey("wait_time_ld_rules.id"), nullable=False),
        sa.Column("old_total_usd", sa.Float, server_default="0.0"),
        sa.Column("new_total_usd", sa.Float, server_default="0.0"),
        sa.Column("delta_usd", sa.Float, server_default="0.0"),
        sa.Column("breaches_old", sa.Integer, server_default="0"),
        sa.Column("breaches_new", sa.Integer, server_default="0"),
        sa.Column("breakdown_json", sa.Text, nullable=True),
        sa.Column("warnings_json", sa.Text, nullable=True),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime, index=True),
    )


def downgrade() -> None:
    op.drop_table("wait_time_ab_runs")
    op.drop_table("wait_time_ld_rules")
    op.drop_table("wait_time_observations")
    op.drop_table("wait_time_imports")
