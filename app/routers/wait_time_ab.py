"""Wait-time A/B Test API.

Endpoints (all under /api/wait-time-ab):

  Imports:
    POST   /imports                       — multipart upload of an .xlsx file
    GET    /imports                       — list imports
    GET    /imports/{id}                  — detail (header + small sample)
    DELETE /imports/{id}                  — delete (cascades observations)

  LD rules:
    GET    /rules?proposal_id=
    POST   /rules                         — create
    GET    /rules/{id}
    PUT    /rules/{id}
    DELETE /rules/{id}

  A/B comparison:
    POST   /compare                       — { import_id, old_rule_id, new_rule_id, name }
                                            persists a WaitTimeAbRun and returns full breakdown
    GET    /runs?proposal_id=
    GET    /runs/{id}
    DELETE /runs/{id}
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..models import (
    User,
    WaitTimeAbRun,
    WaitTimeImport,
    WaitTimeLdRule,
    WaitTimeObservation,
)
from ..services.wait_time_ab import (
    VALID_MODES,
    compare_rules,
    load_observations,
    persist_import,
    rule_to_dict,
)
from ..services.wait_time_ab_export import export_run_to_xlsx

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Imports ─────────────────────────────────────────────────────────


def _import_to_dict(imp: WaitTimeImport) -> Dict[str, Any]:
    return {
        "id": imp.id,
        "proposal_id": imp.proposal_id,
        "name": imp.name,
        "description": imp.description,
        "source_filename": imp.source_filename,
        "metric_name_filter": imp.metric_name_filter,
        "row_count": imp.row_count,
        "station_count": imp.station_count,
        "date_min": imp.date_min.strftime("%Y-%m-%d") if imp.date_min else None,
        "date_max": imp.date_max.strftime("%Y-%m-%d") if imp.date_max else None,
        "uploaded_by": imp.uploaded_by,
        "created_at": imp.created_at.isoformat() if imp.created_at else None,
    }


@router.post("/imports")
async def upload_import(
    file: UploadFile = File(...),
    name: str = Form(...),
    description: Optional[str] = Form(None),
    proposal_id: Optional[int] = Form(None),
    metric_name_filter: Optional[str] = Form("Facility Average Wait Time"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    fname = (file.filename or "").lower()
    if not fname.endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "File must be an .xlsx workbook.")
    body = await file.read()
    if not body:
        raise HTTPException(400, "Empty file.")
    if len(body) > 50 * 1024 * 1024:
        raise HTTPException(400, "File too large (max 50 MB).")

    summary = persist_import(
        db,
        file_bytes=body,
        name=name,
        description=description,
        proposal_id=proposal_id,
        metric_name_filter=metric_name_filter or None,
        source_filename=file.filename,
        uploaded_by=user.id,
    )
    if isinstance(summary, dict) and summary.get("error"):
        raise HTTPException(400, summary["error"])
    return summary


@router.get("/imports")
def list_imports(
    proposal_id: Optional[int] = None,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    q = db.query(WaitTimeImport)
    if proposal_id is not None:
        q = q.filter(WaitTimeImport.proposal_id == proposal_id)
    rows = q.order_by(WaitTimeImport.created_at.desc()).all()
    return {"imports": [_import_to_dict(r) for r in rows]}


@router.get("/imports/{import_id}")
def get_import(
    import_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    imp = db.query(WaitTimeImport).get(import_id)
    if not imp:
        raise HTTPException(404, "Import not found")
    sample = (db.query(WaitTimeObservation)
              .filter(WaitTimeObservation.import_id == import_id)
              .limit(20).all())
    return {
        **_import_to_dict(imp),
        "sample": [{
            "station_id": s.station_id,
            "station_name": s.station_name,
            "test_date": s.test_date.strftime("%Y-%m-%d") if s.test_date else None,
            "metric_name": s.metric_name,
            "hourly_values": json.loads(s.hourly_values) if s.hourly_values else [None]*14,
        } for s in sample],
    }


@router.delete("/imports/{import_id}")
def delete_import(
    import_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    imp = db.query(WaitTimeImport).get(import_id)
    if not imp:
        raise HTTPException(404, "Import not found")
    # ON DELETE CASCADE handles observations; explicitly clear any A/B runs
    # referencing this import so the FK doesn't trip.
    db.query(WaitTimeAbRun).filter(WaitTimeAbRun.import_id == import_id).delete(
        synchronize_session=False)
    db.query(WaitTimeObservation).filter(
        WaitTimeObservation.import_id == import_id).delete(synchronize_session=False)
    db.delete(imp)
    db.commit()
    return {"deleted": import_id}


# ── LD rules ────────────────────────────────────────────────────────


class LdRuleIn(BaseModel):
    proposal_id: Optional[int] = None
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    is_baseline: bool = False
    threshold_minutes: float = Field(..., ge=0)
    mode: str = Field(...)
    dollars_per_unit: float = Field(0.0, ge=0)
    daily_cap_usd: Optional[float] = Field(None, ge=0)
    monthly_cap_usd: Optional[float] = Field(None, ge=0)
    grace_period_minutes: float = Field(0.0, ge=0)
    exclude_hours_csv: Optional[str] = None
    count_null_hours_as_breach: bool = False
    # Allow tiers_json to be either a list (tiered mode) OR a dict (the
    # nj_new_t1628 mode uses it as a parameter bag for O-31 customization).
    tiers_json: Optional[Any] = None
    notes: Optional[str] = None
    excluded_facilities_csv: Optional[str] = None
    monthly_grace_days: int = Field(0, ge=0, le=31)
    # Monthly LD parameters (independent of mode). When monthly_enabled is
    # true, the engine computes a per-station-per-month average and applies
    # a flat dollar charge if avg > monthly_threshold_minutes, plus optional
    # 10-min-band increments above monthly_increment_threshold.
    monthly_enabled: bool = False
    monthly_threshold_minutes: Optional[float] = Field(None, ge=0)
    monthly_dollars: Optional[float] = Field(None, ge=0)
    monthly_increment_threshold: Optional[float] = Field(None, ge=0)
    monthly_increment_dollars: Optional[float] = Field(None, ge=0)
    monthly_band_size_minutes: Optional[float] = Field(None, gt=0)
    monthly_scheduled_hours_per_day: Optional[float] = Field(9.0, gt=0, le=24)
    monthly_scheduled_operating_days_override: Optional[int] = Field(None, ge=1, le=31)


class LdRuleOut(LdRuleIn):
    id: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


def _rule_out(r: WaitTimeLdRule) -> Dict[str, Any]:
    tiers = None
    if r.tiers_json:
        try:
            tiers = json.loads(r.tiers_json)
        except (json.JSONDecodeError, TypeError):
            tiers = None
    return {
        "id": r.id,
        "proposal_id": r.proposal_id,
        "name": r.name,
        "description": r.description,
        "is_baseline": bool(r.is_baseline),
        "threshold_minutes": r.threshold_minutes,
        "mode": r.mode,
        "dollars_per_unit": r.dollars_per_unit,
        "daily_cap_usd": r.daily_cap_usd,
        "monthly_cap_usd": r.monthly_cap_usd,
        "grace_period_minutes": r.grace_period_minutes,
        "exclude_hours_csv": r.exclude_hours_csv,
        "count_null_hours_as_breach": bool(r.count_null_hours_as_breach),
        "tiers_json": tiers,
        "notes": r.notes,
        "excluded_facilities_csv": getattr(r, "excluded_facilities_csv", None),
        "monthly_grace_days": getattr(r, "monthly_grace_days", 0) or 0,
        "monthly_enabled": bool(getattr(r, "monthly_enabled", False)),
        "monthly_threshold_minutes": getattr(r, "monthly_threshold_minutes", None),
        "monthly_dollars": getattr(r, "monthly_dollars", None),
        "monthly_increment_threshold": getattr(r, "monthly_increment_threshold", None),
        "monthly_increment_dollars": getattr(r, "monthly_increment_dollars", None),
        "monthly_band_size_minutes": getattr(r, "monthly_band_size_minutes", None),
        "monthly_scheduled_hours_per_day": getattr(r, "monthly_scheduled_hours_per_day", None),
        "monthly_scheduled_operating_days_override": getattr(r, "monthly_scheduled_operating_days_override", None),
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


def _validate_rule_payload(payload: LdRuleIn) -> None:
    mode = (payload.mode or "").strip().lower()
    if mode not in VALID_MODES:
        raise HTTPException(400, f"mode must be one of {sorted(VALID_MODES)}")
    if mode == "tiered" and not payload.tiers_json:
        raise HTTPException(400, "tiers_json is required when mode='tiered'")


@router.get("/rules")
def list_rules(
    proposal_id: Optional[int] = None,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    q = db.query(WaitTimeLdRule)
    if proposal_id is not None:
        q = q.filter(WaitTimeLdRule.proposal_id == proposal_id)
    rows = q.order_by(WaitTimeLdRule.is_baseline.desc(),
                       WaitTimeLdRule.created_at.desc()).all()
    return {"rules": [_rule_out(r) for r in rows]}


@router.post("/rules")
def create_rule(
    payload: LdRuleIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _validate_rule_payload(payload)
    rule = WaitTimeLdRule(
        proposal_id=payload.proposal_id,
        name=payload.name,
        description=payload.description,
        is_baseline=payload.is_baseline,
        threshold_minutes=payload.threshold_minutes,
        mode=payload.mode.strip().lower(),
        dollars_per_unit=payload.dollars_per_unit,
        daily_cap_usd=payload.daily_cap_usd,
        monthly_cap_usd=payload.monthly_cap_usd,
        grace_period_minutes=payload.grace_period_minutes,
        exclude_hours_csv=payload.exclude_hours_csv,
        count_null_hours_as_breach=payload.count_null_hours_as_breach,
        tiers_json=(json.dumps(payload.tiers_json) if payload.tiers_json else None),
        notes=payload.notes,
        excluded_facilities_csv=payload.excluded_facilities_csv,
        monthly_grace_days=payload.monthly_grace_days or 0,
        monthly_enabled=payload.monthly_enabled,
        monthly_threshold_minutes=payload.monthly_threshold_minutes,
        monthly_dollars=payload.monthly_dollars,
        monthly_increment_threshold=payload.monthly_increment_threshold,
        monthly_increment_dollars=payload.monthly_increment_dollars,
        monthly_band_size_minutes=payload.monthly_band_size_minutes,
        monthly_scheduled_hours_per_day=payload.monthly_scheduled_hours_per_day,
        monthly_scheduled_operating_days_override=payload.monthly_scheduled_operating_days_override,
        created_by=user.id,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return _rule_out(rule)


@router.get("/rules/{rule_id}")
def get_rule(rule_id: int, db: Session = Depends(get_db),
             _user: User = Depends(get_current_user)):
    r = db.query(WaitTimeLdRule).get(rule_id)
    if not r:
        raise HTTPException(404, "Rule not found")
    return _rule_out(r)


@router.put("/rules/{rule_id}")
def update_rule(
    rule_id: int,
    payload: LdRuleIn,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    _validate_rule_payload(payload)
    r = db.query(WaitTimeLdRule).get(rule_id)
    if not r:
        raise HTTPException(404, "Rule not found")
    r.proposal_id = payload.proposal_id
    r.name = payload.name
    r.description = payload.description
    r.is_baseline = payload.is_baseline
    r.threshold_minutes = payload.threshold_minutes
    r.mode = payload.mode.strip().lower()
    r.dollars_per_unit = payload.dollars_per_unit
    r.daily_cap_usd = payload.daily_cap_usd
    r.monthly_cap_usd = payload.monthly_cap_usd
    r.grace_period_minutes = payload.grace_period_minutes
    r.exclude_hours_csv = payload.exclude_hours_csv
    r.count_null_hours_as_breach = payload.count_null_hours_as_breach
    r.tiers_json = (json.dumps(payload.tiers_json) if payload.tiers_json else None)
    r.notes = payload.notes
    r.excluded_facilities_csv = payload.excluded_facilities_csv
    r.monthly_grace_days = payload.monthly_grace_days or 0
    r.monthly_enabled = payload.monthly_enabled
    r.monthly_threshold_minutes = payload.monthly_threshold_minutes
    r.monthly_dollars = payload.monthly_dollars
    r.monthly_increment_threshold = payload.monthly_increment_threshold
    r.monthly_increment_dollars = payload.monthly_increment_dollars
    r.monthly_band_size_minutes = payload.monthly_band_size_minutes
    r.monthly_scheduled_hours_per_day = payload.monthly_scheduled_hours_per_day
    r.monthly_scheduled_operating_days_override = payload.monthly_scheduled_operating_days_override
    db.commit()
    db.refresh(r)
    return _rule_out(r)


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(get_db),
                _user: User = Depends(get_current_user)):
    r = db.query(WaitTimeLdRule).get(rule_id)
    if not r:
        raise HTTPException(404, "Rule not found")
    # Detect references from runs
    refs = (db.query(WaitTimeAbRun)
            .filter((WaitTimeAbRun.old_rule_id == rule_id) |
                    (WaitTimeAbRun.new_rule_id == rule_id))
            .count())
    if refs:
        raise HTTPException(400,
            f"Rule is used by {refs} A/B run(s). Delete those runs first.")
    db.delete(r)
    db.commit()
    return {"deleted": rule_id}


# ── A/B comparison ──────────────────────────────────────────────────


class CompareIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    import_id: int
    old_rule_id: int
    new_rule_id: int
    proposal_id: Optional[int] = None


def _run_to_dict(run: WaitTimeAbRun, include_breakdown: bool = True) -> Dict[str, Any]:
    out = {
        "id": run.id,
        "proposal_id": run.proposal_id,
        "name": run.name,
        "import_id": run.import_id,
        "old_rule_id": run.old_rule_id,
        "new_rule_id": run.new_rule_id,
        "old_total_usd": run.old_total_usd,
        "new_total_usd": run.new_total_usd,
        "delta_usd": run.delta_usd,
        "breaches_old": run.breaches_old,
        "breaches_new": run.breaches_new,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }
    if include_breakdown and run.breakdown_json:
        try:
            out["breakdown"] = json.loads(run.breakdown_json)
        except (json.JSONDecodeError, TypeError):
            out["breakdown"] = None
    if run.warnings_json:
        try:
            out["warnings"] = json.loads(run.warnings_json)
        except (json.JSONDecodeError, TypeError):
            out["warnings"] = None
    return out


@router.post("/compare")
def run_compare(
    payload: CompareIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    imp = db.query(WaitTimeImport).get(payload.import_id)
    if not imp:
        raise HTTPException(404, "Import not found")
    old_r = db.query(WaitTimeLdRule).get(payload.old_rule_id)
    new_r = db.query(WaitTimeLdRule).get(payload.new_rule_id)
    if not old_r or not new_r:
        raise HTTPException(404, "One or both rules not found")
    if old_r.id == new_r.id:
        raise HTTPException(400, "old_rule_id and new_rule_id must differ.")

    obs = load_observations(db, imp.id)
    if not obs:
        raise HTTPException(400, "Import has no observations.")

    result = compare_rules(obs, rule_to_dict(old_r), rule_to_dict(new_r))
    totals = result["totals"]

    run = WaitTimeAbRun(
        proposal_id=payload.proposal_id,
        name=payload.name,
        import_id=imp.id,
        old_rule_id=old_r.id,
        new_rule_id=new_r.id,
        old_total_usd=totals["old_total_usd"],
        new_total_usd=totals["new_total_usd"],
        delta_usd=totals["delta_usd"],
        breaches_old=totals["breaches_old"],
        breaches_new=totals["breaches_new"],
        breakdown_json=json.dumps({
            "totals": totals,
            "monthly": result["monthly"],
            "per_station": result["per_station"],
            "daily": result["daily"],
        }),
        warnings_json=json.dumps(result["warnings"]) if result["warnings"] else None,
        created_by=user.id,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return _run_to_dict(run, include_breakdown=True)


@router.get("/runs")
def list_runs(
    proposal_id: Optional[int] = None,
    import_id: Optional[int] = None,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    q = db.query(WaitTimeAbRun)
    if proposal_id is not None:
        q = q.filter(WaitTimeAbRun.proposal_id == proposal_id)
    if import_id is not None:
        q = q.filter(WaitTimeAbRun.import_id == import_id)
    rows = q.order_by(WaitTimeAbRun.created_at.desc()).all()
    # Don't include the breakdown payload on list (can be large).
    return {"runs": [_run_to_dict(r, include_breakdown=False) for r in rows]}


@router.get("/runs/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db),
            _user: User = Depends(get_current_user)):
    r = db.query(WaitTimeAbRun).get(run_id)
    if not r:
        raise HTTPException(404, "Run not found")
    return _run_to_dict(r, include_breakdown=True)


@router.delete("/runs/{run_id}")
def delete_run(run_id: int, db: Session = Depends(get_db),
               _user: User = Depends(get_current_user)):
    r = db.query(WaitTimeAbRun).get(run_id)
    if not r:
        raise HTTPException(404, "Run not found")
    db.delete(r)
    db.commit()
    return {"deleted": run_id}


@router.get("/runs/{run_id}/export.xlsx")
def export_run(run_id: int, db: Session = Depends(get_db),
               _user: User = Depends(get_current_user)):
    """Stream a polished .xlsx workbook for a single run.

    Sheets: Summary (KPIs + rule descriptions) · Monthly (rollup + chart)
    · Per-Station (data bars + totals) · Daily (only days with charges)
    · Monthly LDs (per-station-month proxy with threshold heatmap).
    """
    r = db.query(WaitTimeAbRun).get(run_id)
    if not r:
        raise HTTPException(404, "Run not found")
    old_rule = db.query(WaitTimeLdRule).get(r.old_rule_id)
    new_rule = db.query(WaitTimeLdRule).get(r.new_rule_id)
    if not old_rule or not new_rule:
        raise HTTPException(400,
            "One or both rules referenced by this run no longer exist.")

    run_dict = _run_to_dict(r, include_breakdown=True)
    xlsx_bytes = export_run_to_xlsx(
        run=run_dict,
        old_rule=rule_to_dict(old_rule),
        new_rule=rule_to_dict(new_rule),
    )

    # Make the filename safe for Content-Disposition
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_"
                         for c in (r.name or f"run_{r.id}"))[:80]
    filename = f"wait_time_ab_{safe_name}_run{r.id}.xlsx"

    return Response(
        content=xlsx_bytes,
        media_type=("application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
