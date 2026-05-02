#!/usr/bin/env python3
"""Seed the pricing system with:

1. A "NJ 2019 Reference Template" PricingModel containing the 8-tab structure
   and line-item NAMES derived from the NJ 2019 v1.20 workbook.
   Per user directive, current_cost / future_cost are left at 0.0 as
   placeholders — users enter real numbers via the UI.

2. Historical competitor bids for Parsons and Opus on the NJ 2019 Vehicle
   Inspection RFP, including per-line pricing (from NJ Vendor Cost
   Comparison 12-12-23.xlsx) and strategy notes used by the writing
   personas when impersonating each competitor.

Run with: ``py seed_pricing_template.py`` (idempotent — re-running won't
duplicate rows).
"""
from __future__ import annotations

import json
import sys
from typing import List, Optional

from app.database import SessionLocal
from app.models import (
    Competitor,
    CompetitorBidLineItem,
    CompetitorBidStrategy,
    CompetitorHistoricalBid,
    PricingCategory,
    PricingLineItem,
    PricingModel,
    PricingScenario,
    User,
)


TEMPLATE_NAME = "NJ 2019 Reference Template"
PARSONS_BID_TAG = "NJ 2019 Enhanced MVI/M — Parsons bid"
OPUS_BID_TAG = "NJ 2019 Enhanced MVI/M — Opus bid"


# ───────────── Structure derived from NJ 2019 workbook ─────────────
# Each tuple: (tab, category_name, [(line_name, allocation_basis, unit), ...])
TEMPLATE_STRUCTURE = [
    # Tab 1: ASSUMPTIONS — single category capturing global contract facts
    ("assumptions", "Contract Assumptions", [
        ("Total Work Hours per FTE (Parsons salaried)", "SHARED", "hours/yr"),
        ("Total Work Hours per FTE (Union)", "SHARED", "hours/yr"),
        ("Paid Vacation & Statutory Holidays", "SHARED", "hours/yr"),
        ("PTO — Vacation / Sick / Bereavement / Military", "SHARED", "hours/yr"),
        ("Labor Inflation (annual)", "SHARED", "%"),
        ("ODC Inflation (annual)", "SHARED", "%"),
        ("Union Labor Inflation (annual)", "SHARED", "%"),
    ]),

    # Tab 2: LABOR — direct salaried and union wages + fringe
    ("labor", "Canadian Labor", [
        ("Development — Canada", "SHARED", "hours"),
        ("Support — Canada", "SHARED", "hours"),
        ("Canada Fringe Benefits", "SHARED", "% of labor"),
    ]),
    ("labor", "US Salaried Labor (MGT)", [
        ("Management Labor", "SHARED", "hours"),
        ("MGT Fringe Benefits", "SHARED", "% of labor"),
    ]),
    ("labor", "US Union Labor", [
        ("Union Labor — Inspectors & Techs", "SHARED", "hours"),
        ("Union Base Fringe", "SHARED", "% of labor"),
        ("Union Pension / Healthcare Fringe", "SHARED", "% of labor"),
    ]),

    # Tab 3: ODCs — Other Direct Costs (recurring, non-labor, non-capital)
    ("odcs", "Facilities & Infrastructure", [
        ("Leased Facilities — Office Rent", "SHARED", "year"),
        ("Infrastructure — Recurring (datacenter, licenses)", "SHARED", "year"),
        ("Snow Removal (CIF stations)", "CIF", "year"),
        ("Bonds / Insurance / Taxes / Fees", "SHARED", "year"),
        ("Cyber Security Bond", "SHARED", "year"),
        ("Security Testing (annual)", "SHARED", "year"),
        ("Insurance (general liability)", "SHARED", "year"),
        ("Contingency Reserve", "SHARED", "year"),
    ]),
    ("odcs", "Operations", [
        ("Safety Equipment Warranty (Hunter)", "SHARED", "year"),
        ("Safety Supplies & PPE", "SHARED", "year"),
        ("Calibration Gas", "CIF", "year"),
        ("Lane Tech Licenses", "CIF", "station-year"),
        ("Travel (Gas / Mileage)", "SHARED", "year"),
        ("Employee Recognition / Sunshine Fund", "SHARED", "year"),
        ("Training", "SHARED", "year"),
        ("Uniforms", "SHARED", "year"),
        ("Incentive Payments", "SHARED", "year"),
        ("Miscellaneous", "SHARED", "year"),
    ]),

    # Tab 4: CAPITAL — one-time hardware / software, amortized across contract
    ("capital", "IT Infrastructure (One-time)", [
        ("Datacenter Hosting — Additional Cabinet", "CAPITAL_AMORT", "each"),
        ("Datacenter Cables / Wiring / Cable Mgmt", "CAPITAL_AMORT", "each"),
        ("VMware vSphere (Virtualization HW) — PROD", "CAPITAL_AMORT", "each"),
        ("vCentre Server (HP DL360)", "CAPITAL_AMORT", "each"),
        ("DB / HSM Server Refresh (Oracle SPARC)", "CAPITAL_AMORT", "each"),
        ("VMware Storage — Oracle ZFS 3-2", "CAPITAL_AMORT", "each"),
        ("10GB Cisco Switch (40% discount)", "CAPITAL_AMORT", "each"),
        ("Centralized Stations VPN Hardware (Cisco 1841)", "CAPITAL_AMORT", "each"),
        ("Firewall / IPS (Cisco, 40% discount)", "CAPITAL_AMORT", "each"),
    ]),
    ("capital", "Licenses & Software (One-time)", [
        ("RDBMS Mgmt — GoldenGate (PROD)", "CAPITAL_AMORT", "license"),
        ("RDBMS Mgmt — GoldenGate Foundation Pack", "CAPITAL_AMORT", "license"),
        ("RDBMS Mgmt — Multitenant Option", "CAPITAL_AMORT", "license"),
        ("VIN Decoder (VinPower)", "CAPITAL_AMORT", "license"),
        ("Business Intelligence — Actuate", "CAPITAL_AMORT", "license"),
        ("Linux Subscription (Satellite Server)", "CAPITAL_AMORT", "license"),
        ("Satellite Server Client Licenses", "CAPITAL_AMORT", "license"),
        ("Windows Server Datacenter Edition", "CAPITAL_AMORT", "license"),
        ("Mobile Device Management (Soti)", "CAPITAL_AMORT", "license"),
        ("Backup Software (NetBackup)", "CAPITAL_AMORT", "license"),
    ]),
    ("capital", "Station Equipment (One-time)", [
        ("PIF Inspection Workstation (PC-based)", "PIF", "station"),
        ("PIF Inspection Workstation (Tablet-based)", "PIF", "station"),
        ("PIF Inspection Workstation (Laptop-based)", "PIF", "station"),
        ("CIF Inspection Bay Equipment", "CIF", "lane"),
        ("OBD Scan Tool", "PIF", "each"),
        ("Monitor / Keyboard / Mouse bundle", "PIF", "station"),
        ("Printer (receipt / label)", "PIF", "station"),
        ("Barcode Scanner", "PIF", "station"),
    ]),

    # Tab 5: HOURLY_RATES — pass-through labor categories
    ("hourly_rates", "Hourly Labor Categories", [
        ("Additional Work — General (per hour)", "SHARED", "hour"),
        ("Project Manager (per hour)", "SHARED", "hour"),
        ("Business Analyst (per hour)", "SHARED", "hour"),
        ("User Interface Designer (per hour)", "SHARED", "hour"),
        ("Developer (per hour)", "SHARED", "hour"),
        ("Database Administrator (per hour)", "SHARED", "hour"),
        ("Security Design Lead (per hour)", "SHARED", "hour"),
        ("Security Tester (per hour)", "SHARED", "hour"),
        ("Quality Assurance Lead (per hour)", "SHARED", "hour"),
        ("Training Specialist (per hour)", "SHARED", "hour"),
    ]),

    # Tab 6: SUBMITTAL — items priced exactly as shown on the state's price sheet
    ("submittal", "NGVID Required Documents", [
        ("NGVID Required Documents (lump sum)", "SHARED", "lump"),
    ]),
    ("submittal", "Design, Development, Implementation", [
        ("Design, Development, and Implementation (lump sum)", "SHARED", "lump"),
    ]),
    ("submittal", "Per-Inspection Transaction Fees", [
        ("CIF Inspection Transaction Fee (Year 1)", "CIF", "inspection"),
        ("CIF Inspection Transaction Fee (Years 2–6)", "CIF", "inspection"),
        ("PIF Inspection Transaction Fee (Year 1)", "PIF", "inspection"),
        ("PIF Inspection Transaction Fee (Years 2–6)", "PIF", "inspection"),
    ]),
    ("submittal", "State Staff Training", [
        ("Training State Staff on NGSystem (lump sum)", "SHARED", "lump"),
    ]),
    ("submittal", "PIF/PFF Replacement Equipment (price-each)", [
        ("Replacement — Monitor", "PIF", "each"),
        ("Replacement — Keyboard", "PIF", "each"),
        ("Replacement — Mouse", "PIF", "each"),
        ("Replacement — OBD Scan Tool", "PIF", "each"),
        ("Replacement — Barcode Scanner", "PIF", "each"),
        ("Replacement — Printer", "PIF", "each"),
        ("Replacement — Tablet", "PIF", "each"),
        ("Replacement — Computer / Laptop", "PIF", "each"),
    ]),

    # Tab 7: CASHFLOW — timing-related inputs (interest, working capital)
    ("cashflow", "Working Capital & Finance", [
        ("Working Capital Reserve (% of annual revenue)", "SHARED", "%"),
        ("Cost of Capital / Discount Rate", "SHARED", "%"),
        ("Days Sales Outstanding (assumed)", "SHARED", "days"),
        ("Milestone Payment Offset (month 1 to first invoice)", "SHARED", "months"),
    ]),

    # Tab 8: COMPARISON — empty placeholder; populated by competitor historical bids
    ("comparison", "Competitor Baseline (read-only)", [
        ("See Intelligence → Competitors → Historical Bids", "SHARED", "—"),
    ]),
]


def _ensure_competitor(db, name: str, description: str) -> Competitor:
    existing = db.query(Competitor).filter(Competitor.name == name).first()
    if existing:
        return existing
    c = Competitor(
        name=name,
        website=None,
        aliases=json.dumps([name]),
        description=description,
        watchlist=False,  # Parsons is us; we don't watchlist ourselves
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _get_admin_user_id(db) -> Optional[int]:
    u = db.query(User).order_by(User.id).first()
    return u.id if u else None


# ───────────────── Template seeder ─────────────────

def seed_template_model(db) -> PricingModel:
    existing = db.query(PricingModel).filter(PricingModel.name == TEMPLATE_NAME).first()
    if existing:
        print(f"[skip] '{TEMPLATE_NAME}' already exists (id={existing.id})")
        return existing

    creator_id = _get_admin_user_id(db)
    m = PricingModel(
        proposal_id=None,
        name=TEMPLATE_NAME,
        description=(
            "Reference pricing model scaffolded from the NJ 2019 Enhanced Motor "
            "Vehicle Inspection/Maintenance bid. Line items are placeholders — "
            "enter current_cost / future_cost via the Pricing Model page."
        ),
        base_years=6,
        extension_years=4,
        target_contract_value=0.0,
        cif_volumes_json=json.dumps(
            [1425000.0, 1900000.0, 1900000.0, 1900000.0, 1900000.0, 1900000.0,
             1900000.0, 1900000.0, 1900000.0, 1900000.0]
        ),
        pif_volumes_json=json.dumps(
            [205000.0, 255000.0, 255000.0, 255000.0, 255000.0, 255000.0,
             255000.0, 255000.0, 255000.0, 255000.0]
        ),
        cif_margin_pct=12.0,
        pif_margin_pct=15.0,
        created_by=creator_id,
    )
    db.add(m)
    db.commit()
    db.refresh(m)

    db.add(PricingScenario(
        pricing_model_id=m.id,
        name="Base Case",
        description="As-bid baseline scenario with template placeholders.",
        is_default=True,
    ))
    db.commit()

    # Build categories + line items
    # Group consecutive rows with the same (tab, category_name)
    grouped: List[tuple] = []
    for tab, cat_name, lines in TEMPLATE_STRUCTURE:
        grouped.append((tab, cat_name, lines))

    cat_sort = {}  # track per-tab sort_order
    for tab, cat_name, lines in grouped:
        cat_sort[tab] = cat_sort.get(tab, 0) + 1
        cat = PricingCategory(
            pricing_model_id=m.id,
            tab=tab,
            name=cat_name,
            description=None,
            sort_order=cat_sort[tab],
            user_defined=False,
        )
        db.add(cat)
        db.flush()

        for i, (line_name, basis, unit) in enumerate(lines, start=1):
            db.add(PricingLineItem(
                category_id=cat.id,
                name=line_name,
                description=None,
                notes=None,
                allocation_basis=basis,
                included=True,
                current_cost=0.0,
                future_cost=0.0,
                reduction_pct=0.0,
                fringe_pct=0.0,
                qty=1.0,
                unit=unit,
                sort_order=i,
                user_defined=False,
            ))

    db.commit()
    print(f"[ok] Created '{TEMPLATE_NAME}' (id={m.id}) with "
          f"{len(TEMPLATE_STRUCTURE)} categories")
    return m


# ───────────────── Historical-bid seeder ─────────────────

def _dump_annual(vals: List[float]) -> str:
    return json.dumps([float(v) for v in vals])


def seed_parsons_bid(db, parsons_id: int):
    existing = (
        db.query(CompetitorHistoricalBid)
        .filter(CompetitorHistoricalBid.rfp_name == PARSONS_BID_TAG)
        .first()
    )
    if existing:
        print(f"[skip] Parsons NJ 2019 historical bid already exists (id={existing.id})")
        return existing

    bid = CompetitorHistoricalBid(
        competitor_id=parsons_id,
        rfp_name=PARSONS_BID_TAG,
        state="NJ",
        bid_year=2019,
        contract_term_years=10,
        total_value=220319369.86,
        award_status="lost",
        summary=(
            "Parsons bid $220.3M on the NJ 2019 Enhanced MVI/M RFP and lost "
            "to Opus ($121.5M). Parsons carried union labor, higher per-"
            "inspection fees, and more expensive CIF capital. Key drivers "
            "of the loss: per-inspection CIF fee of $17.73 vs Opus $9.95; "
            "PC-based workstation $3,037 vs Opus $1,994; union labor model "
            "with ~1% annual wage escalation."
        ),
        source_doc="docs/NJ Vendor Cost Comparison 12-12-23.xlsx",
        notes="Imported from NJ Vendor Cost Comparison analysis (row 40–60, 'Sheet1').",
        created_by=_get_admin_user_id(db),
    )
    db.add(bid)
    db.commit()
    db.refresh(bid)

    # Key line items (from the Vendor Cost Comparison)
    lines = [
        # (category, line_name, qty, unit_price, total_value, annual_values)
        ("NGVID Required Documents", "NGVID Required Documents (lump sum)", 1.0, 250694.86, 250694.86, None),
        ("Design/Dev/Implementation", "Design, Development, and Implementation (lump sum)", 1.0, 1002775.0, 1002775.0, None),
        ("Capital — PIF Equipment", "NGWorkstation PC-based (price per station)", 1250.0, 3037.21, 3796512.5, None),
        ("Capital — PIF Equipment", "NGWorkstation Tablet-based", 1250.0, 3625.94, 4532425.0, None),
        ("Capital — PIF Equipment", "NGWorkstation Laptop-based", 1250.0, 3101.99, 3877487.5, None),
        ("Transaction Fees", "PIF Inspection Fee (Year 1)", 205000.0, 4.92, 1008600.0, None),
        ("Transaction Fees", "PIF Inspection Fee (Years 2–6)", 255000.0, 6.56, 8364000.0, None),
        ("Transaction Fees", "CIF Inspection Fee (Year 1)", 1425000.0, 17.73, 25265250.0, None),
        ("Transaction Fees", "CIF Inspection Fee (Years 2+)", 1900000.0, 17.73, 33687000.0, None),
        ("Hourly Rates", "Project Manager (Y1 $/hr — escalating)", 1.0, 121.52, None,
            [121.52, 122.74, 123.97, 125.21, 126.46, 127.72, 129.0, 130.29, 131.59, 132.91]),
        ("Hourly Rates", "Database Administrator (Y1 $/hr — escalating)", 1.0, 108.72, None,
            [108.72, 109.81, 110.91, 112.02, 113.14, 114.27, 115.41, 116.57, 117.73, 118.91]),
        ("Replacement Equipment", "Monitor (each)", 1.0, 136.0, None, None),
        ("Replacement Equipment", "Tablet (each)", 1.0, 1417.5, None, None),
    ]
    for cat, name, qty, up, tv, annual in lines:
        db.add(CompetitorBidLineItem(
            historical_bid_id=bid.id,
            category=cat,
            line_name=name,
            qty=qty,
            unit_price=up,
            total_value=tv,
            annual_values_json=_dump_annual(annual) if annual else None,
            notes=None,
        ))

    # Parsons' own historical posture
    strategies = [
        ("Union Labor Posture",
         "Parsons prices with unionized inspectors and full fringe load (~63% base + 40% mgmt fringe). This drives higher CIF operating cost than non-union bidders.",
         "NJ 2019 model sheet Pricing details rows 19–21: Union base wage + 63% fringe.",
         "high"),
        ("High CIF Transaction Fee",
         "Parsons bid $17.73 per CIF inspection in NJ 2019, materially above Opus ($9.95). This is a deliberate margin-over-volume posture tied to the union cost base.",
         "NJ Vendor Cost Comparison Sheet1 B60–D60.",
         "high"),
        ("Premium PIF Capital",
         "Parsons PIF PC-based workstation priced at $3,037 vs Opus $1,994 — reflects higher-spec Hunter safety hardware and longer warranty coverage.",
         "NJ Vendor Cost Comparison Sheet1 B20–E20.",
         "medium"),
        ("Moderate Labor Escalation",
         "Parsons assumed ~1% annual wage escalation on hourly categories (PM rate Y1 $121.52 → Y10 $132.91).",
         "NJ Vendor Cost Comparison Sheet1 I15–R15.",
         "medium"),
    ]
    for label, desc, ev, conf in strategies:
        db.add(CompetitorBidStrategy(
            historical_bid_id=bid.id,
            competitor_id=parsons_id,
            strategy_label=label,
            description=desc,
            evidence=ev,
            confidence=conf,
        ))

    db.commit()
    print(f"[ok] Created Parsons historical bid (id={bid.id}) with "
          f"{len(lines)} line items and {len(strategies)} strategies")
    return bid


def seed_opus_bid(db, opus_id: int):
    existing = (
        db.query(CompetitorHistoricalBid)
        .filter(CompetitorHistoricalBid.rfp_name == OPUS_BID_TAG)
        .first()
    )
    if existing:
        print(f"[skip] Opus NJ 2019 historical bid already exists (id={existing.id})")
        return existing

    bid = CompetitorHistoricalBid(
        competitor_id=opus_id,
        rfp_name=OPUS_BID_TAG,
        state="NJ",
        bid_year=2019,
        contract_term_years=10,
        total_value=121515837.50,
        award_status="won",
        summary=(
            "Opus won the NJ 2019 Enhanced MVI/M award at $121.5M — ~45% "
            "below Parsons' $220.3M bid. Opus used a non-union private "
            "inspector model, ultra-low per-inspection CIF fee ($9.95 vs "
            "Parsons $17.73), flat (no-CPI) hourly rates, and cheaper "
            "PC-based station hardware ($1,994 vs Parsons $3,037)."
        ),
        source_doc="docs/NJ Vendor Cost Comparison 12-12-23.xlsx",
        notes="Imported from NJ Vendor Cost Comparison analysis.",
        created_by=_get_admin_user_id(db),
    )
    db.add(bid)
    db.commit()
    db.refresh(bid)

    lines = [
        ("NGVID Required Documents", "NGVID Required Documents (lump sum)", 1.0, 150000.0, 150000.0, None),
        ("Design/Dev/Implementation", "Design, Development, and Implementation (lump sum)", 1.0, 575000.0, 575000.0, None),
        ("Capital — PIF Equipment", "NGWorkstation PC-based (price per station)", 1250.0, 1993.78, 2492225.0, None),
        ("Capital — PIF Equipment", "NGWorkstation Tablet-based", 1250.0, 1944.60, 2430750.0, None),
        ("Capital — PIF Equipment", "NGWorkstation Laptop-based", 1250.0, 2332.79, 2915987.5, None),
        ("Transaction Fees", "PIF Inspection Fee (Year 1)", 205000.0, 2.50, 512500.0, None),
        ("Transaction Fees", "PIF Inspection Fee (Years 2–6)", 255000.0, 2.50, 3187500.0, None),
        ("Transaction Fees", "CIF Inspection Fee (Year 1)", 1425000.0, 9.95, 14179375.0, None),
        ("Transaction Fees", "CIF Inspection Fee (Years 2+)", 1900000.0, 9.95, 94525000.0, None),
        ("Hourly Rates", "Project Manager (flat $/hr no CPI)", 1.0, 225.0, None,
            [225.0] * 10),
        ("Hourly Rates", "Database Administrator (flat $/hr no CPI)", 1.0, 195.0, None,
            [195.0] * 10),
        ("Replacement Equipment", "Monitor (each)", 1.0, 125.0, None, None),
        ("Replacement Equipment", "Tablet (each)", 1.0, 1755.0, None, None),
    ]
    for cat, name, qty, up, tv, annual in lines:
        db.add(CompetitorBidLineItem(
            historical_bid_id=bid.id,
            category=cat,
            line_name=name,
            qty=qty,
            unit_price=up,
            total_value=tv,
            annual_values_json=_dump_annual(annual) if annual else None,
            notes=None,
        ))

    strategies = [
        ("Non-union Private Inspector Model",
         "Opus bids with non-union private inspectors, dramatically lowering the loaded labor rate and avoiding union fringe (~63% base + 40% mgmt uplift).",
         "Reported in NJ 2019 pre-award analysis; consistent with Opus' Connecticut, Tennessee, Virginia bids.",
         "high"),
        ("Ultra-Low CIF Per-Inspection Fee",
         "Opus priced CIF inspection at $9.95 — 44% below Parsons' $17.73. This is Opus' signature price-for-volume tactic on centralized-fleet contracts.",
         "NJ Vendor Cost Comparison Sheet1 B61–D61 ($9.95 × 1.425M tests = $14.18M).",
         "high"),
        ("Low PIF Capital Cost",
         "PC-based PIF workstation priced at $1,993.78 — 34% below Parsons' $3,037. Opus specs lower-tier Hunter hardware and shorter warranty.",
         "NJ Vendor Cost Comparison Sheet1 B21–E21.",
         "high"),
        ("Flat (Zero-CPI) Hourly Rates",
         "Opus holds all hourly labor rates flat for 10 years (PM $225/hr, DBA $195/hr, Developer $195/hr — no escalation). Trades future-year margin compression for a lower NPV bid.",
         "NJ Vendor Cost Comparison Sheet1 I11–R11, I31–R31, I36–R36.",
         "high"),
        ("Low Lump-Sum NGVID & DDI",
         "NGVID documents $150K (vs Parsons $251K); DDI $575K (vs Parsons $1.00M). Opus treats upfront deliverables as loss-leaders to secure volume.",
         "NJ Vendor Cost Comparison Sheet1 B10–E11, B15–E16.",
         "medium"),
    ]
    for label, desc, ev, conf in strategies:
        db.add(CompetitorBidStrategy(
            historical_bid_id=bid.id,
            competitor_id=opus_id,
            strategy_label=label,
            description=desc,
            evidence=ev,
            confidence=conf,
        ))

    db.commit()
    print(f"[ok] Created Opus historical bid (id={bid.id}) with "
          f"{len(lines)} line items and {len(strategies)} strategies")
    return bid


def main():
    db = SessionLocal()
    try:
        # Ensure Parsons exists (Opus already seeded as competitor_id=6)
        parsons = _ensure_competitor(
            db,
            "Parsons",
            "Parsons Corporation — owner of this platform; historical-bid record for "
            "self-analysis when writing from a competitor's perspective.",
        )
        opus = db.query(Competitor).filter(Competitor.name == "Opus").first()
        if opus is None:
            print("[err] Opus competitor not found — expected id=6 from app startup seeder.")
            sys.exit(1)

        seed_template_model(db)
        seed_parsons_bid(db, parsons.id)
        seed_opus_bid(db, opus.id)
        print("\nDone.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
