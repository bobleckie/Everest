"""Seed default staffing positions for the NJ T1628 pricing model.

Sources:
  - docs/Copy of NJ 2019 v1.20.xlsx  (Price Submittal Form & Pricing details)
  - docs/NJ Vendor Cost Comparison 12-12-23.xlsx
  - NJ T1628 Bid Solicitation §§3.14.1, 3.47, 5.16

All roles are US-based (N.J.S.A. 52:34-13.2 Source Disclosure compliance).
"""
import json
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from app.database import SessionLocal
from app.models import PricingModel, StaffingPosition

# Contract term for NJ 2021: 6 base + 4 extension = 10 years
# Headcount arrays are [yr1 .. yr10]

POSITIONS = [
    # ── Key Personnel — Hourly T&M (RFP §3.14.1 / Price Submittal lines 13-20) ──
    # Rates from Parsons NJ 2019 v1.20.xlsx Price Submittal; 1% annual escalation
    # Hours/year = 0 default (billed on T&M actuals, not a fixed cost pool)
    dict(
        role_title="Project Manager",
        classification="hourly_key_personnel",
        hourly_rate=121.52,
        hours_per_year=0.0,          # T&M: headcount captures FTE equivalent
        fringe_pct=0.0,              # bill rate already loaded
        burden_pct=0.0,
        ga_pct=0.0,
        fee_pct=0.0,
        escalation_pct=1.0,
        headcount_by_year=[1,1,1,1,1,1,1,1,1,1],
        allocation_basis="SHARED",
        notes="RFP §3.14.1 — miscellaneous hourly rate line 13. Parsons Y1 $121.52/hr.",
        sort_order=10,
    ),
    dict(
        role_title="Business Analyst",
        classification="hourly_key_personnel",
        hourly_rate=81.54,
        hours_per_year=0.0,
        fringe_pct=0.0,
        burden_pct=0.0,
        ga_pct=0.0,
        fee_pct=0.0,
        escalation_pct=1.0,
        headcount_by_year=[1,1,1,1,1,1,0,0,0,0],
        allocation_basis="SHARED",
        notes="RFP §3.14.1 line 14. Parsons Y1 $81.54/hr.",
        sort_order=11,
    ),
    dict(
        role_title="User Interface Designer",
        classification="hourly_key_personnel",
        hourly_rate=119.59,
        hours_per_year=0.0,
        fringe_pct=0.0,
        burden_pct=0.0,
        ga_pct=0.0,
        fee_pct=0.0,
        escalation_pct=1.0,
        headcount_by_year=[1,1,1,1,1,1,0,0,0,0],
        allocation_basis="SHARED",
        notes="RFP §3.14.1 line 15. US-based designer. Parsons Y1 $119.59/hr.",
        sort_order=12,
    ),
    dict(
        role_title="Developer",
        classification="development",
        hourly_rate=89.15,
        hours_per_year=0.0,
        fringe_pct=0.0,
        burden_pct=0.0,
        ga_pct=0.0,
        fee_pct=0.0,
        escalation_pct=1.0,
        headcount_by_year=[3,3,2,2,1,1,0,0,0,0],
        allocation_basis="SHARED",
        notes="RFP §3.14.1 line 16. US-based. Parsons Y1 $89.15/hr.",
        sort_order=13,
    ),
    dict(
        role_title="Database Administrator",
        classification="development",
        hourly_rate=108.72,
        hours_per_year=0.0,
        fringe_pct=0.0,
        burden_pct=0.0,
        ga_pct=0.0,
        fee_pct=0.0,
        escalation_pct=1.0,
        headcount_by_year=[1,1,1,1,1,1,1,1,1,1],
        allocation_basis="SHARED",
        notes="RFP §3.14.1 line 17. US-based. Parsons Y1 $108.72/hr.",
        sort_order=14,
    ),
    dict(
        role_title="Security Design Lead",
        classification="hourly_key_personnel",
        hourly_rate=192.03,
        hours_per_year=0.0,
        fringe_pct=0.0,
        burden_pct=0.0,
        ga_pct=0.0,
        fee_pct=0.0,
        escalation_pct=1.0,
        headcount_by_year=[1,1,1,1,1,1,0,0,0,0],
        allocation_basis="SHARED",
        notes="RFP §3.14.1 line 18. Parsons Y1 $192.03/hr.",
        sort_order=15,
    ),
    dict(
        role_title="Security Tester",
        classification="hourly_key_personnel",
        hourly_rate=192.02,
        hours_per_year=0.0,
        fringe_pct=0.0,
        burden_pct=0.0,
        ga_pct=0.0,
        fee_pct=0.0,
        escalation_pct=1.0,
        headcount_by_year=[1,1,1,1,0,0,0,0,0,0],
        allocation_basis="SHARED",
        notes="RFP §3.14.1 line 19. Parsons Y1 $192.02/hr.",
        sort_order=16,
    ),
    dict(
        role_title="Quality Assurance Manager",
        classification="hourly_key_personnel",
        hourly_rate=81.54,
        hours_per_year=0.0,
        fringe_pct=0.0,
        burden_pct=0.0,
        ga_pct=0.0,
        fee_pct=0.0,
        escalation_pct=1.0,
        headcount_by_year=[1,1,1,1,1,1,0,0,0,0],
        allocation_basis="SHARED",
        notes="RFP §3.14.1 line 20. Parsons Y1 $81.54/hr.",
        sort_order=17,
    ),

    # ── Management / Salaried (from Pricing details row 18) ──
    dict(
        role_title="Program Manager (Salaried)",
        classification="salaried_mgmt",
        base_salary=133640.0,        # recurring annual from Pricing details
        hourly_rate=0.0,
        hours_per_year=1696.0,
        fringe_pct=40.25,            # from Pricing details row 20 (MGT fringe)
        burden_pct=0.0,
        ga_pct=0.0,
        fee_pct=0.0,
        escalation_pct=3.0,          # +3% from yr3; use 3% overall as proxy
        headcount_by_year=[1,1,1,1,1,1,1,1,1,1],
        allocation_basis="SHARED",
        notes="US Salaried non-union management. Pricing details row 18: $133,640/yr recurring. 40.25% fringe.",
        sort_order=20,
    ),

    # ── Union Hourly (from Pricing details rows 19-21) ──
    dict(
        role_title="Inspector / Operator (Union)",
        classification="union_hourly",
        hourly_rate=0.0,             # union CBA rate — enter via scenario
        base_salary=424064.0,        # aggregate pool Y1 from Pricing details
        hours_per_year=1696.0,       # FT = 212 days × 8 hrs
        fringe_pct=63.0,             # union fringe from row 21
        burden_pct=0.0,
        ga_pct=0.0,
        fee_pct=0.0,
        escalation_pct=3.0,          # +3% yr2, then +1%/yr; 3% is conservative avg
        headcount_by_year=[1,1,1,1,1,1,1,1,1,1],
        allocation_basis="PIF",
        notes="US Union labor pool. Parsons CBA. $424,064/yr aggregate Y1; 63% fringe. "
              "Actual inspector headcount set per station volume.",
        sort_order=30,
    ),

    # ── Field Operations (RFP §3.14 / Application Dev module) ──
    dict(
        role_title="Facility Manager",
        classification="field_operations",
        base_salary=75000.0,
        hourly_rate=0.0,
        hours_per_year=1696.0,
        fringe_pct=40.25,
        burden_pct=0.0,
        ga_pct=0.0,
        fee_pct=0.0,
        escalation_pct=3.0,
        headcount_by_year=[1,1,1,1,1,1,1,1,1,1],
        allocation_basis="CIF",
        notes="On-site CIF facility manager. RFP App Dev module — 'Setup Facility Manager'.",
        sort_order=40,
    ),
    dict(
        role_title="Facility Technician",
        classification="field_operations",
        base_salary=55000.0,
        hourly_rate=0.0,
        hours_per_year=1696.0,
        fringe_pct=40.25,
        burden_pct=0.0,
        ga_pct=0.0,
        fee_pct=0.0,
        escalation_pct=3.0,
        headcount_by_year=[2,2,2,2,2,2,2,2,2,2],
        allocation_basis="CIF",
        notes="CIF/PIF equipment maintenance. RFP App Dev module — 'Setup Technician'.",
        sort_order=41,
    ),
    dict(
        role_title="Bus Inspection Team (BIT) Operator",
        classification="union_hourly",
        base_salary=0.0,
        hourly_rate=25.0,            # placeholder — per union CBA
        hours_per_year=1920.0,       # warehouse/field 240 days × 8 hrs
        fringe_pct=63.0,
        burden_pct=0.0,
        ga_pct=0.0,
        fee_pct=0.0,
        escalation_pct=3.0,
        headcount_by_year=[2,2,2,2,2,2,2,2,2,2],
        allocation_basis="SHARED",
        notes="Mobile bus inspection team (tablet-equipped, 80 units in capital). "
              "RFP §3.14 — contractor field staff. 1920 hrs/yr (warehouse schedule).",
        sort_order=42,
    ),
    dict(
        role_title="Emission Repair Technician (ERT)",
        classification="field_operations",
        base_salary=0.0,
        hourly_rate=30.0,            # placeholder
        hours_per_year=1696.0,
        fringe_pct=40.25,
        burden_pct=0.0,
        ga_pct=0.0,
        fee_pct=0.0,
        escalation_pct=3.0,
        headcount_by_year=[2,2,2,2,2,2,2,2,2,2],
        allocation_basis="PIF",
        notes="Certified emission repair tech at ERFs. RFP §3.14 — licensed by MVC.",
        sort_order=43,
    ),

    # ── ODC / Consulting ──
    dict(
        role_title="Consulting / Temporary Labor",
        classification="odc",
        base_salary=145000.0,        # Y1 ODC pool from Pricing details row 81
        hourly_rate=0.0,
        hours_per_year=1696.0,
        fringe_pct=0.0,              # ODC billed at all-in rate
        burden_pct=0.0,
        ga_pct=0.0,
        fee_pct=0.0,
        escalation_pct=0.0,
        headcount_by_year=[1,0,0,0,0,0,0,0,0,0],  # startup only
        allocation_basis="SHARED",
        notes="Startup phase consulting pool only ($145k Y1, then $0). "
              "Pricing details row 81.",
        sort_order=50,
    ),
    dict(
        role_title="IT / IS Labor",
        classification="development",
        base_salary=0.0,
        hourly_rate=0.0,
        hours_per_year=1696.0,
        fringe_pct=0.0,
        burden_pct=0.0,
        ga_pct=0.0,
        fee_pct=0.0,
        escalation_pct=0.0,
        headcount_by_year=[0]*10,
        allocation_basis="SHARED",
        notes="IS Labor bucket — modeled at $0 in 2019 Parsons model (row 82). "
              "US-based. Enter actual when scoped.",
        sort_order=51,
    ),
]


def seed(db, model_id: int, total_years: int):
    existing = {
        p.role_title: p
        for p in db.query(StaffingPosition)
        .filter(StaffingPosition.pricing_model_id == model_id)
        .all()
    }
    created = updated = 0
    for d in POSITIONS:
        hc = list(d.get("headcount_by_year", []))
        while len(hc) < total_years:
            hc.append(0.0)
        hc = hc[:total_years]
        d2 = {k: v for k, v in d.items() if k != "headcount_by_year"}
        d2["headcount_by_year"] = json.dumps(hc)
        if d2["role_title"] in existing:
            pos = existing[d2["role_title"]]
            for k, v in d2.items():
                setattr(pos, k, v)
            updated += 1
        else:
            pos = StaffingPosition(pricing_model_id=model_id, user_defined=False, **d2)
            db.add(pos)
            created += 1
    db.commit()
    return created, updated


def main():
    db = SessionLocal()
    try:
        models = db.query(PricingModel).all()
        if not models:
            print("No pricing models found.")
            return
        for m in models:
            total_years = (m.base_years or 6) + (m.extension_years or 4)
            created, updated = seed(db, m.id, total_years)
            print(f"Model [{m.id}] '{m.name}': {created} created, {updated} updated ({total_years} yrs)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
