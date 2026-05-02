"""Pricing model + competitor historical bid tables

Revision ID: 20260421_pricing
Revises: 20260420_085631
Create Date: 2026-04-21

Adds the what-if pricing engine (8 tabs, per-line CPI, scenarios, CIF/PIF
margin outputs) and per-competitor historical bid intelligence so writing
personas can adopt the competitor mentality.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '20260421_pricing'
down_revision: Union[str, None] = '20260420_085631'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── pricing_models ────────────────────────────────────────────
    op.create_table(
        'pricing_models',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('proposal_id', sa.Integer(), nullable=True),
        sa.Column('name', sa.String(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('version', sa.Integer(), nullable=True),
        sa.Column('base_years', sa.Integer(), nullable=True),
        sa.Column('extension_years', sa.Integer(), nullable=True),
        sa.Column('target_contract_value', sa.Float(), nullable=True),
        sa.Column('cif_volumes_json', sa.Text(), nullable=True),
        sa.Column('pif_volumes_json', sa.Text(), nullable=True),
        sa.Column('cif_margin_pct', sa.Float(), nullable=True),
        sa.Column('pif_margin_pct', sa.Float(), nullable=True),
        sa.Column('computed_total_cost', sa.Float(), nullable=True),
        sa.Column('computed_cif_cost', sa.Float(), nullable=True),
        sa.Column('computed_pif_cost', sa.Float(), nullable=True),
        sa.Column('computed_cif_ppt', sa.Float(), nullable=True),
        sa.Column('computed_pif_ppt', sa.Float(), nullable=True),
        sa.Column('computed_cif_revenue', sa.Float(), nullable=True),
        sa.Column('computed_pif_revenue', sa.Float(), nullable=True),
        sa.Column('computed_cif_gp', sa.Float(), nullable=True),
        sa.Column('computed_pif_gp', sa.Float(), nullable=True),
        sa.Column('computed_total_revenue', sa.Float(), nullable=True),
        sa.Column('computed_total_gp', sa.Float(), nullable=True),
        sa.Column('computed_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['proposal_id'], ['proposals.id']),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_pricing_models_id'), 'pricing_models', ['id'], unique=False)
    op.create_index(op.f('ix_pricing_models_name'), 'pricing_models', ['name'], unique=False)
    op.create_index(op.f('ix_pricing_models_proposal_id'), 'pricing_models', ['proposal_id'], unique=False)

    # ── pricing_scenarios ─────────────────────────────────────────
    op.create_table(
        'pricing_scenarios',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('pricing_model_id', sa.Integer(), nullable=True),
        sa.Column('name', sa.String(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_default', sa.Boolean(), nullable=True),
        sa.Column('cif_margin_pct_override', sa.Float(), nullable=True),
        sa.Column('pif_margin_pct_override', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['pricing_model_id'], ['pricing_models.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_pricing_scenarios_id'), 'pricing_scenarios', ['id'], unique=False)
    op.create_index(op.f('ix_pricing_scenarios_pricing_model_id'), 'pricing_scenarios', ['pricing_model_id'], unique=False)

    # ── pricing_categories ────────────────────────────────────────
    op.create_table(
        'pricing_categories',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('pricing_model_id', sa.Integer(), nullable=True),
        sa.Column('tab', sa.String(), nullable=True),
        sa.Column('name', sa.String(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=True),
        sa.Column('user_defined', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['pricing_model_id'], ['pricing_models.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_pricing_categories_id'), 'pricing_categories', ['id'], unique=False)
    op.create_index(op.f('ix_pricing_categories_pricing_model_id'), 'pricing_categories', ['pricing_model_id'], unique=False)
    op.create_index(op.f('ix_pricing_categories_tab'), 'pricing_categories', ['tab'], unique=False)

    # ── pricing_line_items ────────────────────────────────────────
    op.create_table(
        'pricing_line_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('category_id', sa.Integer(), nullable=True),
        sa.Column('name', sa.String(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('allocation_basis', sa.String(), nullable=True),
        sa.Column('included', sa.Boolean(), nullable=True),
        sa.Column('current_cost', sa.Float(), nullable=True),
        sa.Column('future_cost', sa.Float(), nullable=True),
        sa.Column('reduction_pct', sa.Float(), nullable=True),
        sa.Column('fringe_pct', sa.Float(), nullable=True),
        sa.Column('qty', sa.Float(), nullable=True),
        sa.Column('unit', sa.String(), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=True),
        sa.Column('user_defined', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['category_id'], ['pricing_categories.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_pricing_line_items_id'), 'pricing_line_items', ['id'], unique=False)
    op.create_index(op.f('ix_pricing_line_items_category_id'), 'pricing_line_items', ['category_id'], unique=False)

    # ── pricing_line_escalations ──────────────────────────────────
    op.create_table(
        'pricing_line_escalations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('line_item_id', sa.Integer(), nullable=True),
        sa.Column('year_idx', sa.Integer(), nullable=True),
        sa.Column('cpi_pct', sa.Float(), nullable=True),
        sa.Column('override_amount', sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(['line_item_id'], ['pricing_line_items.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_pricing_line_escalations_id'), 'pricing_line_escalations', ['id'], unique=False)
    op.create_index(op.f('ix_pricing_line_escalations_line_item_id'), 'pricing_line_escalations', ['line_item_id'], unique=False)

    # ── pricing_scenario_overrides ────────────────────────────────
    op.create_table(
        'pricing_scenario_overrides',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('scenario_id', sa.Integer(), nullable=True),
        sa.Column('line_item_id', sa.Integer(), nullable=True),
        sa.Column('field', sa.String(), nullable=True),
        sa.Column('year_idx', sa.Integer(), nullable=True),
        sa.Column('value_numeric', sa.Float(), nullable=True),
        sa.Column('value_bool', sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(['scenario_id'], ['pricing_scenarios.id']),
        sa.ForeignKeyConstraint(['line_item_id'], ['pricing_line_items.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_pricing_scenario_overrides_id'), 'pricing_scenario_overrides', ['id'], unique=False)
    op.create_index(op.f('ix_pricing_scenario_overrides_scenario_id'), 'pricing_scenario_overrides', ['scenario_id'], unique=False)
    op.create_index(op.f('ix_pricing_scenario_overrides_line_item_id'), 'pricing_scenario_overrides', ['line_item_id'], unique=False)

    # ── competitor_historical_bids ────────────────────────────────
    op.create_table(
        'competitor_historical_bids',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('competitor_id', sa.Integer(), nullable=True),
        sa.Column('rfp_name', sa.String(), nullable=True),
        sa.Column('state', sa.String(), nullable=True),
        sa.Column('bid_year', sa.Integer(), nullable=True),
        sa.Column('contract_term_years', sa.Integer(), nullable=True),
        sa.Column('total_value', sa.Float(), nullable=True),
        sa.Column('award_status', sa.String(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('source_doc', sa.String(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['competitor_id'], ['competitors.id']),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_competitor_historical_bids_id'), 'competitor_historical_bids', ['id'], unique=False)
    op.create_index(op.f('ix_competitor_historical_bids_competitor_id'), 'competitor_historical_bids', ['competitor_id'], unique=False)

    # ── competitor_bid_line_items ─────────────────────────────────
    op.create_table(
        'competitor_bid_line_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('historical_bid_id', sa.Integer(), nullable=True),
        sa.Column('category', sa.String(), nullable=True),
        sa.Column('line_name', sa.String(), nullable=True),
        sa.Column('qty', sa.Float(), nullable=True),
        sa.Column('unit_price', sa.Float(), nullable=True),
        sa.Column('total_value', sa.Float(), nullable=True),
        sa.Column('annual_values_json', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['historical_bid_id'], ['competitor_historical_bids.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_competitor_bid_line_items_id'), 'competitor_bid_line_items', ['id'], unique=False)
    op.create_index(op.f('ix_competitor_bid_line_items_historical_bid_id'), 'competitor_bid_line_items', ['historical_bid_id'], unique=False)

    # ── competitor_bid_strategies ─────────────────────────────────
    op.create_table(
        'competitor_bid_strategies',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('historical_bid_id', sa.Integer(), nullable=True),
        sa.Column('competitor_id', sa.Integer(), nullable=True),
        sa.Column('strategy_label', sa.String(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('evidence', sa.Text(), nullable=True),
        sa.Column('confidence', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['historical_bid_id'], ['competitor_historical_bids.id']),
        sa.ForeignKeyConstraint(['competitor_id'], ['competitors.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_competitor_bid_strategies_id'), 'competitor_bid_strategies', ['id'], unique=False)
    op.create_index(op.f('ix_competitor_bid_strategies_historical_bid_id'), 'competitor_bid_strategies', ['historical_bid_id'], unique=False)
    op.create_index(op.f('ix_competitor_bid_strategies_competitor_id'), 'competitor_bid_strategies', ['competitor_id'], unique=False)


def downgrade() -> None:
    op.drop_table('competitor_bid_strategies')
    op.drop_table('competitor_bid_line_items')
    op.drop_table('competitor_historical_bids')
    op.drop_table('pricing_scenario_overrides')
    op.drop_table('pricing_line_escalations')
    op.drop_table('pricing_line_items')
    op.drop_table('pricing_categories')
    op.drop_table('pricing_scenarios')
    op.drop_table('pricing_models')
