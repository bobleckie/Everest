"""Pricing Model API — what-if engine for CIF/PIF price-per-transaction.

Endpoints
---------
Pricing models:
    GET    /api/pricing/models                  — list all
    POST   /api/pricing/models                  — create
    GET    /api/pricing/models/{id}             — detail (header only)
    PUT    /api/pricing/models/{id}             — update header fields
    DELETE /api/pricing/models/{id}             — delete
    GET    /api/pricing/models/{id}/full        — full tree (categories + lines + escalations)
    POST   /api/pricing/models/{id}/compute     — recompute & persist outputs
    POST   /api/pricing/models/{id}/clone       — clone into a new model

Scenarios:
    GET    /api/pricing/models/{id}/scenarios
    POST   /api/pricing/models/{id}/scenarios
    PUT    /api/pricing/scenarios/{sid}
    DELETE /api/pricing/scenarios/{sid}

Categories:
    POST   /api/pricing/models/{id}/categories
    PUT    /api/pricing/categories/{cid}
    DELETE /api/pricing/categories/{cid}

Line items:
    POST   /api/pricing/categories/{cid}/line-items
    PUT    /api/pricing/line-items/{lid}
    DELETE /api/pricing/line-items/{lid}
    PUT    /api/pricing/line-items/{lid}/escalations   — upsert escalation table

Historical competitor bids:
    GET    /api/pricing/competitor-bids?competitor_id=
    POST   /api/pricing/competitor-bids
    GET    /api/pricing/competitor-bids/{hid}
    PUT    /api/pricing/competitor-bids/{hid}
    DELETE /api/pricing/competitor-bids/{hid}
    POST   /api/pricing/competitor-bids/{hid}/line-items
    POST   /api/pricing/competitor-bids/{hid}/strategies
"""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from ..auth import get_current_admin_user, get_current_user
from ..database import SessionLocal, get_db
from ..models import (
    CompetitorBidLineItem,
    CompetitorBidStrategy,
    CompetitorHistoricalBid,
    PricingAuditLog,
    PricingCategory,
    PricingCoverageRun,
    PricingCoverageSuggestion,
    PricingLineEscalation,
    PricingLineItem,
    PricingModel,
    PricingScenario,
    PricingScenarioOverride,
    RfpRequirement,
    StaffingPosition,
    User,
)
from ..services.pricing_coverage import (
    accept_suggestion as _pc_accept,
    reject_suggestion as _pc_reject,
    run_coverage_analysis as _pc_run,
)

router = APIRouter()


# ═══════════════════════════════════════════════════════════════════
# Bulletproof helpers — rounding, audit, version, invalidation
# ═══════════════════════════════════════════════════════════════════

_MONEY_Q = Decimal("0.01")     # dollars to cents
_RATE_Q  = Decimal("0.0001")   # rates to 4 decimals (per-txn price)
_PCT_Q   = Decimal("0.0001")   # percentages to 4 decimals


def _q_money(v: Any) -> float:
    """Round a number to 2 decimal places (dollars), half-up."""
    if v is None:
        return 0.0
    try:
        return float(Decimal(str(v)).quantize(_MONEY_Q, rounding=ROUND_HALF_UP))
    except Exception:
        return 0.0


def _q_rate(v: Any) -> float:
    """Round a number to 4 decimal places (per-transaction price)."""
    if v is None:
        return 0.0
    try:
        return float(Decimal(str(v)).quantize(_RATE_Q, rounding=ROUND_HALF_UP))
    except Exception:
        return 0.0


def _audit(
    db: Session,
    model_id: int,
    version: int,
    action: str,
    entity_type: str,
    entity_id: Optional[int] = None,
    field: Optional[str] = None,
    old_value: Any = None,
    new_value: Any = None,
    user_id: Optional[int] = None,
    note: Optional[str] = None,
) -> None:
    """Write an audit-log row. Never raises. Always a no-op flush-safe call."""
    try:
        db.add(PricingAuditLog(
            pricing_model_id=model_id,
            version=version,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            field=field,
            old_value=json.dumps(old_value, default=str) if old_value is not None else None,
            new_value=json.dumps(new_value, default=str) if new_value is not None else None,
            user_id=user_id,
            note=note,
        ))
    except Exception:
        pass  # audit must never break a mutation


def _assert_version(m: PricingModel, expected: Optional[int]) -> None:
    """Optimistic lock check — raise 409 if the client's version is stale."""
    if expected is not None and (m.version or 1) != expected:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Version conflict: your view of this pricing model is version "
                f"{expected} but the server has {m.version}. Refresh and retry."
            ),
        )


def _bump(m: PricingModel) -> int:
    """Increment model version and return the new version."""
    m.version = (m.version or 0) + 1
    m.updated_at = datetime.utcnow()
    return m.version


def _model_of_line(db: Session, line_id: int) -> Optional[PricingModel]:
    line = db.query(PricingLineItem).get(line_id)
    if not line:
        return None
    cat = db.query(PricingCategory).get(line.category_id)
    if not cat:
        return None
    return db.query(PricingModel).get(cat.pricing_model_id)


def _model_of_category(db: Session, cat_id: int) -> Optional[PricingModel]:
    cat = db.query(PricingCategory).get(cat_id)
    if not cat:
        return None
    return db.query(PricingModel).get(cat.pricing_model_id)


# ───────────────────────── Schemas ──────────────────────────────────

class EscalationIn(BaseModel):
    year_idx: int = Field(..., ge=1, le=25)
    cpi_pct: float = Field(0.0, ge=-50.0, le=50.0)
    override_amount: Optional[float] = Field(None, ge=0)


class EscalationOut(BaseModel):
    id: int
    year_idx: int
    cpi_pct: float
    override_amount: Optional[float]


_ALLOCATION_SET = {"CIF", "PIF", "SHARED", "CAPITAL_AMORT"}
_SCENARIO_OVERRIDE_FIELDS = {
    "included", "current_cost", "future_cost", "reduction_pct",
    "fringe_pct", "burden_pct", "ga_pct", "fee_pct", "qty", "cpi_pct",
}


class LineItemIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    notes: Optional[str] = None
    allocation_basis: str = "SHARED"
    included: bool = True
    current_cost: float = Field(0.0, ge=0)
    future_cost: float = Field(0.0, ge=0)
    reduction_pct: float = Field(0.0, ge=-100, le=100)
    fringe_pct: float = Field(0.0, ge=-100, le=500)
    burden_pct: float = Field(0.0, ge=0, le=500)
    ga_pct: float = Field(0.0, ge=0, le=500)
    fee_pct: float = Field(0.0, ge=0, le=500)
    qty: float = Field(1.0, ge=0)
    unit: str = "each"
    sort_order: int = 0
    user_defined: bool = False
    escalations: Optional[List[EscalationIn]] = None

    @field_validator("allocation_basis")
    @classmethod
    def _v_alloc(cls, v: str) -> str:
        v = (v or "SHARED").upper()
        if v not in _ALLOCATION_SET:
            raise ValueError(f"allocation_basis must be one of {_ALLOCATION_SET}")
        return v


class LineItemUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    notes: Optional[str] = None
    allocation_basis: Optional[str] = None
    included: Optional[bool] = None
    current_cost: Optional[float] = Field(None, ge=0)
    future_cost: Optional[float] = Field(None, ge=0)
    reduction_pct: Optional[float] = Field(None, ge=-100, le=100)
    fringe_pct: Optional[float] = Field(None, ge=-100, le=500)
    burden_pct: Optional[float] = Field(None, ge=0, le=500)
    ga_pct: Optional[float] = Field(None, ge=0, le=500)
    fee_pct: Optional[float] = Field(None, ge=0, le=500)
    qty: Optional[float] = Field(None, ge=0)
    unit: Optional[str] = None
    sort_order: Optional[int] = None

    @field_validator("allocation_basis")
    @classmethod
    def _v_alloc(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.upper()
        if v not in _ALLOCATION_SET:
            raise ValueError(f"allocation_basis must be one of {_ALLOCATION_SET}")
        return v


class LineItemOut(BaseModel):
    id: int
    category_id: int
    name: str
    description: Optional[str]
    notes: Optional[str]
    allocation_basis: str
    included: bool
    current_cost: float
    future_cost: float
    reduction_pct: float
    fringe_pct: float
    burden_pct: float = 0.0
    ga_pct: float = 0.0
    fee_pct: float = 0.0
    qty: float
    unit: str
    sort_order: int
    user_defined: bool
    escalations: List[EscalationOut] = []


class CategoryIn(BaseModel):
    tab: str
    name: str
    description: Optional[str] = None
    sort_order: int = 0
    user_defined: bool = False


class CategoryUpdate(BaseModel):
    tab: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    sort_order: Optional[int] = None


class CategoryOut(BaseModel):
    id: int
    pricing_model_id: int
    tab: str
    name: str
    description: Optional[str]
    sort_order: int
    user_defined: bool
    line_items: List[LineItemOut] = []


class ScenarioIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    is_default: bool = False
    cif_margin_pct_override: Optional[float] = Field(None, ge=-50, le=95)
    pif_margin_pct_override: Optional[float] = Field(None, ge=-50, le=95)


class ScenarioOverrideIn(BaseModel):
    """Sparse field-level override for a single line item within a scenario."""
    line_item_id: int
    field: str
    year_idx: Optional[int] = Field(None, ge=1, le=25)
    value_numeric: Optional[float] = None
    value_bool: Optional[bool] = None

    @field_validator("field")
    @classmethod
    def _v_field(cls, v: str) -> str:
        if v not in _SCENARIO_OVERRIDE_FIELDS:
            raise ValueError(f"field must be one of {sorted(_SCENARIO_OVERRIDE_FIELDS)}")
        return v


class ScenarioOverrideOut(BaseModel):
    id: int
    scenario_id: int
    line_item_id: int
    field: str
    year_idx: Optional[int]
    value_numeric: Optional[float]
    value_bool: Optional[bool]


class CloneModelIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None


class ScenarioOut(BaseModel):
    id: int
    pricing_model_id: int
    name: str
    description: Optional[str]
    is_default: bool
    cif_margin_pct_override: Optional[float]
    pif_margin_pct_override: Optional[float]


class PricingModelIn(BaseModel):
    proposal_id: Optional[int] = None
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    base_years: int = Field(6, ge=1, le=20)
    extension_years: int = Field(4, ge=0, le=10)
    target_contract_value: float = Field(0.0, ge=0)
    cif_volumes: Optional[List[float]] = None
    pif_volumes: Optional[List[float]] = None
    # Margins are REQUIRED — there is no honest default. The previous
    # 12.0 / 15.0 defaults produced suspicious "did the system fabricate
    # this?" numbers that a real bid model must never carry.
    cif_margin_pct: float = Field(..., ge=-50, le=95)
    pif_margin_pct: float = Field(..., ge=-50, le=95)

    @field_validator("cif_volumes", "pif_volumes")
    @classmethod
    def _v_vols(cls, v):
        if v is None:
            return v
        if any((x or 0) < 0 for x in v):
            raise ValueError("volumes must be non-negative")
        return v


class PricingModelUpdate(BaseModel):
    proposal_id: Optional[int] = None
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    base_years: Optional[int] = Field(None, ge=1, le=20)
    extension_years: Optional[int] = Field(None, ge=0, le=10)
    target_contract_value: Optional[float] = Field(None, ge=0)
    cif_volumes: Optional[List[float]] = None
    pif_volumes: Optional[List[float]] = None
    cif_margin_pct: Optional[float] = Field(None, ge=-50, le=95)
    pif_margin_pct: Optional[float] = Field(None, ge=-50, le=95)
    expected_version: Optional[int] = None  # optimistic concurrency lock

    @field_validator("cif_volumes", "pif_volumes")
    @classmethod
    def _v_vols(cls, v):
        if v is None:
            return v
        if any((x or 0) < 0 for x in v):
            raise ValueError("volumes must be non-negative")
        return v


class PricingModelOut(BaseModel):
    id: int
    proposal_id: Optional[int]
    name: str
    description: Optional[str]
    version: int
    base_years: int
    extension_years: int
    target_contract_value: float
    cif_volumes: List[float]
    pif_volumes: List[float]
    cif_margin_pct: float
    pif_margin_pct: float
    computed_total_cost: float
    computed_cif_cost: float
    computed_pif_cost: float
    computed_cif_ppt: float
    computed_pif_ppt: float
    computed_cif_revenue: float
    computed_pif_revenue: float
    computed_cif_gp: float
    computed_pif_gp: float
    computed_total_revenue: float
    computed_total_gp: float
    computed_at: Optional[str]
    created_at: Optional[str]


class PricingModelFullOut(PricingModelOut):
    scenarios: List[ScenarioOut] = []
    categories: List[CategoryOut] = []


# ─── Competitor historical bid schemas ───

class BidLineItemIn(BaseModel):
    category: Optional[str] = None
    line_name: str
    qty: Optional[float] = None
    unit_price: Optional[float] = None
    total_value: Optional[float] = None
    annual_values: Optional[List[float]] = None
    notes: Optional[str] = None


class BidLineItemOut(BaseModel):
    id: int
    historical_bid_id: int
    category: Optional[str]
    line_name: str
    qty: Optional[float]
    unit_price: Optional[float]
    total_value: Optional[float]
    annual_values: Optional[List[float]]
    notes: Optional[str]


class BidStrategyIn(BaseModel):
    strategy_label: str
    description: str
    evidence: Optional[str] = None
    confidence: str = "medium"


class BidStrategyOut(BaseModel):
    id: int
    historical_bid_id: int
    competitor_id: int
    strategy_label: str
    description: str
    evidence: Optional[str]
    confidence: str


class HistoricalBidIn(BaseModel):
    competitor_id: int
    rfp_name: str
    state: Optional[str] = None
    bid_year: Optional[int] = None
    contract_term_years: Optional[int] = None
    total_value: Optional[float] = None
    award_status: Optional[str] = None  # won | lost | withdrew
    summary: Optional[str] = None
    source_doc: Optional[str] = None
    notes: Optional[str] = None


class HistoricalBidUpdate(BaseModel):
    rfp_name: Optional[str] = None
    state: Optional[str] = None
    bid_year: Optional[int] = None
    contract_term_years: Optional[int] = None
    total_value: Optional[float] = None
    award_status: Optional[str] = None
    summary: Optional[str] = None
    source_doc: Optional[str] = None
    notes: Optional[str] = None


class HistoricalBidOut(BaseModel):
    id: int
    competitor_id: int
    rfp_name: str
    state: Optional[str]
    bid_year: Optional[int]
    contract_term_years: Optional[int]
    total_value: Optional[float]
    award_status: Optional[str]
    summary: Optional[str]
    source_doc: Optional[str]
    notes: Optional[str]
    created_at: Optional[str]
    line_items: List[BidLineItemOut] = []
    strategies: List[BidStrategyOut] = []


# ─── Staffing schemas ───────────────────────────────────────────────

CLASSIFICATIONS = {"hourly_key_personnel", "salaried_mgmt", "union_hourly", "development", "odc", "field_operations"}
PAY_TYPES = {"hourly", "salaried"}
ENGAGEMENT_TYPES = {"fte", "project", "implementation", "temporary", "contract"}

class StaffingPositionIn(BaseModel):
    role_title: str
    classification: str = "salaried_mgmt"
    pay_type: str = "hourly"
    base_salary: float = 0.0
    hourly_rate: float = 0.0
    hours_per_year: float = 1696.0
    overtime_eligible: bool = False
    overtime_pct: float = 0.0
    fringe_pct: float = 0.0
    burden_pct: float = 0.0
    ga_pct: float = 0.0
    fee_pct: float = 0.0
    headcount_by_year: List[float] = []
    escalation_pct: float = 0.0
    engagement_type: str = "fte"
    allocation_basis: str = "SHARED"
    notes: Optional[str] = None
    sort_order: int = 0
    included: bool = True

    @field_validator("classification")
    @classmethod
    def _valid_cls(cls, v: str) -> str:
        if v not in CLASSIFICATIONS:
            raise ValueError(f"classification must be one of {sorted(CLASSIFICATIONS)}")
        return v

    @field_validator("pay_type")
    @classmethod
    def _valid_pay(cls, v: str) -> str:
        if v not in PAY_TYPES:
            raise ValueError(f"pay_type must be one of {sorted(PAY_TYPES)}")
        return v

    @field_validator("engagement_type")
    @classmethod
    def _valid_eng(cls, v: str) -> str:
        if v not in ENGAGEMENT_TYPES:
            raise ValueError(f"engagement_type must be one of {sorted(ENGAGEMENT_TYPES)}")
        return v


class StaffingPositionUpdate(BaseModel):
    role_title: Optional[str] = None
    classification: Optional[str] = None
    pay_type: Optional[str] = None
    base_salary: Optional[float] = None
    hourly_rate: Optional[float] = None
    hours_per_year: Optional[float] = None
    overtime_eligible: Optional[bool] = None
    overtime_pct: Optional[float] = None
    fringe_pct: Optional[float] = None
    burden_pct: Optional[float] = None
    ga_pct: Optional[float] = None
    fee_pct: Optional[float] = None
    headcount_by_year: Optional[List[float]] = None
    escalation_pct: Optional[float] = None
    engagement_type: Optional[str] = None
    allocation_basis: Optional[str] = None
    notes: Optional[str] = None
    sort_order: Optional[int] = None
    included: Optional[bool] = None


class StaffingPositionOut(BaseModel):
    id: int
    pricing_model_id: int
    role_title: str
    classification: str
    pay_type: str
    base_salary: float
    hourly_rate: float
    hours_per_year: float
    overtime_eligible: bool
    overtime_pct: float
    fringe_pct: float
    burden_pct: float
    ga_pct: float
    fee_pct: float
    headcount_by_year: List[float]
    escalation_pct: float
    engagement_type: str
    allocation_basis: str
    notes: Optional[str]
    sort_order: int
    included: bool
    user_defined: bool
    # Computed fields (populated by serializer)
    annual_costs: List[float] = []   # loaded annual cost per year
    total_cost: float = 0.0

    class Config:
        from_attributes = True


# ───────────────────────── Serializers ──────────────────────────────

def _load_list(text: Optional[str]) -> List[float]:
    if not text:
        return []
    try:
        val = json.loads(text)
        return [float(x) for x in val] if isinstance(val, list) else []
    except Exception:
        return []


def _dump_list(vals: Optional[List[float]]) -> Optional[str]:
    if vals is None:
        return None
    return json.dumps([float(x) for x in vals])


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _esc_out(esc: PricingLineEscalation) -> EscalationOut:
    return EscalationOut(
        id=esc.id,
        year_idx=esc.year_idx,
        cpi_pct=esc.cpi_pct or 0.0,
        override_amount=esc.override_amount,
    )


def _line_out(line: PricingLineItem) -> LineItemOut:
    return LineItemOut(
        id=line.id,
        category_id=line.category_id,
        name=line.name,
        description=line.description,
        notes=line.notes,
        allocation_basis=line.allocation_basis or "SHARED",
        included=bool(line.included),
        current_cost=line.current_cost or 0.0,
        future_cost=line.future_cost or 0.0,
        reduction_pct=line.reduction_pct or 0.0,
        fringe_pct=line.fringe_pct or 0.0,
        burden_pct=line.burden_pct or 0.0,
        ga_pct=line.ga_pct or 0.0,
        fee_pct=line.fee_pct or 0.0,
        qty=line.qty or 0.0,
        unit=line.unit or "each",
        sort_order=line.sort_order or 0,
        user_defined=bool(line.user_defined),
        escalations=[_esc_out(e) for e in sorted(line.escalations or [], key=lambda e: e.year_idx)]
        if hasattr(line, 'escalations') else [],
    )


def _cat_out(cat: PricingCategory, lines: List[PricingLineItem]) -> CategoryOut:
    return CategoryOut(
        id=cat.id,
        pricing_model_id=cat.pricing_model_id,
        tab=cat.tab,
        name=cat.name,
        description=cat.description,
        sort_order=cat.sort_order or 0,
        user_defined=bool(cat.user_defined),
        line_items=[_line_out(l) for l in lines],
    )


def _scenario_out(s: PricingScenario) -> ScenarioOut:
    return ScenarioOut(
        id=s.id,
        pricing_model_id=s.pricing_model_id,
        name=s.name,
        description=s.description,
        is_default=bool(s.is_default),
        cif_margin_pct_override=s.cif_margin_pct_override,
        pif_margin_pct_override=s.pif_margin_pct_override,
    )


def _model_out(m: PricingModel) -> PricingModelOut:
    return PricingModelOut(
        id=m.id,
        proposal_id=m.proposal_id,
        name=m.name,
        description=m.description,
        version=m.version or 1,
        base_years=m.base_years or 0,
        extension_years=m.extension_years or 0,
        target_contract_value=m.target_contract_value or 0.0,
        cif_volumes=_load_list(m.cif_volumes_json),
        pif_volumes=_load_list(m.pif_volumes_json),
        cif_margin_pct=m.cif_margin_pct or 0.0,
        pif_margin_pct=m.pif_margin_pct or 0.0,
        computed_total_cost=m.computed_total_cost or 0.0,
        computed_cif_cost=m.computed_cif_cost or 0.0,
        computed_pif_cost=m.computed_pif_cost or 0.0,
        computed_cif_ppt=m.computed_cif_ppt or 0.0,
        computed_pif_ppt=m.computed_pif_ppt or 0.0,
        computed_cif_revenue=m.computed_cif_revenue or 0.0,
        computed_pif_revenue=m.computed_pif_revenue or 0.0,
        computed_cif_gp=m.computed_cif_gp or 0.0,
        computed_pif_gp=m.computed_pif_gp or 0.0,
        computed_total_revenue=m.computed_total_revenue or 0.0,
        computed_total_gp=m.computed_total_gp or 0.0,
        computed_at=_iso(m.computed_at),
        created_at=_iso(m.created_at),
    )


def _bid_line_out(b: CompetitorBidLineItem) -> BidLineItemOut:
    return BidLineItemOut(
        id=b.id,
        historical_bid_id=b.historical_bid_id,
        category=b.category,
        line_name=b.line_name,
        qty=b.qty,
        unit_price=b.unit_price,
        total_value=b.total_value,
        annual_values=_load_list(b.annual_values_json) if b.annual_values_json else None,
        notes=b.notes,
    )


def _strategy_out(s: CompetitorBidStrategy) -> BidStrategyOut:
    return BidStrategyOut(
        id=s.id,
        historical_bid_id=s.historical_bid_id,
        competitor_id=s.competitor_id,
        strategy_label=s.strategy_label,
        description=s.description,
        evidence=s.evidence,
        confidence=s.confidence or "medium",
    )


def _bid_out(
    b: CompetitorHistoricalBid,
    line_items: List[CompetitorBidLineItem],
    strategies: List[CompetitorBidStrategy],
) -> HistoricalBidOut:
    return HistoricalBidOut(
        id=b.id,
        competitor_id=b.competitor_id,
        rfp_name=b.rfp_name,
        state=b.state,
        bid_year=b.bid_year,
        contract_term_years=b.contract_term_years,
        total_value=b.total_value,
        award_status=b.award_status,
        summary=b.summary,
        source_doc=b.source_doc,
        notes=b.notes,
        created_at=_iso(b.created_at),
        line_items=[_bid_line_out(x) for x in line_items],
        strategies=[_strategy_out(x) for x in strategies],
    )


# ───────────────────────── Compute engine ───────────────────────────

def _compound(base: float, cpi_pct_per_year: List[float], year_idx_1based: int) -> float:
    """Compound ``base`` forward by the given per-year CPI percents.

    ``year_idx_1based`` is 1 for year 1 (no compounding), 2 compounds once, etc.
    ``cpi_pct_per_year[i]`` applies going from year (i+1) to year (i+2).
    """
    if year_idx_1based <= 1:
        return base
    out = base
    # Compound years 2..N using cpi of (year-1)
    for y in range(2, year_idx_1based + 1):
        pct_idx = y - 2  # cpi of year-1
        pct = cpi_pct_per_year[pct_idx] if pct_idx < len(cpi_pct_per_year) else 0.0
        out = out * (1.0 + (pct / 100.0))
    return out


def _effective_fields(
    line: PricingLineItem,
    overrides_for_line: Dict[Tuple[str, Optional[int]], Any],
) -> Dict[str, Any]:
    """Resolve the effective line fields after applying scenario overrides.

    ``overrides_for_line`` is keyed by ``(field, year_idx)`` — ``year_idx``
    is ``None`` for non-escalation fields.
    """
    def _get(field: str, current):
        ov = overrides_for_line.get((field, None))
        return ov if ov is not None else current

    return {
        "included":      _get("included",      bool(line.included) if line.included is not None else True),
        "current_cost":  _get("current_cost",  line.current_cost or 0.0),
        "future_cost":   _get("future_cost",   line.future_cost or 0.0),
        "reduction_pct": _get("reduction_pct", line.reduction_pct or 0.0),
        "fringe_pct":    _get("fringe_pct",    line.fringe_pct or 0.0),
        "burden_pct":    _get("burden_pct",    getattr(line, "burden_pct", None) or 0.0),
        "ga_pct":        _get("ga_pct",        getattr(line, "ga_pct", None) or 0.0),
        "fee_pct":       _get("fee_pct",       getattr(line, "fee_pct", None) or 0.0),
        "qty":           _get("qty",           line.qty or 0.0),
        "unit":          (line.unit or "each"),
        "allocation_basis": (line.allocation_basis or "SHARED").upper(),
    }


def _line_effective_per_year(
    line: PricingLineItem,
    total_years: int,
    overrides_for_line: Optional[Dict[Tuple[str, Optional[int]], Any]] = None,
) -> List[float]:
    """Return the effective total cost PER YEAR for this line (qty-included).

    Rules:
      - included=False           -> zeros
      - base_unit                = future_cost * (1 - reduction_pct/100) * (1 + fringe_pct/100)
      - per-year value           = compounded base * qty
      - CAPITAL_AMORT            = (base_unit * qty) / total_years, no CPI
      - scenario overrides apply to: included | current_cost | future_cost |
        reduction_pct | fringe_pct | qty | cpi_pct (with year_idx)
      - per-year override_amount on PricingLineEscalation replaces the value
        for that year entirely (takes precedence over everything).
    """
    overrides_for_line = overrides_for_line or {}
    eff = _effective_fields(line, overrides_for_line)

    if not eff["included"]:
        return [0.0] * total_years

    red_factor = 1.0 - (eff["reduction_pct"] or 0.0) / 100.0
    fringe_factor = 1.0 + (eff["fringe_pct"] or 0.0) / 100.0
    base_unit = (eff["future_cost"] or 0.0) * red_factor * fringe_factor

    # Hourly bill-rate build-up (Gap #7): only stacks burden/G&A/fee when the
    # line is unit="hourly". This converts a fringed wage into a billable rate.
    if (eff.get("unit") or "").lower() == "hourly":
        burden_factor = 1.0 + (eff.get("burden_pct") or 0.0) / 100.0
        ga_factor     = 1.0 + (eff.get("ga_pct")     or 0.0) / 100.0
        fee_factor    = 1.0 + (eff.get("fee_pct")    or 0.0) / 100.0
        base_unit = base_unit * burden_factor * ga_factor * fee_factor

    qty = eff["qty"] or 0.0

    # Capital amortized = spread evenly over contract, no escalation
    if eff["allocation_basis"] == "CAPITAL_AMORT":
        total_cap = base_unit * qty
        per_year = total_cap / total_years if total_years > 0 else 0.0
        return [per_year] * total_years

    # Build CPI list from base escalations + overrides
    base_escs = sorted(line.escalations or [], key=lambda e: e.year_idx)
    cpi_list: List[float] = []
    for y in range(1, total_years + 1):
        ov_key = ("cpi_pct", y)
        if ov_key in overrides_for_line and overrides_for_line[ov_key] is not None:
            cpi_list.append(float(overrides_for_line[ov_key]))
        else:
            match = next((e for e in base_escs if e.year_idx == y), None)
            cpi_list.append((match.cpi_pct if match else 0.0) or 0.0)

    # Absolute-override amounts (replace the year entirely)
    override_map = {e.year_idx: e.override_amount for e in base_escs if e.override_amount is not None}

    out: List[float] = []
    for y in range(1, total_years + 1):
        if y in override_map and override_map[y] is not None:
            out.append(float(override_map[y]))
            continue
        escalated_unit = _compound(base_unit, cpi_list, y)
        out.append(escalated_unit * qty)
    return out


def _load_scenario_overrides(
    db: Session, scenario_id: Optional[int]
) -> Dict[int, Dict[Tuple[str, Optional[int]], Any]]:
    """Load scenario overrides indexed by line_id -> (field, year_idx) -> value."""
    if not scenario_id:
        return {}
    rows = (
        db.query(PricingScenarioOverride)
        .filter(PricingScenarioOverride.scenario_id == scenario_id)
        .all()
    )
    out: Dict[int, Dict[Tuple[str, Optional[int]], Any]] = {}
    for r in rows:
        bucket = out.setdefault(r.line_item_id, {})
        if r.field == "included":
            bucket[(r.field, None)] = bool(r.value_bool) if r.value_bool is not None else None
        elif r.field == "cpi_pct":
            bucket[(r.field, r.year_idx)] = r.value_numeric
        else:
            bucket[(r.field, None)] = r.value_numeric
    return out


def _compute_core(
    db: Session,
    model_id: int,
    scenario_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Pure numerical core of the compute engine.

    Returns a dict with: totals, per-year breakdown, and warnings. Does NOT
    persist anything. Used by both ``compute_model`` (persists to cached
    fields) and the cash-flow endpoint (read-only detail).
    """
    m = db.query(PricingModel).get(model_id)
    if not m:
        raise HTTPException(404, "Pricing model not found")

    warnings: List[str] = []

    total_years = (m.base_years or 0) + (m.extension_years or 0)
    if total_years <= 0:
        warnings.append("base_years + extension_years = 0; defaulting to 1 year.")
        total_years = 1

    cif_volumes = _load_list(m.cif_volumes_json)
    pif_volumes = _load_list(m.pif_volumes_json)
    if len(cif_volumes) < total_years:
        warnings.append(
            f"cif_volumes has {len(cif_volumes)} entries but contract has {total_years} years; "
            f"missing years treated as 0."
        )
    if len(pif_volumes) < total_years:
        warnings.append(
            f"pif_volumes has {len(pif_volumes)} entries but contract has {total_years} years; "
            f"missing years treated as 0."
        )
    cif_volumes = (cif_volumes + [0.0] * total_years)[:total_years]
    pif_volumes = (pif_volumes + [0.0] * total_years)[:total_years]

    # Scenario-level margin overrides (fall back to model margins)
    scenario: Optional[PricingScenario] = None
    cif_margin = m.cif_margin_pct or 0.0
    pif_margin = m.pif_margin_pct or 0.0
    if scenario_id:
        scenario = db.query(PricingScenario).get(scenario_id)
        if not scenario:
            raise HTTPException(404, f"Scenario {scenario_id} not found")
        if scenario.pricing_model_id != model_id:
            raise HTTPException(400, "Scenario does not belong to this pricing model")
        if scenario.cif_margin_pct_override is not None:
            cif_margin = scenario.cif_margin_pct_override
        if scenario.pif_margin_pct_override is not None:
            pif_margin = scenario.pif_margin_pct_override

    # Load lines + escalations
    categories = db.query(PricingCategory).filter(PricingCategory.pricing_model_id == m.id).all()
    cat_ids = [c.id for c in categories]
    lines = (
        db.query(PricingLineItem)
        .filter(PricingLineItem.category_id.in_(cat_ids))
        .all()
        if cat_ids else []
    )
    line_ids = [l.id for l in lines]
    escs = (
        db.query(PricingLineEscalation)
        .filter(PricingLineEscalation.line_item_id.in_(line_ids))
        .all()
        if line_ids else []
    )
    esc_by_line: Dict[int, List[PricingLineEscalation]] = {}
    for e in escs:
        esc_by_line.setdefault(e.line_item_id, []).append(e)
    for l in lines:
        l.escalations = esc_by_line.get(l.id, [])

    overrides_by_line = _load_scenario_overrides(db, scenario_id)

    # Per-year CIF/PIF costs, and per-line breakdowns for later detail
    cif_per_year = [0.0] * total_years
    pif_per_year = [0.0] * total_years
    line_breakdowns: List[Dict[str, Any]] = []

    zero_vol_shared_hit = False
    for line in lines:
        overrides = overrides_by_line.get(line.id, {})
        per_year = _line_effective_per_year(line, total_years, overrides)
        basis = (line.allocation_basis or "SHARED").upper()

        line_cif = [0.0] * total_years
        line_pif = [0.0] * total_years
        for y_idx, yearly in enumerate(per_year):
            if basis == "CIF":
                line_cif[y_idx] = yearly
            elif basis == "PIF":
                line_pif[y_idx] = yearly
            else:
                cif_v = cif_volumes[y_idx] if y_idx < len(cif_volumes) else 0.0
                pif_v = pif_volumes[y_idx] if y_idx < len(pif_volumes) else 0.0
                denom = cif_v + pif_v
                if denom > 0:
                    line_cif[y_idx] = yearly * (cif_v / denom)
                    line_pif[y_idx] = yearly * (pif_v / denom)
                else:
                    # No volumes — 50/50 fallback. Warn once.
                    if yearly > 0 and not zero_vol_shared_hit:
                        warnings.append(
                            "One or more SHARED/CAPITAL_AMORT lines had cost in years with "
                            "zero CIF+PIF volumes; a 50/50 fallback split was applied. "
                            "Populate cif_volumes/pif_volumes for a correct allocation."
                        )
                        zero_vol_shared_hit = True
                    line_cif[y_idx] = yearly * 0.5
                    line_pif[y_idx] = yearly * 0.5

        for y in range(total_years):
            cif_per_year[y] += line_cif[y]
            pif_per_year[y] += line_pif[y]

        line_breakdowns.append({
            "line_id": line.id,
            "name": line.name,
            "allocation_basis": basis,
            "per_year_total":  [_q_money(v) for v in per_year],
            "per_year_cif":    [_q_money(v) for v in line_cif],
            "per_year_pif":    [_q_money(v) for v in line_pif],
        })

    cif_cost = sum(cif_per_year)
    pif_cost = sum(pif_per_year)
    total_cost = cif_cost + pif_cost

    def _revenue_from_cost(cost: float, margin_pct: float) -> float:
        m_frac = max(min((margin_pct or 0.0) / 100.0, 0.9999), -0.9999)
        if m_frac >= 1.0:
            return cost
        return cost / (1.0 - m_frac)

    cif_rev = _revenue_from_cost(cif_cost, cif_margin)
    pif_rev = _revenue_from_cost(pif_cost, pif_margin)

    cif_total_vol = sum(cif_volumes) or 0.0
    pif_total_vol = sum(pif_volumes) or 0.0
    cif_ppt = (cif_rev / cif_total_vol) if cif_total_vol > 0 else 0.0
    pif_ppt = (pif_rev / pif_total_vol) if pif_total_vol > 0 else 0.0

    if cif_total_vol == 0:
        warnings.append("Total CIF volume is zero — CIF price-per-transaction cannot be computed.")
    if pif_total_vol == 0:
        warnings.append("Total PIF volume is zero — PIF price-per-transaction cannot be computed.")
    if cif_margin >= 100 or pif_margin >= 100:
        warnings.append("Margin >= 100% is mathematically impossible and was clamped to 99.99%.")

    # Per-year revenue allocation: split proportionally by that year's cost share
    cif_rev_per_year = []
    for y in range(total_years):
        share = (cif_per_year[y] / cif_cost) if cif_cost > 0 else (1.0 / total_years)
        cif_rev_per_year.append(cif_rev * share)
    pif_rev_per_year = []
    for y in range(total_years):
        share = (pif_per_year[y] / pif_cost) if pif_cost > 0 else (1.0 / total_years)
        pif_rev_per_year.append(pif_rev * share)

    cash_flow = []
    for y in range(total_years):
        c_c = cif_per_year[y]
        p_c = pif_per_year[y]
        c_r = cif_rev_per_year[y]
        p_r = pif_rev_per_year[y]
        cash_flow.append({
            "year_idx":    y + 1,
            "cif_cost":    _q_money(c_c),
            "pif_cost":    _q_money(p_c),
            "total_cost":  _q_money(c_c + p_c),
            "cif_revenue": _q_money(c_r),
            "pif_revenue": _q_money(p_r),
            "total_revenue": _q_money(c_r + p_r),
            "cif_gp":      _q_money(c_r - c_c),
            "pif_gp":      _q_money(p_r - p_c),
            "total_gp":    _q_money((c_r + p_r) - (c_c + p_c)),
            "cif_volume":  cif_volumes[y],
            "pif_volume":  pif_volumes[y],
        })

    return {
        "model_id": model_id,
        "scenario_id": scenario_id,
        "total_years": total_years,
        "cif_margin_pct": cif_margin,
        "pif_margin_pct": pif_margin,
        "totals": {
            "cif_cost":       _q_money(cif_cost),
            "pif_cost":       _q_money(pif_cost),
            "total_cost":     _q_money(total_cost),
            "cif_revenue":    _q_money(cif_rev),
            "pif_revenue":    _q_money(pif_rev),
            "total_revenue":  _q_money(cif_rev + pif_rev),
            "cif_gp":         _q_money(cif_rev - cif_cost),
            "pif_gp":         _q_money(pif_rev - pif_cost),
            "total_gp":       _q_money((cif_rev + pif_rev) - total_cost),
            "cif_ppt":        _q_rate(cif_ppt),
            "pif_ppt":        _q_rate(pif_ppt),
            "cif_total_volume": cif_total_vol,
            "pif_total_volume": pif_total_vol,
        },
        "cash_flow": cash_flow,
        "line_breakdowns": line_breakdowns,
        "warnings": warnings,
    }


def compute_model(
    db: Session,
    model_id: int,
    scenario_id: Optional[int] = None,
    persist: bool = True,
) -> PricingModel:
    """Recompute all output fields on a PricingModel.

    When ``persist`` is True (default), cached totals on ``PricingModel`` are
    updated. When a ``scenario_id`` is passed, computation uses scenario
    overrides but the BASE MODEL cache is NOT overwritten (scenario compute
    results are returned, not persisted, to keep the base case intact).
    """
    result = _compute_core(db, model_id, scenario_id=scenario_id)
    m = db.query(PricingModel).get(model_id)
    if not m:
        raise HTTPException(404, "Pricing model not found")

    # Only persist if this is the base/default compute (no scenario).
    if persist and not scenario_id:
        t = result["totals"]
        m.computed_total_cost = t["total_cost"]
        m.computed_cif_cost   = t["cif_cost"]
        m.computed_pif_cost   = t["pif_cost"]
        m.computed_cif_revenue = t["cif_revenue"]
        m.computed_pif_revenue = t["pif_revenue"]
        m.computed_cif_gp = t["cif_gp"]
        m.computed_pif_gp = t["pif_gp"]
        m.computed_cif_ppt = t["cif_ppt"]
        m.computed_pif_ppt = t["pif_ppt"]
        m.computed_total_revenue = t["total_revenue"]
        m.computed_total_gp = t["total_gp"]
        m.computed_at = datetime.utcnow()
        db.commit()
        db.refresh(m)
    # Stash result for callers that want full detail
    m._last_compute = result   # type: ignore[attr-defined]
    return m


# ───────────────────────── Pricing Model endpoints ──────────────────

@router.get("/models", response_model=List[PricingModelOut])
def list_pricing_models(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    models = db.query(PricingModel).order_by(PricingModel.created_at.desc()).all()
    return [_model_out(m) for m in models]


@router.post("/models", response_model=PricingModelOut)
def create_pricing_model(
    payload: PricingModelIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    m = PricingModel(
        proposal_id=payload.proposal_id,
        name=payload.name,
        description=payload.description,
        base_years=payload.base_years,
        extension_years=payload.extension_years,
        target_contract_value=payload.target_contract_value,
        cif_volumes_json=_dump_list(payload.cif_volumes),
        pif_volumes_json=_dump_list(payload.pif_volumes),
        cif_margin_pct=payload.cif_margin_pct,
        pif_margin_pct=payload.pif_margin_pct,
        created_by=user.id,
        version=1,
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    # Seed a default scenario
    default_sc = PricingScenario(
        pricing_model_id=m.id, name="Base Case", is_default=True
    )
    db.add(default_sc)
    _audit(db, m.id, m.version, "create", "model", m.id, user_id=user.id,
           new_value=payload.model_dump())
    db.commit()
    compute_model(db, m.id)
    db.refresh(m)
    return _model_out(m)


@router.get("/models/{model_id}", response_model=PricingModelOut)
def get_pricing_model(
    model_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    m = db.query(PricingModel).get(model_id)
    if not m:
        raise HTTPException(404, "Pricing model not found")
    return _model_out(m)


@router.get("/models/{model_id}/full", response_model=PricingModelFullOut)
def get_pricing_model_full(
    model_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    m = db.query(PricingModel).get(model_id)
    if not m:
        raise HTTPException(404, "Pricing model not found")

    scenarios = (
        db.query(PricingScenario).filter(PricingScenario.pricing_model_id == m.id).all()
    )
    categories = (
        db.query(PricingCategory)
        .filter(PricingCategory.pricing_model_id == m.id)
        .order_by(PricingCategory.tab, PricingCategory.sort_order, PricingCategory.id)
        .all()
    )
    cat_ids = [c.id for c in categories]
    lines = (
        db.query(PricingLineItem)
        .filter(PricingLineItem.category_id.in_(cat_ids))
        .order_by(PricingLineItem.sort_order, PricingLineItem.id)
        .all()
        if cat_ids else []
    )
    line_ids = [l.id for l in lines]
    escs = (
        db.query(PricingLineEscalation)
        .filter(PricingLineEscalation.line_item_id.in_(line_ids))
        .all()
        if line_ids else []
    )
    # Stitch escalations to their lines
    esc_by_line: dict = {}
    for e in escs:
        esc_by_line.setdefault(e.line_item_id, []).append(e)
    for l in lines:
        l.escalations = esc_by_line.get(l.id, [])  # attach dynamically for serializer

    lines_by_cat: dict = {}
    for l in lines:
        lines_by_cat.setdefault(l.category_id, []).append(l)

    out = PricingModelFullOut(
        **_model_out(m).model_dump(),
        scenarios=[_scenario_out(s) for s in scenarios],
        categories=[_cat_out(c, lines_by_cat.get(c.id, [])) for c in categories],
    )
    return out


@router.put("/models/{model_id}", response_model=PricingModelOut)
def update_pricing_model(
    model_id: int,
    payload: PricingModelUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    m = db.query(PricingModel).get(model_id)
    if not m:
        raise HTTPException(404, "Pricing model not found")
    data = payload.model_dump(exclude_unset=True)
    expected = data.pop("expected_version", None)
    _assert_version(m, expected)
    if "cif_volumes" in data:
        m.cif_volumes_json = _dump_list(data.pop("cif_volumes"))
    if "pif_volumes" in data:
        m.pif_volumes_json = _dump_list(data.pop("pif_volumes"))
    for k, v in data.items():
        setattr(m, k, v)
    new_ver = _bump(m)
    _audit(db, m.id, new_ver, "update", "model", m.id, user_id=user.id, new_value=data)
    db.commit()
    compute_model(db, m.id)
    db.refresh(m)
    return _model_out(m)


@router.delete("/models/{model_id}")
def delete_pricing_model(
    model_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_admin_user),
):
    m = db.query(PricingModel).get(model_id)
    if not m:
        raise HTTPException(404, "Pricing model not found")
    # Cascade delete children
    cats = db.query(PricingCategory).filter(PricingCategory.pricing_model_id == m.id).all()
    cat_ids = [c.id for c in cats]
    lines = db.query(PricingLineItem).filter(PricingLineItem.category_id.in_(cat_ids)).all() if cat_ids else []
    line_ids = [l.id for l in lines]
    if line_ids:
        db.query(PricingLineEscalation).filter(PricingLineEscalation.line_item_id.in_(line_ids)).delete(synchronize_session=False)
        db.query(PricingScenarioOverride).filter(PricingScenarioOverride.line_item_id.in_(line_ids)).delete(synchronize_session=False)
    for l in lines:
        db.delete(l)
    for c in cats:
        db.delete(c)
    db.query(PricingScenario).filter(PricingScenario.pricing_model_id == m.id).delete(synchronize_session=False)
    db.delete(m)
    db.commit()
    return {"deleted": model_id}


@router.post("/models/{model_id}/compute", response_model=PricingModelOut)
def compute_pricing_model(
    model_id: int,
    scenario_id: Optional[int] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    m = compute_model(db, model_id, scenario_id=scenario_id)
    return _model_out(m)


@router.get("/models/{model_id}/compute-detail")
def compute_detail(
    model_id: int,
    scenario_id: Optional[int] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Returns full compute detail including per-year cash flow and warnings.

    For scenarios, the base model's cached totals are NOT overwritten;
    computation is transient so the base case stays intact.
    """
    return _compute_core(db, model_id, scenario_id=scenario_id)


@router.get("/models/{model_id}/cash-flow")
def cash_flow(
    model_id: int,
    scenario_id: Optional[int] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Per-year cost / revenue / gross-profit breakdown for this model."""
    res = _compute_core(db, model_id, scenario_id=scenario_id)
    return {
        "model_id": res["model_id"],
        "scenario_id": res["scenario_id"],
        "total_years": res["total_years"],
        "cash_flow": res["cash_flow"],
        "warnings": res["warnings"],
    }


@router.get("/models/{model_id}/submittal")
def price_submittal(
    model_id: int,
    scenario_id: Optional[int] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Formal price submittal: per-line per-year unit price & totals.

    This is the structured data that feeds the RFP cost volume.
    """
    m = db.query(PricingModel).get(model_id)
    if not m:
        raise HTTPException(404, "Pricing model not found")
    res = _compute_core(db, model_id, scenario_id=scenario_id)
    total_years = res["total_years"]

    # Build a category lookup for labeling
    cats = {c.id: c for c in db.query(PricingCategory).filter(
        PricingCategory.pricing_model_id == model_id
    ).all()}
    lines = db.query(PricingLineItem).filter(
        PricingLineItem.category_id.in_(list(cats.keys()))
    ).all() if cats else []
    lines_by_id = {l.id: l for l in lines}

    rows: List[Dict[str, Any]] = []
    for lb in res["line_breakdowns"]:
        line = lines_by_id.get(lb["line_id"])
        if not line:
            continue
        cat = cats.get(line.category_id)
        rows.append({
            "tab": cat.tab if cat else "",
            "category": cat.name if cat else "",
            "line_id": line.id,
            "line_name": line.name,
            "unit": line.unit,
            "qty": line.qty,
            "allocation_basis": lb["allocation_basis"],
            "per_year_total": lb["per_year_total"],
            "per_year_cif": lb["per_year_cif"],
            "per_year_pif": lb["per_year_pif"],
            "total": _q_money(sum(lb["per_year_total"])),
        })

    return {
        "model_id": model_id,
        "scenario_id": scenario_id,
        "model_name": m.name,
        "model_version": m.version,
        "total_years": total_years,
        "totals": res["totals"],
        "line_items": rows,
        "warnings": res["warnings"],
        "generated_at": datetime.utcnow().isoformat(),
    }


@router.post("/models/{model_id}/clone", response_model=PricingModelOut)
def clone_pricing_model(
    model_id: int,
    payload: CloneModelIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Deep-copy a pricing model with all categories, line items, escalations,
    scenarios, and scenario overrides. The new model starts at version 1."""
    src = db.query(PricingModel).get(model_id)
    if not src:
        raise HTTPException(404, "Pricing model not found")

    # 1. Clone the model row
    dst = PricingModel(
        proposal_id=src.proposal_id,
        name=payload.name,
        description=payload.description or src.description,
        version=1,
        base_years=src.base_years,
        extension_years=src.extension_years,
        target_contract_value=src.target_contract_value,
        cif_volumes_json=src.cif_volumes_json,
        pif_volumes_json=src.pif_volumes_json,
        cif_margin_pct=src.cif_margin_pct,
        pif_margin_pct=src.pif_margin_pct,
        created_by=user.id,
    )
    db.add(dst)
    db.commit()
    db.refresh(dst)

    # 2. Clone categories and lines (build id-maps for FK rewriting)
    cat_id_map: Dict[int, int] = {}
    line_id_map: Dict[int, int] = {}

    src_cats = db.query(PricingCategory).filter(
        PricingCategory.pricing_model_id == src.id
    ).all()
    for c in src_cats:
        new_c = PricingCategory(
            pricing_model_id=dst.id,
            tab=c.tab, name=c.name, description=c.description,
            sort_order=c.sort_order, user_defined=c.user_defined,
        )
        db.add(new_c)
        db.flush()
        cat_id_map[c.id] = new_c.id

    src_lines = db.query(PricingLineItem).filter(
        PricingLineItem.category_id.in_([c.id for c in src_cats])
    ).all() if src_cats else []
    for l in src_lines:
        new_l = PricingLineItem(
            category_id=cat_id_map[l.category_id],
            name=l.name, description=l.description, notes=l.notes,
            allocation_basis=l.allocation_basis,
            included=l.included,
            current_cost=l.current_cost, future_cost=l.future_cost,
            reduction_pct=l.reduction_pct, fringe_pct=l.fringe_pct,
            burden_pct=getattr(l, "burden_pct", 0.0) or 0.0,
            ga_pct=getattr(l, "ga_pct", 0.0) or 0.0,
            fee_pct=getattr(l, "fee_pct", 0.0) or 0.0,
            qty=l.qty, unit=l.unit,
            sort_order=l.sort_order, user_defined=l.user_defined,
        )
        db.add(new_l)
        db.flush()
        line_id_map[l.id] = new_l.id

    # 3. Clone escalations
    src_escs = db.query(PricingLineEscalation).filter(
        PricingLineEscalation.line_item_id.in_(list(line_id_map.keys()))
    ).all() if line_id_map else []
    for e in src_escs:
        db.add(PricingLineEscalation(
            line_item_id=line_id_map[e.line_item_id],
            year_idx=e.year_idx,
            cpi_pct=e.cpi_pct,
            override_amount=e.override_amount,
        ))

    # 4. Clone scenarios + scenario overrides (remap line_item_id)
    src_scenarios = db.query(PricingScenario).filter(
        PricingScenario.pricing_model_id == src.id
    ).all()
    scen_id_map: Dict[int, int] = {}
    has_default = False
    for s in src_scenarios:
        new_s = PricingScenario(
            pricing_model_id=dst.id,
            name=s.name, description=s.description,
            is_default=s.is_default,
            cif_margin_pct_override=s.cif_margin_pct_override,
            pif_margin_pct_override=s.pif_margin_pct_override,
        )
        db.add(new_s)
        db.flush()
        scen_id_map[s.id] = new_s.id
        if s.is_default:
            has_default = True
    if not has_default:
        db.add(PricingScenario(pricing_model_id=dst.id, name="Base Case", is_default=True))

    src_ovr = db.query(PricingScenarioOverride).filter(
        PricingScenarioOverride.scenario_id.in_(list(scen_id_map.keys()))
    ).all() if scen_id_map else []
    for o in src_ovr:
        new_line = line_id_map.get(o.line_item_id)
        if new_line is None:
            continue
        db.add(PricingScenarioOverride(
            scenario_id=scen_id_map[o.scenario_id],
            line_item_id=new_line,
            field=o.field,
            year_idx=o.year_idx,
            value_numeric=o.value_numeric,
            value_bool=o.value_bool,
        ))

    _audit(db, dst.id, 1, "clone", "model", dst.id, user_id=user.id,
           note=f"Cloned from model {src.id}")
    db.commit()
    compute_model(db, dst.id)
    db.refresh(dst)
    return _model_out(dst)


# ───────────────────────── Scenarios ────────────────────────────────

@router.get("/models/{model_id}/scenarios", response_model=List[ScenarioOut])
def list_scenarios(
    model_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    return [
        _scenario_out(s)
        for s in db.query(PricingScenario).filter(PricingScenario.pricing_model_id == model_id).all()
    ]


@router.post("/models/{model_id}/scenarios", response_model=ScenarioOut)
def create_scenario(
    model_id: int,
    payload: ScenarioIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    m = db.query(PricingModel).get(model_id)
    if not m:
        raise HTTPException(404, "Pricing model not found")
    s = PricingScenario(pricing_model_id=model_id, **payload.model_dump())
    db.add(s)
    _bump(m)
    _audit(db, m.id, m.version, "create", "scenario", user_id=user.id,
           new_value=payload.model_dump())
    db.commit()
    db.refresh(s)
    return _scenario_out(s)


@router.put("/scenarios/{scenario_id}", response_model=ScenarioOut)
def update_scenario(
    scenario_id: int,
    payload: ScenarioIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = db.query(PricingScenario).get(scenario_id)
    if not s:
        raise HTTPException(404, "Scenario not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(s, k, v)
    m = db.query(PricingModel).get(s.pricing_model_id)
    if m:
        _bump(m)
        _audit(db, m.id, m.version, "update", "scenario", s.id, user_id=user.id, new_value=data)
    db.commit()
    db.refresh(s)
    return _scenario_out(s)


@router.delete("/scenarios/{scenario_id}")
def delete_scenario(
    scenario_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    s = db.query(PricingScenario).get(scenario_id)
    if not s:
        raise HTTPException(404, "Scenario not found")
    if s.is_default:
        raise HTTPException(400, "Cannot delete the default scenario.")
    model_id = s.pricing_model_id
    db.query(PricingScenarioOverride).filter(PricingScenarioOverride.scenario_id == scenario_id).delete(synchronize_session=False)
    db.delete(s)
    m = db.query(PricingModel).get(model_id)
    if m:
        _bump(m)
        _audit(db, m.id, m.version, "delete", "scenario", scenario_id, user_id=user.id)
    db.commit()
    return {"deleted": scenario_id}


# ───────────────────────── Scenario overrides ───────────────────────

@router.get("/scenarios/{scenario_id}/overrides", response_model=List[ScenarioOverrideOut])
def list_scenario_overrides(
    scenario_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    s = db.query(PricingScenario).get(scenario_id)
    if not s:
        raise HTTPException(404, "Scenario not found")
    rows = db.query(PricingScenarioOverride).filter(
        PricingScenarioOverride.scenario_id == scenario_id
    ).all()
    return [
        ScenarioOverrideOut(
            id=r.id, scenario_id=r.scenario_id, line_item_id=r.line_item_id,
            field=r.field, year_idx=r.year_idx,
            value_numeric=r.value_numeric, value_bool=r.value_bool,
        ) for r in rows
    ]


@router.put("/scenarios/{scenario_id}/overrides", response_model=List[ScenarioOverrideOut])
def upsert_scenario_overrides(
    scenario_id: int,
    payload: List[ScenarioOverrideIn],
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Replace ALL overrides for this scenario with the provided set."""
    s = db.query(PricingScenario).get(scenario_id)
    if not s:
        raise HTTPException(404, "Scenario not found")
    # Validate each override's line belongs to this model
    line_ids = {p.line_item_id for p in payload}
    if line_ids:
        lines = db.query(PricingLineItem).filter(PricingLineItem.id.in_(line_ids)).all()
        line_map = {l.id: l for l in lines}
        for lid in line_ids:
            line = line_map.get(lid)
            if not line:
                raise HTTPException(400, f"Line item {lid} not found")
            cat = db.query(PricingCategory).get(line.category_id)
            if not cat or cat.pricing_model_id != s.pricing_model_id:
                raise HTTPException(400, f"Line item {lid} does not belong to this scenario's model")
    db.query(PricingScenarioOverride).filter(
        PricingScenarioOverride.scenario_id == scenario_id
    ).delete(synchronize_session=False)
    created = []
    for p in payload:
        ov = PricingScenarioOverride(
            scenario_id=scenario_id,
            line_item_id=p.line_item_id,
            field=p.field,
            year_idx=p.year_idx,
            value_numeric=p.value_numeric,
            value_bool=p.value_bool,
        )
        db.add(ov)
        created.append(ov)
    m = db.query(PricingModel).get(s.pricing_model_id)
    if m:
        _bump(m)
        _audit(db, m.id, m.version, "update", "override", scenario_id, user_id=user.id,
               new_value=[p.model_dump() for p in payload])
    db.commit()
    for ov in created:
        db.refresh(ov)
    return [
        ScenarioOverrideOut(
            id=ov.id, scenario_id=ov.scenario_id, line_item_id=ov.line_item_id,
            field=ov.field, year_idx=ov.year_idx,
            value_numeric=ov.value_numeric, value_bool=ov.value_bool,
        ) for ov in created
    ]


# ───────────────────────── Audit trail ──────────────────────────────

@router.get("/models/{model_id}/audit-log")
def get_audit_log(
    model_id: int,
    limit: int = 200,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not db.query(PricingModel).get(model_id):
        raise HTTPException(404, "Pricing model not found")
    rows = (
        db.query(PricingAuditLog)
        .filter(PricingAuditLog.pricing_model_id == model_id)
        .order_by(PricingAuditLog.created_at.desc())
        .limit(max(1, min(limit, 1000)))
        .all()
    )
    return [
        {
            "id": r.id,
            "version": r.version,
            "action": r.action,
            "entity_type": r.entity_type,
            "entity_id": r.entity_id,
            "field": r.field,
            "new_value": json.loads(r.new_value) if r.new_value else None,
            "old_value": json.loads(r.old_value) if r.old_value else None,
            "user_id": r.user_id,
            "note": r.note,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in rows
    ]


# ───────────────────────── Categories ───────────────────────────────

@router.post("/models/{model_id}/categories", response_model=CategoryOut)
def create_category(
    model_id: int,
    payload: CategoryIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    m = db.query(PricingModel).get(model_id)
    if not m:
        raise HTTPException(404, "Pricing model not found")
    c = PricingCategory(pricing_model_id=model_id, **payload.model_dump())
    db.add(c)
    _bump(m)
    _audit(db, m.id, m.version, "create", "category", user_id=user.id,
           new_value=payload.model_dump())
    db.commit()
    db.refresh(c)
    return _cat_out(c, [])


@router.put("/categories/{category_id}", response_model=CategoryOut)
def update_category(
    category_id: int,
    payload: CategoryUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    c = db.query(PricingCategory).get(category_id)
    if not c:
        raise HTTPException(404, "Category not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(c, k, v)
    m = db.query(PricingModel).get(c.pricing_model_id)
    if m:
        _bump(m)
        _audit(db, m.id, m.version, "update", "category", c.id, user_id=user.id, new_value=data)
    db.commit()
    db.refresh(c)
    compute_model(db, c.pricing_model_id)
    lines = db.query(PricingLineItem).filter(PricingLineItem.category_id == c.id).all()
    return _cat_out(c, lines)


@router.delete("/categories/{category_id}")
def delete_category(
    category_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    c = db.query(PricingCategory).get(category_id)
    if not c:
        raise HTTPException(404, "Category not found")
    model_id = c.pricing_model_id
    lines = db.query(PricingLineItem).filter(PricingLineItem.category_id == c.id).all()
    line_ids = [l.id for l in lines]
    if line_ids:
        db.query(PricingLineEscalation).filter(PricingLineEscalation.line_item_id.in_(line_ids)).delete(synchronize_session=False)
        db.query(PricingScenarioOverride).filter(PricingScenarioOverride.line_item_id.in_(line_ids)).delete(synchronize_session=False)
    for l in lines:
        db.delete(l)
    db.delete(c)
    m = db.query(PricingModel).get(model_id)
    if m:
        _bump(m)
        _audit(db, m.id, m.version, "delete", "category", category_id, user_id=user.id)
    db.commit()
    compute_model(db, model_id)
    return {"deleted": category_id}


# ───────────────────────── Staffing Plan ────────────────────────────

def _staffing_position_out(pos: StaffingPosition, total_years: int) -> StaffingPositionOut:
    """Serialize a StaffingPosition and compute loaded annual costs."""
    hc_raw = pos.headcount_by_year or "[]"
    try:
        hc = json.loads(hc_raw) if isinstance(hc_raw, str) else list(hc_raw)
    except Exception:
        hc = []
    # Pad/trim to total_years
    while len(hc) < total_years:
        hc.append(0.0)
    hc = hc[:total_years]

    # Base annual cost (before multipliers)
    pay_type = (pos.pay_type or "hourly").lower()
    if pay_type == "hourly":
        hrs = pos.hours_per_year or 1696.0
        rate = pos.hourly_rate or 0.0
        # OT premium: if X% of hours are OT at 1.5x, extra cost = 0.5 * X% * base
        ot_mult = 1.0
        if pos.overtime_eligible and (pos.overtime_pct or 0) > 0:
            ot_mult = 1.0 + 0.5 * (pos.overtime_pct / 100.0)
        base = rate * hrs * ot_mult
    else:  # salaried — OT not applicable for exempt salary
        base = pos.base_salary or 0.0

    # Loaded rate multiplier (stacked)
    loaded_mult = (
        (1 + (pos.fringe_pct or 0) / 100)
        * (1 + (pos.burden_pct or 0) / 100)
        * (1 + (pos.ga_pct or 0) / 100)
        * (1 + (pos.fee_pct or 0) / 100)
    )

    esc = (pos.escalation_pct or 0.0) / 100.0
    annual_costs = []
    for yr_idx, headcount in enumerate(hc):
        escalated_base = base * ((1 + esc) ** yr_idx)
        annual_costs.append(round(headcount * escalated_base * loaded_mult, 2))

    out = StaffingPositionOut(
        id=pos.id,
        pricing_model_id=pos.pricing_model_id,
        role_title=pos.role_title,
        classification=pos.classification or "salaried_mgmt",
        pay_type=pos.pay_type or "hourly",
        base_salary=pos.base_salary or 0.0,
        hourly_rate=pos.hourly_rate or 0.0,
        hours_per_year=pos.hours_per_year or 1696.0,
        overtime_eligible=bool(pos.overtime_eligible),
        overtime_pct=pos.overtime_pct or 0.0,
        fringe_pct=pos.fringe_pct or 0.0,
        burden_pct=pos.burden_pct or 0.0,
        ga_pct=pos.ga_pct or 0.0,
        fee_pct=pos.fee_pct or 0.0,
        headcount_by_year=hc,
        escalation_pct=pos.escalation_pct or 0.0,
        engagement_type=pos.engagement_type or "fte",
        allocation_basis=pos.allocation_basis or "SHARED",
        notes=pos.notes,
        sort_order=pos.sort_order or 0,
        included=bool(pos.included),
        user_defined=bool(pos.user_defined),
        annual_costs=annual_costs,
        total_cost=sum(annual_costs),
    )
    return out


@router.get("/models/{model_id}/staffing", response_model=List[StaffingPositionOut])
def list_staffing_positions(
    model_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    m = db.query(PricingModel).get(model_id)
    if not m:
        raise HTTPException(404, "Model not found")
    total_years = (m.base_years or 6) + (m.extension_years or 4)
    positions = (
        db.query(StaffingPosition)
        .filter(StaffingPosition.pricing_model_id == model_id)
        .order_by(StaffingPosition.sort_order, StaffingPosition.id)
        .all()
    )
    return [_staffing_position_out(p, total_years) for p in positions]


@router.post("/models/{model_id}/staffing", response_model=StaffingPositionOut)
def create_staffing_position(
    model_id: int,
    body: StaffingPositionIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    m = db.query(PricingModel).get(model_id)
    if not m:
        raise HTTPException(404, "Model not found")
    total_years = (m.base_years or 6) + (m.extension_years or 4)
    hc = body.headcount_by_year or []
    while len(hc) < total_years:
        hc.append(0.0)
    pos = StaffingPosition(
        pricing_model_id=model_id,
        role_title=body.role_title,
        classification=body.classification,
        pay_type=body.pay_type,
        base_salary=body.base_salary,
        hourly_rate=body.hourly_rate,
        hours_per_year=body.hours_per_year,
        overtime_eligible=body.overtime_eligible,
        overtime_pct=body.overtime_pct,
        fringe_pct=body.fringe_pct,
        burden_pct=body.burden_pct,
        ga_pct=body.ga_pct,
        fee_pct=body.fee_pct,
        headcount_by_year=json.dumps(hc[:total_years]),
        escalation_pct=body.escalation_pct,
        engagement_type=body.engagement_type,
        allocation_basis=body.allocation_basis,
        notes=body.notes,
        sort_order=body.sort_order,
        included=body.included,
        user_defined=True,
    )
    db.add(pos)
    db.commit()
    db.refresh(pos)
    return _staffing_position_out(pos, total_years)


@router.put("/staffing/{position_id}", response_model=StaffingPositionOut)
def update_staffing_position(
    position_id: int,
    body: StaffingPositionUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    pos = db.query(StaffingPosition).get(position_id)
    if not pos:
        raise HTTPException(404, "Position not found")
    m = db.query(PricingModel).get(pos.pricing_model_id)
    total_years = (m.base_years or 6) + (m.extension_years or 4) if m else 10
    for field, val in body.model_dump(exclude_unset=True).items():
        if field == "headcount_by_year":
            hc = list(val or [])
            while len(hc) < total_years:
                hc.append(0.0)
            setattr(pos, "headcount_by_year", json.dumps(hc[:total_years]))
        else:
            setattr(pos, field, val)
    pos.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(pos)
    return _staffing_position_out(pos, total_years)


@router.delete("/staffing/{position_id}")
def delete_staffing_position(
    position_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    pos = db.query(StaffingPosition).get(position_id)
    if not pos:
        raise HTTPException(404, "Position not found")
    db.delete(pos)
    db.commit()
    return {"deleted": position_id}


# ───────────────────────── Line items ───────────────────────────────

@router.post("/categories/{category_id}/line-items", response_model=LineItemOut)
def create_line_item(
    category_id: int,
    payload: LineItemIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    cat = db.query(PricingCategory).get(category_id)
    if not cat:
        raise HTTPException(404, "Category not found")
    data = payload.model_dump(exclude={"escalations"})
    line = PricingLineItem(category_id=category_id, **data)
    db.add(line)
    db.commit()
    db.refresh(line)
    if payload.escalations:
        for esc in payload.escalations:
            db.add(PricingLineEscalation(
                line_item_id=line.id,
                year_idx=esc.year_idx,
                cpi_pct=esc.cpi_pct,
                override_amount=esc.override_amount,
            ))
    m = db.query(PricingModel).get(cat.pricing_model_id)
    if m:
        _bump(m)
        _audit(db, m.id, m.version, "create", "line_item", line.id, user_id=user.id,
               new_value=data)
    db.commit()
    compute_model(db, cat.pricing_model_id)
    line.escalations = db.query(PricingLineEscalation).filter(PricingLineEscalation.line_item_id == line.id).all()
    return _line_out(line)


@router.put("/line-items/{line_id}", response_model=LineItemOut)
def update_line_item(
    line_id: int,
    payload: LineItemUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    line = db.query(PricingLineItem).get(line_id)
    if not line:
        raise HTTPException(404, "Line item not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(line, k, v)
    m = _model_of_line(db, line_id)
    if m:
        _bump(m)
        _audit(db, m.id, m.version, "update", "line_item", line_id, user_id=user.id, new_value=data)
    db.commit()
    db.refresh(line)
    if m:
        compute_model(db, m.id)
    line.escalations = db.query(PricingLineEscalation).filter(PricingLineEscalation.line_item_id == line.id).all()
    return _line_out(line)


@router.delete("/line-items/{line_id}")
def delete_line_item(
    line_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    line = db.query(PricingLineItem).get(line_id)
    if not line:
        raise HTTPException(404, "Line item not found")
    m = _model_of_line(db, line_id)
    db.query(PricingLineEscalation).filter(PricingLineEscalation.line_item_id == line_id).delete(synchronize_session=False)
    db.query(PricingScenarioOverride).filter(PricingScenarioOverride.line_item_id == line_id).delete(synchronize_session=False)
    db.delete(line)
    if m:
        _bump(m)
        _audit(db, m.id, m.version, "delete", "line_item", line_id, user_id=user.id)
    db.commit()
    if m:
        compute_model(db, m.id)
    return {"deleted": line_id}


@router.put("/line-items/{line_id}/escalations", response_model=LineItemOut)
def upsert_line_escalations(
    line_id: int,
    payload: List[EscalationIn],
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    line = db.query(PricingLineItem).get(line_id)
    if not line:
        raise HTTPException(404, "Line item not found")
    # Reject duplicate year entries in the incoming payload
    years = [e.year_idx for e in payload]
    if len(set(years)) != len(years):
        raise HTTPException(400, "Duplicate year_idx in escalations payload.")
    m = _model_of_line(db, line_id)
    db.query(PricingLineEscalation).filter(PricingLineEscalation.line_item_id == line_id).delete(synchronize_session=False)
    for esc in payload:
        db.add(PricingLineEscalation(
            line_item_id=line_id,
            year_idx=esc.year_idx,
            cpi_pct=esc.cpi_pct,
            override_amount=esc.override_amount,
        ))
    if m:
        _bump(m)
        _audit(db, m.id, m.version, "update", "escalation", line_id, user_id=user.id,
               new_value=[e.model_dump() for e in payload])
    db.commit()
    if m:
        compute_model(db, m.id)
    line.escalations = db.query(PricingLineEscalation).filter(PricingLineEscalation.line_item_id == line_id).all()
    return _line_out(line)


# ───────────────────────── Competitor historical bids ───────────────

@router.get("/competitor-bids", response_model=List[HistoricalBidOut])
def list_historical_bids(
    competitor_id: Optional[int] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(CompetitorHistoricalBid)
    if competitor_id is not None:
        q = q.filter(CompetitorHistoricalBid.competitor_id == competitor_id)
    bids = q.order_by(CompetitorHistoricalBid.created_at.desc()).all()
    out = []
    for b in bids:
        lines = db.query(CompetitorBidLineItem).filter(CompetitorBidLineItem.historical_bid_id == b.id).all()
        strats = db.query(CompetitorBidStrategy).filter(CompetitorBidStrategy.historical_bid_id == b.id).all()
        out.append(_bid_out(b, lines, strats))
    return out


@router.post("/competitor-bids", response_model=HistoricalBidOut)
def create_historical_bid(
    payload: HistoricalBidIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    b = CompetitorHistoricalBid(**payload.model_dump(), created_by=user.id)
    db.add(b)
    db.commit()
    db.refresh(b)
    return _bid_out(b, [], [])


@router.get("/competitor-bids/{bid_id}", response_model=HistoricalBidOut)
def get_historical_bid(
    bid_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    b = db.query(CompetitorHistoricalBid).get(bid_id)
    if not b:
        raise HTTPException(404, "Historical bid not found")
    lines = db.query(CompetitorBidLineItem).filter(CompetitorBidLineItem.historical_bid_id == b.id).all()
    strats = db.query(CompetitorBidStrategy).filter(CompetitorBidStrategy.historical_bid_id == b.id).all()
    return _bid_out(b, lines, strats)


@router.put("/competitor-bids/{bid_id}", response_model=HistoricalBidOut)
def update_historical_bid(
    bid_id: int,
    payload: HistoricalBidUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    b = db.query(CompetitorHistoricalBid).get(bid_id)
    if not b:
        raise HTTPException(404, "Historical bid not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(b, k, v)
    db.commit()
    db.refresh(b)
    lines = db.query(CompetitorBidLineItem).filter(CompetitorBidLineItem.historical_bid_id == b.id).all()
    strats = db.query(CompetitorBidStrategy).filter(CompetitorBidStrategy.historical_bid_id == b.id).all()
    return _bid_out(b, lines, strats)


@router.delete("/competitor-bids/{bid_id}")
def delete_historical_bid(
    bid_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_admin_user)
):
    b = db.query(CompetitorHistoricalBid).get(bid_id)
    if not b:
        raise HTTPException(404, "Historical bid not found")
    db.query(CompetitorBidLineItem).filter(CompetitorBidLineItem.historical_bid_id == bid_id).delete(synchronize_session=False)
    db.query(CompetitorBidStrategy).filter(CompetitorBidStrategy.historical_bid_id == bid_id).delete(synchronize_session=False)
    db.delete(b)
    db.commit()
    return {"deleted": bid_id}


@router.post("/competitor-bids/{bid_id}/line-items", response_model=BidLineItemOut)
def add_bid_line_item(
    bid_id: int,
    payload: BidLineItemIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not db.query(CompetitorHistoricalBid).get(bid_id):
        raise HTTPException(404, "Historical bid not found")
    data = payload.model_dump()
    annual = data.pop("annual_values", None)
    line = CompetitorBidLineItem(
        historical_bid_id=bid_id,
        annual_values_json=_dump_list(annual),
        **data,
    )
    db.add(line)
    db.commit()
    db.refresh(line)
    return _bid_line_out(line)


@router.post("/competitor-bids/{bid_id}/strategies", response_model=BidStrategyOut)
def add_bid_strategy(
    bid_id: int,
    payload: BidStrategyIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    bid = db.query(CompetitorHistoricalBid).get(bid_id)
    if not bid:
        raise HTTPException(404, "Historical bid not found")
    s = CompetitorBidStrategy(
        historical_bid_id=bid_id,
        competitor_id=bid.competitor_id,
        **payload.model_dump(),
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return _strategy_out(s)

# ═══════════════════════════════════════════════════════════════════
# Pricing Coverage Analysis — map RFP requirements to pricing line items
# ═══════════════════════════════════════════════════════════════════

class CoverageRunIn(BaseModel):
    pricing_model_id: int
    proposal_id: Optional[int] = None
    document_ids: Optional[List[int]] = None
    label: Optional[str] = None
    max_requirements: Optional[int] = Field(
        default=None,
        description="Cap for smoke-testing. Leave null to scan all.",
    )
    requirement_classes: Optional[List[str]] = Field(
        default=None,
        description="Restrict to these requirement_class values. "
                    "None (omit) = use service default (obligation/spec/deadline/unclassified). "
                    "Empty list = scan every requirement.",
    )


class CoverageReviewIn(BaseModel):
    review_notes: Optional[str] = None
    target_category_id: Optional[int] = Field(
        default=None,
        description="For accept: force the new line into this existing category id. "
                    "If omitted, proposed_category_name + proposed_tab are used "
                    "(creating the category if needed).",
    )


def _coverage_run_to_dict(r: PricingCoverageRun) -> Dict[str, Any]:
    return {
        "id": r.id,
        "pricing_model_id": r.pricing_model_id,
        "proposal_id": r.proposal_id,
        "label": r.label,
        "status": r.status,
        "requirements_scanned": r.requirements_scanned or 0,
        "cost_bearing_count": r.cost_bearing_count or 0,
        "covered_count": r.covered_count or 0,
        "partial_count": r.partial_count or 0,
        "gap_count": r.gap_count or 0,
        "error_message": r.error_message,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
    }


def _coverage_sugg_to_dict(s: PricingCoverageSuggestion, req: Optional[RfpRequirement] = None) -> Dict[str, Any]:
    try:
        matched_ids = json.loads(s.matched_line_item_ids_json) if s.matched_line_item_ids_json else []
    except Exception:
        matched_ids = []
    out = {
        "id": s.id,
        "run_id": s.run_id,
        "pricing_model_id": s.pricing_model_id,
        "requirement_id": s.requirement_id,
        "coverage_status": s.coverage_status,
        "matched_line_item_ids": matched_ids,
        "proposed_tab": s.proposed_tab,
        "proposed_category_name": s.proposed_category_name,
        "proposed_line_name": s.proposed_line_name,
        "proposed_description": s.proposed_description,
        "proposed_unit": s.proposed_unit,
        "proposed_allocation_basis": s.proposed_allocation_basis,
        "proposed_qty": s.proposed_qty,
        "rationale": s.rationale,
        "confidence": s.confidence,
        "severity": s.severity,
        "status": s.status,
        "review_notes": s.review_notes,
        "accepted_line_item_id": s.accepted_line_item_id,
        "reviewed_by": s.reviewed_by,
        "reviewed_at": s.reviewed_at.isoformat() if s.reviewed_at else None,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }
    if req is not None:
        out["requirement"] = {
            "id": req.id,
            "section_id": req.section_id,
            "category": req.category,
            "priority": req.priority,
            "title": req.title,
            "description": req.description,
            "source_text": req.source_text,
            "source_page": req.source_page,
            "document_id": req.document_id,
        }
    return out


def _run_coverage_bg(body_dict: Dict[str, Any], user_id: Optional[int]):
    """Background worker — creates its own Session."""
    db = SessionLocal()
    try:
        _pc_run(
            db,
            pricing_model_id=body_dict["pricing_model_id"],
            proposal_id=body_dict.get("proposal_id"),
            document_ids=body_dict.get("document_ids"),
            label=body_dict.get("label"),
            created_by_user_id=user_id,
            max_requirements=body_dict.get("max_requirements"),
            requirement_classes=body_dict.get("requirement_classes"),
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception(f"Background coverage run failed: {e}")
    finally:
        db.close()


@router.post("/coverage/run")
def start_coverage_run(
    body: CoverageRunIn,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Kick off a coverage analysis run in the background. Poll
    GET /coverage/runs/{id} for status, then GET /coverage/suggestions?run_id=..
    for the list of suggestions when status is ``completed``."""
    if not (body.proposal_id or body.document_ids):
        raise HTTPException(400, "Provide proposal_id or document_ids")
    model = db.query(PricingModel).get(body.pricing_model_id)
    if not model:
        raise HTTPException(404, "Pricing model not found")

    background.add_task(_run_coverage_bg, body.model_dump(), getattr(user, "id", None))
    return {"status": "queued", "message": "Coverage analysis running in background."}


@router.get("/coverage/runs")
def list_coverage_runs(
    pricing_model_id: Optional[int] = Query(None),
    proposal_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    q = db.query(PricingCoverageRun)
    if pricing_model_id is not None:
        q = q.filter(PricingCoverageRun.pricing_model_id == pricing_model_id)
    if proposal_id is not None:
        q = q.filter(PricingCoverageRun.proposal_id == proposal_id)
    rows = q.order_by(PricingCoverageRun.id.desc()).all()
    return [_coverage_run_to_dict(r) for r in rows]


@router.get("/coverage/runs/{run_id}")
def get_coverage_run(
    run_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    r = db.query(PricingCoverageRun).get(run_id)
    if not r:
        raise HTTPException(404, "Coverage run not found")
    return _coverage_run_to_dict(r)


@router.get("/coverage/suggestions")
def list_coverage_suggestions(
    run_id: Optional[int] = Query(None),
    pricing_model_id: Optional[int] = Query(None),
    coverage_status: Optional[str] = Query(None, description="covered | partial | gap"),
    status: Optional[str] = Query(None, description="pending | accepted | rejected | applied"),
    severity: Optional[str] = Query(None),
    include_requirement: bool = Query(True),
    limit: int = Query(500),
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    q = db.query(PricingCoverageSuggestion)
    if run_id is not None:
        q = q.filter(PricingCoverageSuggestion.run_id == run_id)
    if pricing_model_id is not None:
        q = q.filter(PricingCoverageSuggestion.pricing_model_id == pricing_model_id)
    if coverage_status:
        q = q.filter(PricingCoverageSuggestion.coverage_status == coverage_status)
    if status:
        q = q.filter(PricingCoverageSuggestion.status == status)
    if severity:
        q = q.filter(PricingCoverageSuggestion.severity == severity)
    q = q.order_by(
        PricingCoverageSuggestion.coverage_status.desc(),  # gap > partial > covered alphabetically
        PricingCoverageSuggestion.id.asc(),
    ).limit(limit)
    rows = q.all()

    reqs_by_id: Dict[int, RfpRequirement] = {}
    if include_requirement and rows:
        ids = [s.requirement_id for s in rows if s.requirement_id]
        for r in db.query(RfpRequirement).filter(RfpRequirement.id.in_(ids)).all():
            reqs_by_id[r.id] = r

    return [_coverage_sugg_to_dict(s, reqs_by_id.get(s.requirement_id)) for s in rows]


@router.post("/coverage/suggestions/{suggestion_id}/accept")
def accept_coverage_suggestion(
    suggestion_id: int,
    body: CoverageReviewIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        new_line_id = _pc_accept(
            db,
            suggestion_id=suggestion_id,
            reviewer_user_id=getattr(user, "id", None),
            review_notes=body.review_notes,
            target_category_id=body.target_category_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    sugg = db.query(PricingCoverageSuggestion).get(suggestion_id)
    return {
        "ok": True,
        "new_line_item_id": new_line_id,
        "suggestion": _coverage_sugg_to_dict(sugg) if sugg else None,
    }


@router.post("/coverage/suggestions/{suggestion_id}/reject")
def reject_coverage_suggestion(
    suggestion_id: int,
    body: CoverageReviewIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        _pc_reject(
            db,
            suggestion_id=suggestion_id,
            reviewer_user_id=getattr(user, "id", None),
            review_notes=body.review_notes,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    sugg = db.query(PricingCoverageSuggestion).get(suggestion_id)
    return {"ok": True, "suggestion": _coverage_sugg_to_dict(sugg) if sugg else None}