"""Pricing math stress test.

Builds a fresh, ephemeral pricing model in a sandboxed SQLite database,
seeds it with a randomly-generated mix of 50 line items spanning every
allocation basis + every escalation flavor, and asserts that the
``compute_model`` engine produces totals that exactly match an
independent reference implementation written here in plain Python.

The reference implementation is intentionally written from scratch (not
imported from the router) so that any bug in the production code shows
up as a divergence rather than a shared-bug consensus.

Specifically tested:
  * SHARED allocation split by per-year volume
  * CIF-only / PIF-only allocation
  * CAPITAL_AMORT (capex spread evenly, no CPI)
  * Hourly bill-rate buildup (burden + G&A + fee stack only when unit='hourly')
  * Compounded CPI escalation per-year
  * Per-year override_amount entirely replacing the value
  * Margin sensitivity (revenue = cost / (1 - margin/100))
  * Adding a new line item increments cached totals correctly
  * Changing margin without other edits flows through to KPIs
"""
from __future__ import annotations

import os
import random
import tempfile
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Tuple

import pytest


def _q(v: float) -> float:
    """2-decimal money round, matching the production _q_money."""
    return float(Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# ── Reference implementation (independent of production code) ──────


def _ref_per_year(line, total_years: int) -> List[float]:
    """Return per-year cost for a line, computed from first principles.

    line is a dict of all the relevant fields plus 'escs' (list of dicts
    {year_idx, cpi_pct, override_amount}).
    """
    if not line.get("included", True):
        return [0.0] * total_years
    red_factor = 1.0 - (line.get("reduction_pct") or 0.0) / 100.0
    fringe_factor = 1.0 + (line.get("fringe_pct") or 0.0) / 100.0
    base = (line.get("future_cost") or 0.0) * red_factor * fringe_factor
    if (line.get("unit") or "").lower() == "hourly":
        base *= 1.0 + (line.get("burden_pct") or 0.0) / 100.0
        base *= 1.0 + (line.get("ga_pct") or 0.0) / 100.0
        base *= 1.0 + (line.get("fee_pct") or 0.0) / 100.0
    qty = line.get("qty") or 0.0
    if (line.get("allocation_basis") or "SHARED").upper() == "CAPITAL_AMORT":
        total_cap = base * qty
        return [total_cap / total_years if total_years else 0.0] * total_years

    # Production semantics (matches app/routers/pricing.py::_compound):
    # year 1 is the contract base year — no escalation applied.
    # The CPI value stored at year_idx=N applies as the escalator from
    # year N to year N+1. So year 2 = base * (1 + cpi[year=1]), etc.
    escs = {e["year_idx"]: e for e in (line.get("escs") or [])}
    out: List[float] = []
    for y in range(1, total_years + 1):
        ov = escs.get(y, {}).get("override_amount")
        if ov is not None:
            out.append(float(ov))
            continue
        # Year 1 = base, no compounding
        if y == 1:
            out.append(base * qty)
            continue
        # Year N>=2: compound the CPIs of years 1..(N-1)
        cumulative = 1.0
        for src_year in range(1, y):
            cpi_pct = escs.get(src_year, {}).get("cpi_pct") or 0.0
            cumulative *= (1.0 + cpi_pct / 100.0)
        out.append(base * cumulative * qty)
    return out


def _ref_compute(model: dict, lines: List[dict]) -> dict:
    """Independent reference of the compute engine."""
    total_years = model["base_years"] + model["extension_years"]
    cif_v = (model["cif_volumes"] + [0.0] * total_years)[:total_years]
    pif_v = (model["pif_volumes"] + [0.0] * total_years)[:total_years]

    cif_per_year = [0.0] * total_years
    pif_per_year = [0.0] * total_years
    for line in lines:
        per_year = _ref_per_year(line, total_years)
        basis = (line["allocation_basis"] or "SHARED").upper()
        for y_idx, yearly in enumerate(per_year):
            if basis == "CIF":
                cif_per_year[y_idx] += yearly
            elif basis == "PIF":
                pif_per_year[y_idx] += yearly
            else:
                cv = cif_v[y_idx] if y_idx < len(cif_v) else 0.0
                pv = pif_v[y_idx] if y_idx < len(pif_v) else 0.0
                denom = cv + pv
                if denom > 0:
                    cif_per_year[y_idx] += yearly * (cv / denom)
                    pif_per_year[y_idx] += yearly * (pv / denom)
                else:
                    cif_per_year[y_idx] += yearly * 0.5
                    pif_per_year[y_idx] += yearly * 0.5

    cif_cost = sum(cif_per_year)
    pif_cost = sum(pif_per_year)

    def rev_from_cost(cost: float, margin_pct: float) -> float:
        m = max(min((margin_pct or 0.0) / 100.0, 0.9999), -0.9999)
        return cost / (1.0 - m)

    cif_rev = rev_from_cost(cif_cost, model["cif_margin_pct"])
    pif_rev = rev_from_cost(pif_cost, model["pif_margin_pct"])
    return {
        "cif_cost": _q(cif_cost),
        "pif_cost": _q(pif_cost),
        "total_cost": _q(cif_cost + pif_cost),
        "cif_revenue": _q(cif_rev),
        "pif_revenue": _q(pif_rev),
        "total_revenue": _q(cif_rev + pif_rev),
        "cif_gp": _q(cif_rev - cif_cost),
        "pif_gp": _q(pif_rev - pif_cost),
        "total_gp": _q((cif_rev + pif_rev) - (cif_cost + pif_cost)),
    }


# ── Production-side fixture: ephemeral SQLite + ORM session ─────────


@pytest.fixture()
def db():
    """Spin up an in-memory ephemeral DB so we never touch live rfp.db."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp.name}"

    # Defer imports until DATABASE_URL is set so the engine binds to tmp.
    from app.database import Base, engine, SessionLocal
    Base.metadata.create_all(bind=engine)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


# ── Helpers to build fixtures via the ORM ───────────────────────────


def _make_model_with_lines(db, total_years: int, n_lines: int, seed: int):
    from app.models import (
        PricingCategory, PricingLineEscalation, PricingLineItem, PricingModel,
    )
    rng = random.Random(seed)

    base_years = max(1, total_years // 2)
    extension_years = total_years - base_years

    cif_volumes = [round(rng.uniform(800_000, 1_500_000), 0) for _ in range(total_years)]
    pif_volumes = [round(rng.uniform(400_000, 900_000), 0) for _ in range(total_years)]

    import json as _json
    m = PricingModel(
        name=f"stress test seed={seed}",
        base_years=base_years,
        extension_years=extension_years,
        cif_volumes_json=_json.dumps(cif_volumes),
        pif_volumes_json=_json.dumps(pif_volumes),
        cif_margin_pct=12.5,
        pif_margin_pct=18.0,
        target_contract_value=0.0,
        version=1,
    )
    db.add(m)
    db.commit()
    db.refresh(m)

    cat = PricingCategory(pricing_model_id=m.id, name="Stress Cat", sort_order=1)
    db.add(cat)
    db.commit()
    db.refresh(cat)

    bases = ["SHARED", "CIF", "PIF", "CAPITAL_AMORT"]
    units = ["each", "each", "each", "hourly", "monthly"]

    ref_lines: List[dict] = []
    for i in range(n_lines):
        basis = rng.choice(bases)
        unit = rng.choice(units)
        line = PricingLineItem(
            category_id=cat.id,
            name=f"Line {i+1}",
            allocation_basis=basis,
            included=True if rng.random() > 0.05 else False,
            current_cost=round(rng.uniform(0, 100_000), 2),
            future_cost=round(rng.uniform(1_000, 250_000), 2),
            reduction_pct=round(rng.uniform(-5, 20), 2),
            fringe_pct=round(rng.uniform(0, 35), 2),
            burden_pct=round(rng.uniform(0, 25), 2) if unit == "hourly" else 0.0,
            ga_pct=round(rng.uniform(0, 15), 2) if unit == "hourly" else 0.0,
            fee_pct=round(rng.uniform(0, 10), 2) if unit == "hourly" else 0.0,
            qty=round(rng.uniform(1, 50), 2),
            unit=unit,
            sort_order=i,
        )
        db.add(line)
        db.commit()
        db.refresh(line)

        # Random per-year escalations + ~10% chance of an absolute override
        escs_ref: List[dict] = []
        for y in range(1, total_years + 1):
            cpi = round(rng.uniform(0, 4.5), 2) if rng.random() > 0.2 else 0.0
            override = (round(rng.uniform(5_000, 50_000), 2)
                        if rng.random() < 0.05 else None)
            esc = PricingLineEscalation(
                line_item_id=line.id,
                year_idx=y, cpi_pct=cpi,
                override_amount=override,
            )
            db.add(esc)
            escs_ref.append({"year_idx": y, "cpi_pct": cpi,
                             "override_amount": override})
        db.commit()

        ref_lines.append({
            "included": line.included,
            "current_cost": line.current_cost,
            "future_cost": line.future_cost,
            "reduction_pct": line.reduction_pct,
            "fringe_pct": line.fringe_pct,
            "burden_pct": line.burden_pct,
            "ga_pct": line.ga_pct,
            "fee_pct": line.fee_pct,
            "qty": line.qty,
            "unit": line.unit,
            "allocation_basis": line.allocation_basis,
            "escs": escs_ref,
        })

    ref_model = {
        "base_years": base_years,
        "extension_years": extension_years,
        "cif_volumes": cif_volumes,
        "pif_volumes": pif_volumes,
        "cif_margin_pct": 12.5,
        "pif_margin_pct": 18.0,
    }
    return m, cat, ref_model, ref_lines


# ── Tests ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("seed", [1, 7, 42, 99, 1234])
def test_compute_matches_reference_at_50_lines(db, seed):
    """For 5 different random seeds, build a 50-line model and verify the
    production compute engine matches the independent reference engine to
    the cent for every roll-up KPI."""
    from app.routers.pricing import compute_model

    m, cat, ref_model, ref_lines = _make_model_with_lines(
        db, total_years=10, n_lines=50, seed=seed,
    )
    compute_model(db, m.id)
    db.refresh(m)

    expected = _ref_compute(ref_model, ref_lines)

    actual = {
        "cif_cost": _q(m.computed_cif_cost or 0.0),
        "pif_cost": _q(m.computed_pif_cost or 0.0),
        "total_cost": _q(m.computed_total_cost or 0.0),
        "cif_revenue": _q(m.computed_cif_revenue or 0.0),
        "pif_revenue": _q(m.computed_pif_revenue or 0.0),
        "total_revenue": _q(m.computed_total_revenue or 0.0),
        "cif_gp": _q(m.computed_cif_gp or 0.0),
        "pif_gp": _q(m.computed_pif_gp or 0.0),
        "total_gp": _q(m.computed_total_gp or 0.0),
    }

    # Tolerance: 1 cent per line (allocation rounding can introduce
    # sub-cent drift that reconciles on the order of cents at 50 lines).
    tol = 50 * 0.01  # = $0.50
    for k in expected:
        diff = abs(expected[k] - actual[k])
        assert diff <= tol, (
            f"[seed={seed}] {k} diverged: expected {expected[k]} got "
            f"{actual[k]} (diff {diff:.4f}, tol {tol:.4f})"
        )


def test_margin_change_flows_through(db):
    """Confirm that updating cif_margin_pct on the model and recomputing
    actually changes computed_cif_revenue. This is the core symptom the
    user reported (margin appearing to reset)."""
    from app.routers.pricing import compute_model

    m, cat, ref_model, ref_lines = _make_model_with_lines(
        db, total_years=6, n_lines=10, seed=11,
    )
    compute_model(db, m.id)
    db.refresh(m)
    rev_at_12_5 = m.computed_cif_revenue

    # Bump margin from 12.5 → 25.0
    m.cif_margin_pct = 25.0
    db.commit()
    compute_model(db, m.id)
    db.refresh(m)
    rev_at_25 = m.computed_cif_revenue

    assert rev_at_25 > rev_at_12_5, (
        f"CIF revenue should increase when margin doubles. "
        f"12.5%={rev_at_12_5}, 25%={rev_at_25}"
    )
    # Also verify it matches the reference at the new margin
    ref_model["cif_margin_pct"] = 25.0
    expected = _ref_compute(ref_model, ref_lines)
    assert abs(rev_at_25 - expected["cif_revenue"]) <= 0.50


def test_adding_a_line_increases_cost(db):
    """Confirm that creating a new line via the same code path the UI
    uses bumps the cached total_cost correctly. This is the 'new
    additions inherit the same calcs' guarantee."""
    from app.models import PricingLineItem, PricingLineEscalation
    from app.routers.pricing import compute_model

    m, cat, ref_model, ref_lines = _make_model_with_lines(
        db, total_years=8, n_lines=20, seed=77,
    )
    compute_model(db, m.id)
    db.refresh(m)
    cost_before = m.computed_total_cost

    # Add a brand-new CIF-allocated line that costs ~$60k/year, no CPI,
    # no fringe — easy to predict.
    new_line = PricingLineItem(
        category_id=cat.id,
        name="STRESS New Line",
        allocation_basis="CIF",
        included=True,
        current_cost=0.0, future_cost=10_000.0, reduction_pct=0.0,
        fringe_pct=0.0, burden_pct=0.0, ga_pct=0.0, fee_pct=0.0,
        qty=6.0, unit="each", sort_order=999,
    )
    db.add(new_line)
    db.commit()
    db.refresh(new_line)
    # Zero CPI for every year so the line contributes 6 × $10k × total_years.
    total_years = ref_model["base_years"] + ref_model["extension_years"]
    for y in range(1, total_years + 1):
        db.add(PricingLineEscalation(
            line_item_id=new_line.id, year_idx=y, cpi_pct=0.0,
            override_amount=None,
        ))
    db.commit()

    compute_model(db, m.id)
    db.refresh(m)
    cost_after = m.computed_total_cost

    expected_increase = 10_000.0 * 6.0 * total_years  # $60k * 8 = $480k
    actual_increase = cost_after - cost_before
    assert abs(actual_increase - expected_increase) <= 1.00, (
        f"New line should add ${expected_increase:,.2f} to total cost; "
        f"actually added ${actual_increase:,.2f}"
    )


def test_capital_amort_no_cpi(db):
    """CAPITAL_AMORT lines must be spread evenly with NO CPI compounding.
    If the engine accidentally applies CPI to them, this catches it."""
    from app.models import PricingLineItem, PricingLineEscalation, PricingCategory, PricingModel
    from app.routers.pricing import compute_model
    import json

    m = PricingModel(
        name="cap test", base_years=5, extension_years=0,
        cif_volumes_json=json.dumps([1_000_000] * 5),
        pif_volumes_json=json.dumps([500_000] * 5),
        cif_margin_pct=10.0, pif_margin_pct=10.0,
        target_contract_value=0.0, version=1,
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    cat = PricingCategory(pricing_model_id=m.id, name="Cap", sort_order=1)
    db.add(cat)
    db.commit()
    db.refresh(cat)
    line = PricingLineItem(
        category_id=cat.id, name="Capex",
        allocation_basis="CAPITAL_AMORT", included=True,
        current_cost=0.0, future_cost=100_000.0, reduction_pct=0.0,
        fringe_pct=0.0, qty=10.0, unit="each", sort_order=1,
    )
    db.add(line)
    db.commit()
    db.refresh(line)
    # Add 99% CPI for every year — if the engine respects the CAPITAL_AMORT
    # rule, this should be IGNORED.
    for y in range(1, 6):
        db.add(PricingLineEscalation(
            line_item_id=line.id, year_idx=y, cpi_pct=99.0,
            override_amount=None,
        ))
    db.commit()
    compute_model(db, m.id)
    db.refresh(m)

    # Total capex = 100_000 * 10 = 1_000_000 spread over 5 years, no CPI.
    # SHARED-style allocation by volume: 1M/1.5M to CIF, 0.5M/1.5M to PIF.
    expected_total_cost = 1_000_000.0
    assert abs(m.computed_total_cost - expected_total_cost) <= 1.00
