"""Seed competitive intelligence from the 2021 NJ T1628 / 20DPP00471 Evaluation
Committee Report (Nov 22, 2021, Enhanced Motor Vehicle Inspection Maintenance System).

Idempotent. Safe to run multiple times. Source doc: docs/ExtractPage1.pdf.
"""
from app.database import SessionLocal
from app.models import Competitor, CompetitorHistoricalBid, CompetitorBidStrategy

SOURCE = "docs/ExtractPage1.pdf (2021 NJ T1628 Evaluation Committee Report)"
RFP_NAME = "NJ T1628 / 20DPP00471 Enhanced Motor Vehicle Inspection Maintenance System"
BID_YEAR = 2021
STATE = "NJ"
# Contract term: 6-year base + 4 × 1-year extensions (total possible term = 10 years)
CONTRACT_TERM_YEARS = 10


def upsert_competitor(db, name, website=None, aliases=None, description=None, watchlist=True):
    c = db.query(Competitor).filter(Competitor.name == name).first()
    if c:
        return c
    import json as _json
    c = Competitor(
        name=name,
        website=website,
        aliases=_json.dumps(aliases or []),
        description=description,
        watchlist=watchlist,
    )
    db.add(c)
    db.flush()
    return c


def upsert_bid(db, competitor_id, award_status, total_value, summary, notes=None):
    bid = (
        db.query(CompetitorHistoricalBid)
        .filter(
            CompetitorHistoricalBid.competitor_id == competitor_id,
            CompetitorHistoricalBid.rfp_name == RFP_NAME,
            CompetitorHistoricalBid.bid_year == BID_YEAR,
        )
        .first()
    )
    if bid:
        # Keep seed re-runs from drifting values
        bid.award_status = award_status
        bid.total_value = total_value
        bid.summary = summary
        bid.notes = notes
        bid.source_doc = SOURCE
        bid.state = STATE
        bid.contract_term_years = CONTRACT_TERM_YEARS
        db.flush()
        return bid
    bid = CompetitorHistoricalBid(
        competitor_id=competitor_id,
        rfp_name=RFP_NAME,
        state=STATE,
        bid_year=BID_YEAR,
        contract_term_years=CONTRACT_TERM_YEARS,
        total_value=total_value,
        award_status=award_status,
        summary=summary,
        source_doc=SOURCE,
        notes=notes,
    )
    db.add(bid)
    db.flush()
    return bid


def upsert_strategy(db, bid_id, competitor_id, label, description, evidence, confidence="high"):
    """Idempotent on (historical_bid_id, strategy_label)."""
    existing = (
        db.query(CompetitorBidStrategy)
        .filter(
            CompetitorBidStrategy.historical_bid_id == bid_id,
            CompetitorBidStrategy.strategy_label == label,
        )
        .first()
    )
    if existing:
        existing.description = description
        existing.evidence = evidence
        existing.confidence = confidence
        existing.competitor_id = competitor_id
        db.flush()
        return existing
    s = CompetitorBidStrategy(
        historical_bid_id=bid_id,
        competitor_id=competitor_id,
        strategy_label=label,
        description=description,
        evidence=evidence,
        confidence=confidence,
    )
    db.add(s)
    db.flush()
    return s


# ── Competitors ─────────────────────────────────────────────────────

OPUS_DESC = (
    "OPUS Inspection Inc. — global contractor-managed vehicle inspection & "
    "maintenance (I/M) incumbent; operations in 18 U.S. states and 4 continents. "
    "Won NJ T1628 in 2021 ($199.78M total Blanket P.O. value, $121.5M BAFO on "
    "6-year base). Acquired Envirotest and Systech, giving lineage back to the "
    "first contractor-managed I/M program (Arizona, 1976)."
)

APPLUS_DESC = (
    "Applus Technologies, Inc. — global I/M contractor; in-house design, "
    "development and manufacturing of automotive and I/M systems. First U.S. I/M "
    "company to develop heavy-duty OBD emission testing (SmogDADdy scan tool). "
    "Ranked 2nd on NJ T1628 (2021) at $173.0M BAFO. Active in CT, GA, ID, IL, MA, "
    "OR, UT, WA and others; 10 similar contracts, avg 12-year hold, 1.56M "
    "inspections/year."
)

PARSONS_NOTE = (
    "Parsons Commercial Technology — Parsons' commercial services arm. On NJ "
    "T1628 (2021) Parsons was deemed NON-RESPONSIVE by the State PRU because "
    "its Source Disclosure Form declared software development would be performed "
    "in Canada for 'operational efficiency and cost reduction' — violating "
    "N.J.S.A. 52:34-13.2 and NJSS Term & Condition 3.6, which require services "
    "on a service-primary contract to be performed within the United States. "
    "Parsons never advanced to technical or price evaluation."
)


# ── Strategy rows (from Evaluation Report Sections III–VIII) ───────

OPUS_STRATEGIES = [
    ("AWS Cloud-Hosted NGVID Infrastructure",
     "Opus proposes Amazon Web Services as the hosting layer for the entire NGVID / NGSystem with Opus engineering retaining operational responsibility. Emphasizes continuous hardware/software refresh, virtually unlimited storage and two prior cloud-migration case studies (Cache County UT 2020, Rhode Island 2017) to de-risk legacy-VID transition.",
     "Opus Quote pg. 701, 435 – cited in Eval Report Section VI/Criterion C, pg. 14-15",
     "high"),
    ("VID Central Responsive Web Management Console",
     "Single web-based console (VID Central) with responsive layout that auto-adapts to mobile or PC; Live Network Status panel gives quick stats, analyzer status, software-version distribution, alerts, system response time; fully customizable per-user dashboards.",
     "Opus Quote pg. 453-457; committee noted it 'exceeds the requirements of Section 3.4' and cited the web-based workstation software as a 'big advantage for the program' (Eval Report pg. 12)",
     "high"),
    ("Aggressive CIF Retrofit with ALPR Wait-Time Capture",
     "Plans to retrofit all 25 CIFs and 95 of 107 existing lanes (leaves 12 unused lanes for future growth), retrofit all 3 SIF lanes, install ALPR cameras at ticket gates of all but 4 CIFs to capture >98% of inbound vehicles for wait-time analytics. Tablet-based NGWorkstation for mobility and throughput.",
     "Opus Quote pg. 200, 206; Eval Report pg. 12-13",
     "high"),
    ("Two-Position Lane Configuration — 26.87 veh/hr (optimistic)",
     "Proposing two-position test lane configuration with advertised 26.87 vehicles/hour/lane, operating an average of 78 CIF lanes daily across the network. Committee flagged the throughput as a 'perfect world' figure vulnerable to safety issues, retests and problem vehicles.",
     "Opus Quote pg. 541, 556-557; Eval Report pg. 13",
     "medium"),
    ("DAD4 OBD-II Device Bundled Into Tablet Workstation",
     "OBD module is the Opus-owned Mcclean DAD4 OBD-II device (California BAR DAD-certified) built into the tablet NGWorkstation. Communicates with 99.9% of 1996+ OBD-eligible vehicles ≤14,000 lb GVWR and 99.5% of >14,000 lb GVWR vehicles — meeting the Bid Solicitation despite no standalone HDGV/HDDV OBD experience.",
     "Opus Quote pg. 645; Eval Report pg. 14",
     "high"),
    ("Heavy-Duty OBD Experience Gap (Known Weakness)",
     "Opus' Quote did not list experience providing heavy-duty OBD testing as an operating firm, although the proposed DAD4 device theoretically supports it. Committee explicitly noted 'it would have been helpful to see heavy-duty OBD testing experience.' Applus exploited this gap.",
     "Eval Report pg. 11 (Criterion B discussion)",
     "high"),
    ("Azure DevOps as Transparent Issue-Management System",
     "All implementation issues tracked in Azure DevOps with dashboard access provided directly to the State Contract Manager. PMIS stack: Microsoft Office 365, MS Project, Azure DevOps, SharePoint, central document repository. Intentional transparency play.",
     "Opus Quote pg. 140, 142; Eval Report pg. 11-12",
     "high"),
    ("500 Annual Programming Hours With Rollover",
     "Meets the minimum 250-hour additional-programming requirement by offering 500 hours per year and letting unused hours accumulate across the Blanket P.O. term. Committee saw rollover as meeting the bar but noted 500 hours can be consumed quickly on a program this size.",
     "Eval Report Section VI, Criterion C narrative",
     "medium"),
    ("SEIU Local 32BJ Union Engagement Day-One",
     "Commits to meet SEIU Local 32BJ leadership on the Blanket P.O. effective date to negotiate continuation of the existing inspector workforce. Plans 200 full/part-time inspectors at 65% efficiency, can operate at 80% lane efficiency with as few as 181 inspectors; 1 facility manager plus 1 assistant facility manager per CIF (exceeds Section 3.15).",
     "Opus Quote pg. 574, 584; Eval Report pg. 13",
     "high"),
    ("Preventive Maintenance Funded at $25K/SIF With CT Spare-Parts Depot",
     "Sets aside $25,000 per SIF for preventive maintenance and corrective repairs (State cap); maintains an Opus-owned manufacturing and parts depot in East Granby, CT to minimize NJ lane/equipment downtime.",
     "Opus Quote pg. 607, 850; Eval Report pg. 13-14",
     "high"),
    ("Pre-Purchase Inspection-Fee Model (New York proven)",
     "Offers PIFs / PFFs an alternative to the 90-day lockout: pre-purchase inspection transaction fees in increments of 1/5/10/20 via workstation, web, or help desk (ACH, credit card, PO). Cites 99% reduction in non-payment lockouts in the New York deployment. Committee called the concept 'interesting and beneficial'.",
     "Opus Quote pg. 638-639; Eval Report pg. 14",
     "high"),
    ("-17% Below Average BAFO Price ($121.5M vs $147.2M avg)",
     "Opus' $121,515,837.50 BAFO was 17% below the $147.2M two-bidder average and priced every line of the State Price Sheet below Applus. The full $40M delta between the two responsive bids came down to Opus' lower proposed unit price per inspection. Pricing Analyst: 'no immediate concerns on Opus' pricing despite the disparity'.",
     "Eval Report Section VII Price Analysis, pg. 22-23: Opus original $121,628,337.50 / BAFO $121,515,837.50 (Technical Rank 1, Price Rank 1)",
     "high"),
    ("Institutional Knowledge via Envirotest/Systech Acquisition + ex-Parsons VP",
     "Leverages 1976 Arizona lineage (first contractor-managed I/M program), acquired Envirotest and Systech for multi-decade continuity, and its proposed Vice President previously worked for Parsons' Environment & Infrastructure group, SGS Testcom, and Applus — giving firsthand NJ program visibility. 18 similar contracts, avg 21-year hold, 1.98M inspections/year.",
     "Opus Quote pg. 918, 1084; Eval Report pg. 10",
     "high"),
    ("2014 New York Potential-Default Transparently Disclosed",
     "Proactively disclosed the 2014 New York letter of potential default (software-release delay), documented how the dispute was resolved 'amicably, cooperatively, and expeditiously' and noted NY re-awarded Opus the next-gen contract starting Dec 2022. Demonstrates reliable resolution posture.",
     "Opus Quote pg. 1085-1086; Eval Report pg. 11",
     "medium"),
]

APPLUS_STRATEGIES = [
    ("In-House Design-Build-Manufacture Stack",
     "Applus performs all design, development and manufacturing of its automotive and I/M systems in-house — no subcontractor dependence on the core technology stack. Positions itself as vertically integrated vs. Opus' acquire-and-integrate model.",
     "Eval Report Section VI, Criterion B narrative",
     "high"),
    ("First U.S. I/M Company to Develop Heavy-Duty OBD",
     "Claims status as the first I/M company in the U.S. to develop OBD emission testing for heavy-duty vehicles. Actively co-developing HDGV OBD inspection protocols with MassDEP/RMV. SmogDADdy OBD scan tool update covers LDV (≤8,500 lb GVWR), MD/HD diesel (≤14,000 lb GVWR), hybrids, and 2014+ HDVs with 99.9% communication rate. This is the exact gap the Committee flagged on Opus.",
     "Eval Report Section VI, Criterion B (HDV OBD) — contrasted against Opus' noted weakness",
     "high"),
    ("PMP-Certified Project Manager — 27 Years I/M",
     "Proposed PM has 27 years of I/M program development and management experience, PMP-certified, with hands-on ops manager, training manager and implementation lead roles across Illinois, Washington, Missouri and Indiana; managed Envirotest Systems Corp implementations. Directly exceeds the 5-year BS §4.4.4.3 minimum.",
     "Eval Report Section VI, Criterion A",
     "high"),
    ("38-Year QA Manager (ex-Envirotest IT Manager)",
     "Quality Assurance Manager has 38 years of I/M experience; Director of QA at Applus since 2008; previously Envirotest IT Manager. Covers CT, GA, ID, UT, IL, MA programs. Criterion A score driver.",
     "Eval Report Section VI, Criterion A",
     "high"),
    ("C-Suite Visibility on NJ Program (CEO + VP of Corporate Development)",
     "CEO has 34 years business experience, 24 dedicated to I/M — personally oversaw GA, RI, MA, CT, WA, CA and IL I/M rollouts. VP of Corporate Development has 38 years in vehicle emissions testing and previously worked at Parsons, MACTEC and Systems Control — was 'responsible for contract management oversight of the New Jersey Vehicle Inspection Program' (insider NJ knowledge equivalent to Opus' ex-Parsons VP play).",
     "Eval Report Section VI, Criterion A",
     "high"),
    ("Oracle on Internap/CoreSite Private Cloud",
     "Secure cloud platform provided by Internap and CoreSite rather than AWS: servers, firewalls, Anturis monitoring, Oracle Enterprise Manager, Oracle-specific monitoring, VMware vSphere 6.0. Differentiator vs Opus' AWS play; relies on enterprise-grade Oracle stack rather than hyperscaler elasticity.",
     "Eval Report Section VI, Criterion C",
     "high"),
    ("Responsive Web Design NGWorkstation Dashboard",
     "Web-enabled RWD UI provides a single point of entry to NGVID functionality, application modules, system administration, program activity, inspection history, subscription services, real-time reporting and analytics. Works across tablets and PCs. Directly competitive with Opus' VID Central.",
     "Eval Report Section VI, Criterion C",
     "high"),
    ("1,000 Annual Programming Hours (2× Opus) With 1-Hour Avg SLA",
     "Offers 1,000 hours/year of programming for NGVID/NGWorkstation changes (2× the State's 500-hr requirement and 2× Opus' proposal). Multi-tier support: remote-first, escalating to dispatch of a field technician; claims 1-hour average issue response; unused hours accrue and roll over.",
     "Eval Report Section VI, Criterion C",
     "high"),
    ("All-107-Lane Retrofit With Two-Position + OBD-Only Flex Config",
     "Retrofits all 107 lanes — 60 OBD-only single-position lanes (advertised 18 veh/hr, Committee called it optimistic) and 47 two-position lanes with Hunter brake testers, pneumatic lifts and undercarriage cameras (22.7 veh/hr). Two-position lanes can fall back to single-position mode during low heavy-vehicle demand. More aggressive scope than Opus' 95-lane retrofit but flagged as 'not ideal or cost effective' by Committee.",
     "Eval Report Section VI, Criterion C",
     "medium"),
    ("Dedicated CIF/SIF Managers + Small-Business Subcontracting",
     "Full-time CIF Manager AND Assistant Manager assigned to each of 25 CIFs; all routine maintenance, repairs and utilities absorbed at vendor expense; uses small-business subcontractors for interior/exterior facility maintenance (aligns with SBE/DVOB set-aside).",
     "Eval Report Section VI, Criterion C",
     "high"),
    ("45-Day Workforce Transition Hiring All Parsons Staff",
     "Immediate post-award HR team on-site to negotiate unions and hire existing Parsons staff — including managers — within a 45-day window, supporting a 90-day operational transition inside the 12-month system transition. Committee explicitly noted this timeline 'could be tight' but that hiring intent mitigates risk.",
     "Eval Report Section VI, Criterion C recommendation notes",
     "high"),
    ("Transparency on Prior Subcontractor Failures (MA & CT)",
     "Proactively disclosed two historical subcontractor-caused failures: Massachusetts (liquidated damages 1999-2008; re-awarded 2016 after discontinuing the partnership) and Connecticut (April 2004 equipment flaws; Applus reimbursed motorists, ended the subcontractor relationship, re-awarded 2011). Posture: accept full responsibility, fix, regain trust.",
     "Eval Report Section VI, Criterion B",
     "high"),
    ("10 Similar Contracts, Avg 12-Year Hold, 1.56M Inspections/Yr",
     "Submitted 10 contracts demonstrating hybrid and decentralized I/M experience across IL, CT, RI, ID, MA, UT. Average hold duration 12 years; Illinois since 2007, Connecticut since 2003; average 1,563,889 inspections/year.",
     "Eval Report Section VI, Criterion B",
     "high"),
    ("Price-Rank 2 at $173.0M BAFO — $40M Unit-Price Gap vs Opus",
     "Original quote $173,558,268.00 / BAFO $173,048,736.94 (Price Ranking 2). The Pricing Analyst attributed the entire $40M delta vs Opus to a higher proposed unit price per inspection (Lines 10-11 CIF per-inspection rates). Positions Applus as the technology-heavy, premium-priced alternative.",
     "Eval Report Section VII Price Analysis, pg. 22-23",
     "high"),
    ("References — 6 of 7 Responded, Confirmed Scoring",
     "Six of seven reference questionnaires returned. Massachusetts response was brief despite Applus' HDV OBD work there. Overall responses confirmed the Committee's technical scoring.",
     "Eval Report Section VI, Criterion C tail",
     "medium"),
    ("Technical-Score Near-Tie With Opus (691 vs 693, Δ 10 weighted pts)",
     "Final weighted score 4840 / avg 691 vs Opus' 4850 / avg 693 — statistical tie on technical but lost on price. Implication for Parsons persona work: Applus is the credible technical equal and must be modeled as the primary technical threat.",
     "Eval Report Section V summary table, pg. 8",
     "high"),
]

PARSONS_STRATEGIES = [
    ("Offshore Software Delivery in Canada (Source-Disclosure DQ)",
     "Parsons' 2021 Source Disclosure Form declared that software development would be performed in Canada for 'operational efficiency and cost reduction.' The Bureau ruled this contrary to N.J.S.A. 52:34-13.2 and NJSS Term & Condition 3.6, both of which require services on a service-primary contract to be performed within the United States. Parsons was deemed non-responsive on this single ground and never advanced to technical or price evaluation. MANDATORY guardrail for every future NJ (and most state) bid: declare U.S.-only delivery on the Source Disclosure Form, route all development to U.S.-domiciled staff (no Parsons Canada, no Parsons India for service-primary contracts).",
     "Eval Report Section III.B, pg. 4-5 — verbatim",
     "high"),
    ("Incumbent Posture With No Responsive Quote",
     "Parsons was the NJ MVI/M incumbent (NJ 2019 contract, previously $220.3M). Yet in 2021 the incumbent position was wasted — Parsons could not even get scored because of the compliance defect. Lesson: even incumbency does not survive a Source-Disclosure miss; compliance review is the first gate, not a formality.",
     "Bid Solicitation context; cross-ref to prior-year competitor bid records",
     "medium"),
    ("Technical and Pricing Strategy Unknown to Committee (Black Box)",
     "Because Parsons was rejected at the PRU stage, the Committee never opened the Technical Quote or Price Quote. No scoring exists. Downstream competitive-intelligence gap: we cannot benchmark the 2021 Parsons pricing against Opus/Applus. Persona must therefore be constructed from (a) prior NJ 2019 Parsons bid internals and (b) avoidance of the 2021 compliance failure, not from 2021 evaluation narrative.",
     "Eval Report Section III (Parsons disqualified); Section V onward excludes Parsons entirely",
     "high"),
]


def run():
    db = SessionLocal()
    try:
        # 1. Competitors
        opus = upsert_competitor(
            db, "OPUS Inspection Inc.",
            website="https://www.opusinspection.com",
            aliases=["Opus", "OPUS Inspection", "Opus Inspection"],
            description=OPUS_DESC,
            watchlist=True,
        )
        # Existing Opus row may be under the legacy name "Opus Inspection"
        legacy_opus = db.query(Competitor).filter(Competitor.name == "Opus Inspection").first()
        if legacy_opus and legacy_opus.id != opus.id:
            # Fold the legacy row: the new canonical row is `opus`. Move any
            # children to `opus` then drop the legacy row.
            db.query(CompetitorHistoricalBid).filter(
                CompetitorHistoricalBid.competitor_id == legacy_opus.id
            ).update({"competitor_id": opus.id})
            db.query(CompetitorBidStrategy).filter(
                CompetitorBidStrategy.competitor_id == legacy_opus.id
            ).update({"competitor_id": opus.id})
            db.delete(legacy_opus)
            db.flush()

        applus = upsert_competitor(
            db, "Applus Technologies",
            website="https://www.applustech.com",
            aliases=["Applus", "Applus+", "Applus Technologies, Inc."],
            description=APPLUS_DESC,
            watchlist=True,
        )

        parsons = db.query(Competitor).filter(Competitor.name == "Parsons").first()
        if parsons is None:
            parsons = upsert_competitor(
                db, "Parsons",
                website="https://www.parsons.com",
                aliases=["Parsons Corporation", "Parsons Commercial Technology"],
                description="Parsons Corporation — our own company; model as an 'internal competitor' row for self-reflection and persona-mirroring.",
                watchlist=False,
            )

        db.commit()

        # 2. Historical bids — T1628 (2021)
        opus_summary = (
            "OPUS won NJ T1628 (2021) — Blanket P.O. value $199,776,338 across 6-year base + 4 × 1-yr extensions. "
            "BAFO on the 6-yr base was $121,515,837.50 (Price Ranking 1, Technical Ranking 1). Average technical "
            "score 693/1000; total weighted technical 4850/7000. -17% below the $147.2M two-bidder average."
        )
        opus_bid = upsert_bid(
            db, opus.id,
            award_status="won",
            total_value=199_776_338.00,
            summary=opus_summary,
            notes="Original quote $121,628,337.50; BAFO $121,515,837.50 (6-year base). Blanket P.O. ceiling $199,776,338 includes 4 × 1-year extensions.",
        )

        applus_summary = (
            "Applus ranked #2 on NJ T1628 (2021). BAFO $173,048,736.94 (original $173,558,268.00). Technical Ranking 2, "
            "Price Ranking 2. Average technical score 691/1000; total weighted technical 4840/7000. Differed from Opus "
            "by $40M almost entirely in unit price per inspection (Lines 10-11)."
        )
        applus_bid = upsert_bid(
            db, applus.id,
            award_status="lost",
            total_value=173_048_736.94,
            summary=applus_summary,
            notes="Arithmetic disparity found on Applus' Price Sheet (de minimis, no impact on ranking).",
        )

        parsons_summary = (
            "Parsons submitted a 2021 T1628 quote but was deemed non-responsive by the NJ Bureau (State PRU) "
            "because the Source Disclosure Form declared software development would be performed in Canada — "
            "violating N.J.S.A. 52:34-13.2 and NJSS Term & Condition 3.6. Parsons never advanced past mandatory-"
            "elements review; the Technical Quote and Price Quote were not opened."
        )
        parsons_bid = upsert_bid(
            db, parsons.id,
            award_status="non_responsive",
            total_value=None,
            summary=parsons_summary,
            notes=PARSONS_NOTE,
        )

        db.commit()

        # 3. Strategies
        for label, desc, ev, conf in OPUS_STRATEGIES:
            upsert_strategy(db, opus_bid.id, opus.id, label, desc, ev, conf)
        for label, desc, ev, conf in APPLUS_STRATEGIES:
            upsert_strategy(db, applus_bid.id, applus.id, label, desc, ev, conf)
        for label, desc, ev, conf in PARSONS_STRATEGIES:
            upsert_strategy(db, parsons_bid.id, parsons.id, label, desc, ev, conf)

        db.commit()

        # 4. Summary
        print("=== NJ T1628 (2021) competitive intelligence seeded ===")
        for comp_name, bid in [("Opus", opus_bid), ("Applus", applus_bid), ("Parsons", parsons_bid)]:
            strategies = (
                db.query(CompetitorBidStrategy)
                .filter(CompetitorBidStrategy.historical_bid_id == bid.id)
                .all()
            )
            val = f"${bid.total_value:,.2f}" if bid.total_value else "n/a (non-responsive)"
            print(f"  {comp_name:>8s}  bid_id={bid.id:<3d}  status={bid.award_status:>16s}  value={val:>18s}  strategies={len(strategies)}")
    finally:
        db.close()


if __name__ == "__main__":
    run()
