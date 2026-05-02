from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from ..database import get_db
from ..models import (
    Proposal, ProposalSection, User,
    IngestedDocument, RfpRequirement, RfpScheduleEvent, RfpQuestion,
    Competitor, CompetitorThread, CompetitorTimelineEvent,
    CompetitorNewsItem, ProposalCompetitor,
)
from ..auth import get_current_vendor_or_manager, get_current_active_user
from ..exceptions import ResourceNotFoundError, AuthorizationError, DatabaseError, ValidationError
from ..logging_config import logger
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import json
import re

router = APIRouter()

@router.post("/create")
def create_proposal(
    title: str,
    rfp_reference: str,
    current_user: User = Depends(get_current_vendor_or_manager),
    db: Session = Depends(get_db)
):
    """Create a new RFP proposal"""
    try:
        # Validate input
        if not title or not title.strip():
            raise ValidationError("Title is required")
        if not rfp_reference or not rfp_reference.strip():
            raise ValidationError("RFP reference is required")

        # For vendors, use their own ID. For managers/admins, they can specify vendor_id
        vendor_id = current_user.id if current_user.role == "vendor" else current_user.id  # TODO: Allow managers to specify vendor

        logger.info(f"Creating proposal for user {current_user.username} (role: {current_user.role})")

        proposal = Proposal(
            vendor_id=vendor_id,
            title=title.strip(),
            rfp_reference=rfp_reference.strip(),
            status="draft"
        )

        db.add(proposal)
        db.flush()  # Get the proposal ID without committing

        # Create initial sections
        sections = [
            "vendorLegal", "certifications", "technicalProposal", "projectManagement",
            "operations", "technology", "staffing", "customerService", "security",
            "smallBusiness", "experience", "costProposal", "transition"
        ]

        section_names = {
            "vendorLegal": "Vendor Legal & Registration",
            "certifications": "Mandatory Certifications & Forms",
            "technicalProposal": "Technical Proposal",
            "projectManagement": "Project Management & Schedule",
            "operations": "Operations & Facilities",
            "technology": "Technology / VIIS",
            "staffing": "Staffing & Training",
            "customerService": "Customer Service & Public Information",
            "security": "Security, Audit & Compliance",
            "smallBusiness": "Small Business & Subcontracting",
            "experience": "Organizational Experience & Past Performance",
            "costProposal": "Cost Proposal",
            "transition": "Contract Transition & Close-Out"
        }

        for section_id in sections:
            section = ProposalSection(
                proposal_id=proposal.id,
                section_id=section_id,
                section_name=section_names[section_id],
                content="",
                status="draft"
            )
            db.add(section)

        db.commit()
        db.refresh(proposal)

        logger.info(f"Proposal created successfully: {proposal.id}")
        return {"message": "Proposal created", "proposal_id": proposal.id}

    except ValidationError:
        raise
    except SQLAlchemyError as e:
        db.rollback()
        logger.error(f"Database error creating proposal: {str(e)}")
        raise DatabaseError("Failed to create proposal")
    except Exception as e:
        db.rollback()
        logger.error(f"Unexpected error creating proposal: {str(e)}")
        raise DatabaseError("Failed to create proposal")

def _slugify(text: str) -> str:
    """URL-safe slug from a proposal title or RFP reference."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:64] or f"rfp-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"


def _aggregate_portfolio_card(db: Session, p: Proposal) -> Dict[str, Any]:
    """Compute the per-proposal stats the portfolio dashboard cards render."""
    req_count = (db.query(sa_func.count(RfpRequirement.id))
                 .filter(RfpRequirement.proposal_id == p.id).scalar()) or 0
    verified_count = (db.query(sa_func.count(RfpRequirement.id))
                      .filter(RfpRequirement.proposal_id == p.id,
                              RfpRequirement.verified.is_(True)).scalar()) or 0
    open_questions = (db.query(sa_func.count(RfpQuestion.id))
                      .filter(RfpQuestion.proposal_id == p.id,
                              RfpQuestion.status.in_(("draft", "reviewed"))).scalar()) or 0
    rfp_doc_count = (db.query(sa_func.count(IngestedDocument.id))
                     .filter(IngestedDocument.proposal_id == p.id,
                             IngestedDocument.source_type == "rfp").scalar()) or 0

    days_until_due = None
    if p.due_date:
        delta = p.due_date - datetime.utcnow()
        days_until_due = int(delta.total_seconds() // 86400)

    pct_verified = int(round((verified_count / req_count) * 100)) if req_count else 0

    return {
        "id": p.id,
        "slug": p.slug,
        "title": p.title,
        "rfp_reference": p.rfp_reference,
        "solicitation_number": p.solicitation_number,
        "issuing_agency": p.issuing_agency,
        "status": p.status,
        "due_date": p.due_date.isoformat() if p.due_date else None,
        "days_until_due": days_until_due,
        "submission_date": p.submission_date.isoformat() if p.submission_date else None,
        "capture_lead": p.capture_lead,
        "win_probability": p.win_probability,
        "target_value_usd": p.target_value_usd,
        "description": p.description,
        "stats": {
            "rfp_doc_count": rfp_doc_count,
            "requirement_count": req_count,
            "requirement_verified_pct": pct_verified,
            "open_question_count": open_questions,
        },
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


@router.get("/portfolio")
def get_portfolio(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Aggregated portfolio view — one card payload per proposal.

    Surfaces what the portfolio dashboard needs without N round-trips:
    title, status, due-date countdown, capture-lead/value/probability, and
    the four headline stats (RFP docs, requirement count + % verified,
    open questions). All readable by any authenticated user; vendor-role
    users still see only their own.
    """
    try:
        q = db.query(Proposal)
        if current_user.role == "vendor":
            q = q.filter(Proposal.vendor_id == current_user.id)
        proposals = q.order_by(Proposal.due_date.asc().nullslast(),
                               Proposal.id.desc()).all()
        cards = [_aggregate_portfolio_card(db, p) for p in proposals]
        return {"count": len(cards), "proposals": cards}
    except SQLAlchemyError as e:
        logger.error(f"Database error in portfolio: {e}")
        raise DatabaseError("Failed to load portfolio")


@router.get("/{proposal_id}/dashboard-summary")
def get_dashboard_summary(
    proposal_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Single aggregator endpoint powering the per-RFP workspace dashboard.

    Returns: header info, schedule events (for the timeline), compliance
    rollup (by category / by priority / by compliance status), questions
    breakdown by priority, document inventory, and competitor watch
    (latest threads + latest news). The frontend renders these as a
    visual dashboard with drill-throughs.
    """
    try:
        p = db.query(Proposal).filter(Proposal.id == proposal_id).first()
        if not p:
            raise ResourceNotFoundError("Proposal", proposal_id)
        if current_user.role == "vendor" and p.vendor_id != current_user.id:
            raise AuthorizationError("Not authorized to view this proposal")

        # ── Schedule events (timeline data) ──────────────────────────
        events_q = (db.query(RfpScheduleEvent)
                    .filter(RfpScheduleEvent.proposal_id == proposal_id)
                    .order_by(RfpScheduleEvent.event_date.asc().nullslast()))
        events = []
        for ev in events_q.all():
            events.append({
                "id": ev.id,
                "event_type": ev.event_type,
                "label": ev.label,
                "event_date": ev.event_date.isoformat() if ev.event_date else None,
                "end_date": ev.end_date.isoformat() if ev.end_date else None,
                "is_mandatory": ev.is_mandatory,
                "status": ev.status,
                "assignee": ev.assignee,
                "confidence": ev.confidence,
            })

        # ── Requirements rollup ──────────────────────────────────────
        reqs = db.query(RfpRequirement).filter(
            RfpRequirement.proposal_id == proposal_id).all()
        by_priority: Dict[str, int] = {}
        by_category: Dict[str, int] = {}
        by_compliance: Dict[str, int] = {}
        # Parsons-coverage rollup — drives the dashboard donut + portfolio
        # gap badge. Same shape as `by_compliance` for UI consistency.
        by_parsons_coverage: Dict[str, int] = {
            "covered": 0, "partial": 0, "gap": 0,
            "uncertain": 0, "not_assessed": 0,
        }
        # Response-disposition rollup — distinguishes evidence_required
        # gaps (true coverage problems) from commitment/acknowledgment/form
        # /event/info requirements that don't need past performance.
        by_response_disposition: Dict[str, int] = {}
        # Cross-tab: how many gap+uncertain reqs are in each disposition?
        # The dashboard splits "evidence gaps" vs "pending commitments etc.".
        gap_evidence_required = 0
        gap_commitment_only = 0
        gap_acknowledgment_only = 0
        gap_form_to_complete = 0
        gap_event_attendance = 0
        gap_informational_only = 0
        gap_unclassified = 0
        verified = 0
        for r in reqs:
            by_priority[r.priority or "unspecified"] = by_priority.get(
                r.priority or "unspecified", 0) + 1
            by_category[r.category or "unspecified"] = by_category.get(
                r.category or "unspecified", 0) + 1
            cs = r.compliance_status or "not_assessed"
            by_compliance[cs] = by_compliance.get(cs, 0) + 1
            pc = r.parsons_coverage_status or "not_assessed"
            by_parsons_coverage[pc] = by_parsons_coverage.get(pc, 0) + 1
            disp = r.response_disposition or "(unclassified)"
            by_response_disposition[disp] = by_response_disposition.get(disp, 0) + 1
            # Gap+uncertain by disposition (the headline split for the dashboard)
            if pc in ("gap", "uncertain"):
                d = r.response_disposition
                if d == "evidence_required":
                    gap_evidence_required += 1
                elif d == "commitment_only":
                    gap_commitment_only += 1
                elif d == "acknowledgment_only":
                    gap_acknowledgment_only += 1
                elif d == "form_to_complete":
                    gap_form_to_complete += 1
                elif d == "event_attendance":
                    gap_event_attendance += 1
                elif d == "informational_only":
                    gap_informational_only += 1
                else:
                    gap_unclassified += 1
            if r.verified:
                verified += 1

        # ── Questions breakdown by priority ──────────────────────────
        q_rows = db.query(RfpQuestion).filter(
            RfpQuestion.proposal_id == proposal_id).all()
        questions_by_priority: Dict[str, int] = {}
        questions_by_status: Dict[str, int] = {}
        for q in q_rows:
            questions_by_priority[q.priority or "unspecified"] = (
                questions_by_priority.get(q.priority or "unspecified", 0) + 1)
            questions_by_status[q.status or "draft"] = (
                questions_by_status.get(q.status or "draft", 0) + 1)

        # ── Documents ────────────────────────────────────────────────
        docs = (db.query(IngestedDocument)
                .filter(IngestedDocument.proposal_id == proposal_id).all())
        doc_inventory = {
            "total": len(docs),
            "rfp": sum(1 for d in docs if d.source_type == "rfp"),
            "by_type": {},
        }
        for d in docs:
            t = d.document_type or d.source_type or "other"
            doc_inventory["by_type"][t] = doc_inventory["by_type"].get(t, 0) + 1

        # ── Competitor watch — latest threads + timeline events ──────
        # Cap each list so the dashboard query stays fast.
        latest_threads = (
            db.query(CompetitorThread)
            .order_by(CompetitorThread.updated_at.desc())
            .limit(5).all()
        )
        threads_summary = [{
            "id": t.id,
            "competitor_id": t.competitor_id,
            "title": t.title,
            "headline": t.headline,
            "confidence": t.confidence,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        } for t in latest_threads]

        # ── News-feed counts for the dashboard widget ────────────────
        tracked_competitor_ids = [
            r[0] for r in
            db.query(ProposalCompetitor.competitor_id)
            .filter(ProposalCompetitor.proposal_id == proposal_id).all()
        ]
        from sqlalchemy import or_ as _or
        news_filter = [CompetitorNewsItem.proposal_id == proposal_id]
        if tracked_competitor_ids:
            news_filter.append(CompetitorNewsItem.competitor_id.in_(tracked_competitor_ids))
        total_news = (
            db.query(sa_func.count(CompetitorNewsItem.id))
            .filter(_or(*news_filter)).scalar() or 0
        )
        news_last_7d = (
            db.query(sa_func.count(CompetitorNewsItem.id))
            .filter(_or(*news_filter))
            .filter(CompetitorNewsItem.fetched_at >= datetime.utcnow() - timedelta(days=7))
            .scalar() or 0
        )
        news_summary = {
            "total": total_news,
            "last_7d": news_last_7d,
            "tracked_competitor_count": len(tracked_competitor_ids),
        }

        # ── Risk score: simple weighted of [days_until_due, % verified,
        # open_questions, missed_events] — frontend can map this to a gauge.
        days_until_due = None
        if p.due_date:
            days_until_due = int((p.due_date - datetime.utcnow()).total_seconds() // 86400)
        # Build list of overdue/missed events with their details.
        # An event is overdue if: explicitly marked "missed", OR its date
        # has passed AND it's not in a terminal state (complete/cancelled).
        # This means upcoming AND in_progress past-due events both count.
        now_iso = datetime.utcnow().isoformat()
        terminal_statuses = {"complete", "cancelled"}
        missed_event_details = [
            e for e in events
            if e.get("status") == "missed"
            or (e.get("status") not in terminal_statuses
                and e.get("event_date")
                and e["event_date"] < now_iso)
        ]

        risk_factors = []
        if days_until_due is not None and days_until_due < 14:
            risk_factors.append({
                "key": "due_date", "severity": "high" if days_until_due < 7 else "medium",
                "label": f"Proposal due in {days_until_due} day(s)",
                "detail": f"Due date: {p.due_date.strftime('%Y-%m-%d')}",
                "action": {"path": "/schedule", "label": "Open Schedule"},
            })
        if reqs and (verified / len(reqs)) < 0.5:
            pct = int((verified / len(reqs)) * 100)
            risk_factors.append({
                "key": "compliance_coverage",
                "severity": "high" if pct < 25 else "medium",
                "label": f"Only {pct}% of requirements verified",
                "detail": f"{verified} of {len(reqs)} requirements have been verified.",
                "action": {"path": "/compliance-matrix", "label": "Open Compliance Matrix"},
            })
        if missed_event_details:
            event_lines = []
            for me in missed_event_details[:10]:
                line = f"• {me.get('label', '(unlabeled)')}"
                if me.get("event_date"):
                    line += f" — due {me['event_date'][:10]}"
                event_lines.append(line)
            risk_factors.append({
                "key": "missed_events", "severity": "high",
                "label": f"{len(missed_event_details)} schedule event(s) overdue or missed",
                "detail": "\n".join(event_lines),
                "items": missed_event_details[:10],
                "action": {"path": "/schedule", "label": "Open Schedule"},
            })
        critical_open_q = sum(1 for q in q_rows if q.priority == "critical"
                              and q.status in ("draft", "reviewed"))
        if critical_open_q > 0:
            risk_factors.append({
                "key": "critical_questions", "severity": "medium",
                "label": f"{critical_open_q} critical question(s) unsubmitted",
                "detail": f"{critical_open_q} critical-priority questions are still in draft or reviewed status and have not been submitted to the agency.",
                "action": {"path": "/questions", "label": "Open Questions"},
            })
        # Parsons-coverage gap risk — split into TRUE evidence gaps
        # (need past performance) vs requirements that just need a
        # commitment, acknowledgment, form, or event attendance. The
        # latter aren't really "coverage problems" — they're response
        # actions we'll handle at proposal-write time.
        if reqs and gap_evidence_required > 0:
            gap_pct = int((gap_evidence_required / len(reqs)) * 100)
            risk_factors.append({
                "key": "parsons_evidence_gap",
                "severity": "high" if gap_pct >= 25 else "medium",
                "label": f"{gap_evidence_required} requirement(s) need Parsons evidence ({gap_pct}%)",
                "detail": (
                    f"{gap_evidence_required} requirements need past performance, "
                    f"SOPs, capability statements, or other Parsons evidence to substantiate "
                    f"our response. Upload more reference content or write evidence "
                    f"directly into the response."
                ),
                "action": {"path": "/parsons-knowledge", "label": "Open Parsons Knowledge"},
            })
        # Pending commitments etc — informational, not a "risk" but
        # surfaced so users know there's still work pending.
        pending_non_evidence = (gap_commitment_only + gap_acknowledgment_only
                                  + gap_form_to_complete + gap_event_attendance)
        if reqs and pending_non_evidence > 0:
            risk_factors.append({
                "key": "pending_commitments",
                "severity": "low",
                "label": f"{pending_non_evidence} pending commitment(s)/form(s)/event(s)",
                "detail": (
                    f"{gap_commitment_only} commitment(s), "
                    f"{gap_acknowledgment_only} acknowledgment(s), "
                    f"{gap_form_to_complete} form(s) to complete, "
                    f"{gap_event_attendance} event attendance(s). "
                    f"These don't need past performance — they need a "
                    f"checkbox response at proposal-write time."
                ),
                "action": {"path": "/compliance-matrix", "label": "Open Compliance Matrix"},
            })

        return {
            "proposal": _aggregate_portfolio_card(db, p),
            "schedule_events": events,
            "requirements": {
                "total": len(reqs),
                "verified": verified,
                "by_priority": by_priority,
                "by_category": by_category,
                "by_compliance": by_compliance,
                "by_parsons_coverage": by_parsons_coverage,
                "by_response_disposition": by_response_disposition,
                "gap_by_disposition": {
                    "evidence_required":   gap_evidence_required,
                    "commitment_only":     gap_commitment_only,
                    "acknowledgment_only": gap_acknowledgment_only,
                    "form_to_complete":    gap_form_to_complete,
                    "event_attendance":    gap_event_attendance,
                    "informational_only":  gap_informational_only,
                    "unclassified":        gap_unclassified,
                },
            },
            "questions": {
                "total": len(q_rows),
                "by_priority": questions_by_priority,
                "by_status": questions_by_status,
            },
            "documents": doc_inventory,
            "competitor_watch": {"latest_threads": threads_summary},
            "news_summary": news_summary,
            "risk_factors": risk_factors,
        }
    except (ResourceNotFoundError, AuthorizationError):
        raise
    except SQLAlchemyError as e:
        logger.error(f"Database error in dashboard summary {proposal_id}: {e}")
        raise DatabaseError("Failed to load dashboard summary")


# ── New-RFP wizard payload ──────────────────────────────────────────

class WizardCreateBody(BaseModel):
    title: str
    rfp_reference: Optional[str] = None
    solicitation_number: Optional[str] = None
    issuing_agency: Optional[str] = None
    due_date: Optional[datetime] = None
    capture_lead: Optional[str] = None
    win_probability: Optional[int] = None
    target_value_usd: Optional[float] = None
    description: Optional[str] = None


@router.post("/wizard")
def create_proposal_via_wizard(
    body: WizardCreateBody = Body(...),
    current_user: User = Depends(get_current_vendor_or_manager),
    db: Session = Depends(get_db),
):
    """Create a new proposal from the portfolio's Add-RFP wizard.

    Differences from the older POST /create:
      * Accepts a JSON body (not query params) so the wizard form maps cleanly.
      * Auto-generates a stable slug from the title for friendly URLs.
      * Pre-creates the same 13 standard sections so authoring works
        immediately without an extra round-trip.
      * Returns the full new portfolio-card payload so the frontend can
        navigate straight into the new RFP's workspace.
    """
    try:
        title = (body.title or "").strip()
        if not title:
            raise ValidationError("Title is required")

        # Generate a unique slug; suffix with timestamp on collision so we
        # never block the wizard on a duplicate name.
        slug = _slugify(title)
        if db.query(Proposal).filter(Proposal.slug == slug).first():
            slug = f"{slug}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

        proposal = Proposal(
            vendor_id=current_user.id,
            title=title,
            slug=slug,
            rfp_reference=(body.rfp_reference or title)[:255],
            solicitation_number=body.solicitation_number,
            issuing_agency=body.issuing_agency,
            due_date=body.due_date,
            status="draft",
            capture_lead=body.capture_lead or current_user.username,
            win_probability=body.win_probability,
            target_value_usd=body.target_value_usd,
            description=body.description,
        )
        db.add(proposal)
        db.flush()

        # Create the standard 13 sections so authoring is unblocked.
        sections = [
            ("vendorLegal", "Vendor Legal & Registration"),
            ("certifications", "Mandatory Certifications & Forms"),
            ("technicalProposal", "Technical Proposal"),
            ("projectManagement", "Project Management & Schedule"),
            ("operations", "Operations & Facilities"),
            ("technology", "Technology / VIIS"),
            ("staffing", "Staffing & Training"),
            ("customerService", "Customer Service & Public Information"),
            ("security", "Security, Audit & Compliance"),
            ("smallBusiness", "Small Business Subcontracting"),
            ("experience", "Past Performance & Experience"),
            ("costProposal", "Cost Proposal"),
            ("transition", "Contract Close-Out & Transition"),
        ]
        for sec_id, sec_name in sections:
            db.add(ProposalSection(proposal_id=proposal.id, section_id=sec_id,
                                   section_name=sec_name, content="",
                                   refined_content="", status="draft"))
        db.commit()
        db.refresh(proposal)

        logger.info(f"Wizard created proposal {proposal.id} ({slug}) for "
                    f"{current_user.username}")
        return _aggregate_portfolio_card(db, proposal)
    except (ValidationError, AuthorizationError):
        raise
    except SQLAlchemyError as e:
        db.rollback()
        logger.error(f"Database error creating proposal via wizard: {e}")
        raise DatabaseError("Failed to create proposal")


# ─────────────────────────────────────────────────────────────────────
# Targeted competitors per proposal (many-to-many) — drives the news
# feed's dynamic competitor list and downstream intel scoping.
# ─────────────────────────────────────────────────────────────────────

class TargetCompetitorBody(BaseModel):
    competitor_id: int
    relevance: Optional[str] = "tracking"  # primary | tracking | ruled_out
    notes: Optional[str] = None


@router.get("/{proposal_id}/competitors")
def list_proposal_competitors(
    proposal_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Return every competitor the capture team is tracking on this proposal."""
    rows = (db.query(ProposalCompetitor)
            .filter(ProposalCompetitor.proposal_id == proposal_id)
            .order_by(ProposalCompetitor.relevance, ProposalCompetitor.created_at)
            .all())
    if not rows:
        return {"proposal_id": proposal_id, "count": 0, "competitors": []}
    comps = {c.id: c for c in
             db.query(Competitor)
             .filter(Competitor.id.in_([r.competitor_id for r in rows]))
             .all()}
    out = []
    for r in rows:
        comp = comps.get(r.competitor_id)
        if not comp:
            continue
        out.append({
            "id": r.id,
            "competitor_id": comp.id,
            "competitor_name": comp.name,
            "competitor_website": comp.website,
            "relevance": r.relevance,
            "notes": r.notes,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return {"proposal_id": proposal_id, "count": len(out), "competitors": out}


@router.post("/{proposal_id}/competitors")
def add_proposal_competitor(
    proposal_id: int,
    body: TargetCompetitorBody,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Add a competitor to this proposal's tracked list."""
    proposal = db.query(Proposal).filter(Proposal.id == proposal_id).first()
    if not proposal:
        raise ResourceNotFoundError("Proposal", proposal_id)
    comp = db.query(Competitor).filter(Competitor.id == body.competitor_id).first()
    if not comp:
        raise HTTPException(404, f"Competitor {body.competitor_id} not found")
    existing = (db.query(ProposalCompetitor)
                .filter(ProposalCompetitor.proposal_id == proposal_id,
                        ProposalCompetitor.competitor_id == body.competitor_id).first())
    if existing:
        # Idempotent: just update the relevance/notes.
        existing.relevance = body.relevance or existing.relevance
        if body.notes is not None:
            existing.notes = body.notes
        db.commit()
        db.refresh(existing)
        return {"id": existing.id, "competitor_id": comp.id, "competitor_name": comp.name,
                "relevance": existing.relevance, "status": "updated"}
    row = ProposalCompetitor(
        proposal_id=proposal_id, competitor_id=body.competitor_id,
        relevance=body.relevance or "tracking",
        notes=body.notes,
        added_by=getattr(current_user, "id", None),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "competitor_id": comp.id, "competitor_name": comp.name,
            "relevance": row.relevance, "status": "added"}


@router.delete("/{proposal_id}/competitors/{competitor_id}")
def remove_proposal_competitor(
    proposal_id: int,
    competitor_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Remove a competitor from this proposal's tracked list. The competitor
    record itself is preserved (still visible globally on the Intel page)."""
    row = (db.query(ProposalCompetitor)
           .filter(ProposalCompetitor.proposal_id == proposal_id,
                   ProposalCompetitor.competitor_id == competitor_id).first())
    if not row:
        raise HTTPException(404, "Competitor is not tracked on this proposal")
    db.delete(row)
    db.commit()
    return {"removed": competitor_id}


# ─────────────────────────────────────────────────────────────────────
# News feed (RFP-specific + targeted competitors)
# ─────────────────────────────────────────────────────────────────────

@router.get("/{proposal_id}/news")
def list_proposal_news(
    proposal_id: int,
    limit: int = 50,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Unified news feed for a proposal: RFP-specific items + items tagged
    to any competitor on this proposal's tracked list. Most recent first.
    """
    proposal = db.query(Proposal).filter(Proposal.id == proposal_id).first()
    if not proposal:
        raise ResourceNotFoundError("Proposal", proposal_id)

    pcs = (db.query(ProposalCompetitor.competitor_id)
           .filter(ProposalCompetitor.proposal_id == proposal_id).all())
    competitor_ids = [r[0] for r in pcs]

    from sqlalchemy import or_
    cond = [CompetitorNewsItem.proposal_id == proposal_id]
    if competitor_ids:
        cond.append(CompetitorNewsItem.competitor_id.in_(competitor_ids))
    rows = (db.query(CompetitorNewsItem)
            .filter(or_(*cond))
            .order_by(
                CompetitorNewsItem.published_at.desc().nullslast(),
                CompetitorNewsItem.fetched_at.desc(),
            )
            .limit(min(max(limit, 1), 200))
            .all())

    # Build competitor name lookup once for the response payload.
    comp_names: Dict[int, str] = {}
    if competitor_ids:
        comp_names = {c.id: c.name for c in
                      db.query(Competitor).filter(Competitor.id.in_(competitor_ids)).all()}

    items = []
    for r in rows:
        items.append({
            "id": r.id,
            "title": r.title,
            "url": r.url,
            "source": r.source,
            "summary": r.summary,
            "published_at": r.published_at.isoformat() if r.published_at else None,
            "fetched_at": r.fetched_at.isoformat() if r.fetched_at else None,
            "scope": "rfp" if r.proposal_id else "competitor",
            "competitor_id": r.competitor_id,
            "competitor_name": comp_names.get(r.competitor_id) if r.competitor_id else None,
            "query_used": r.query_used,
        })
    return {"proposal_id": proposal_id, "count": len(items), "items": items}


@router.post("/{proposal_id}/news/refresh")
def refresh_proposal_news(
    proposal_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Manually refresh the news feed for a proposal — pulls RFP-specific
    news (solicitation # + agency + title) and re-pulls news for every
    targeted competitor."""
    from ..services.competitor_news import refresh_news_for_proposal
    result = refresh_news_for_proposal(db, proposal_id)
    if isinstance(result, dict) and result.get("error"):
        raise HTTPException(404, result["error"])
    return result


@router.delete("/{proposal_id}/news/{news_id}")
def hide_proposal_news_item(
    proposal_id: int,
    news_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Hide a single news item from this proposal's feed (delete the row)."""
    row = (db.query(CompetitorNewsItem)
           .filter(CompetitorNewsItem.id == news_id).first())
    if not row:
        raise HTTPException(404, "News item not found")
    # Only allow deletion of items actually scoped to this proposal OR a
    # competitor on its tracked list — don't let a vendor delete another
    # proposal's data through this endpoint.
    if row.proposal_id and row.proposal_id != proposal_id:
        raise HTTPException(403, "News item is scoped to a different proposal")
    if row.competitor_id:
        tracked = (db.query(ProposalCompetitor)
                   .filter(ProposalCompetitor.proposal_id == proposal_id,
                           ProposalCompetitor.competitor_id == row.competitor_id).first())
        if not tracked:
            raise HTTPException(403, "News item belongs to an untracked competitor")
    db.delete(row)
    db.commit()
    return {"removed": news_id}


@router.get("/{proposal_id}")
def get_proposal(
    proposal_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get proposal details"""
    try:
        logger.info(f"Fetching proposal {proposal_id} for user {current_user.username}")

        proposal = db.query(Proposal).filter(Proposal.id == proposal_id).first()
        if not proposal:
            logger.warning(f"Proposal not found: {proposal_id}")
            raise ResourceNotFoundError("Proposal", proposal_id)

        # Check permissions: users can only see their own proposals or if they're managers
        if current_user.role not in ["admin", "proposal_manager"] and proposal.vendor_id != current_user.id:
            logger.warning(f"Access denied for proposal {proposal_id} by user {current_user.username}")
            raise AuthorizationError("Not authorized to view this proposal")

        sections = db.query(ProposalSection).filter(ProposalSection.proposal_id == proposal_id).all()

        logger.info(f"Proposal {proposal_id} retrieved successfully")
        return {
            "proposal": {
                "id": proposal.id,
                "title": proposal.title,
                "rfp_reference": proposal.rfp_reference,
                "status": proposal.status,
                "submission_date": proposal.submission_date,
                "created_at": proposal.created_at,
                "updated_at": proposal.updated_at
            },
            "sections": [{
                "id": section.id,
                "section_id": section.section_id,
                "section_name": section.section_name,
                "content": section.content,
                "refined_content": section.refined_content,
                "status": section.status,
                "conflicts": section.conflicts,
                "score": section.score,
                "evaluator_notes": section.evaluator_notes
            } for section in sections]
        }
    except SQLAlchemyError as e:
        logger.error(f"Database error fetching proposal {proposal_id}: {str(e)}")
        raise DatabaseError("Failed to fetch proposal")
    except (ResourceNotFoundError, AuthorizationError):
        raise
    except Exception as e:
        logger.error(f"Unexpected error fetching proposal {proposal_id}: {str(e)}")
        raise DatabaseError("Failed to fetch proposal")

@router.put("/{proposal_id}/section/{section_id}")
def update_proposal_section(
    proposal_id: int,
    section_id: str,
    content: str = None,
    refined_content: str = None,
    status: str = None,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Update a proposal section"""
    try:
        logger.info(f"Updating section {section_id} for proposal {proposal_id} by user {current_user.username}")

        # Check proposal ownership
        proposal = db.query(Proposal).filter(Proposal.id == proposal_id).first()
        if not proposal:
            logger.warning(f"Proposal not found: {proposal_id}")
            raise ResourceNotFoundError("Proposal", proposal_id)

        if current_user.role not in ["admin", "proposal_manager"] and proposal.vendor_id != current_user.id:
            logger.warning(f"Access denied for proposal {proposal_id} by user {current_user.username}")
            raise AuthorizationError("Not authorized to modify this proposal")

        section = db.query(ProposalSection).filter(
            ProposalSection.proposal_id == proposal_id,
            ProposalSection.section_id == section_id
        ).first()

        if not section:
            logger.warning(f"Section not found: {section_id} for proposal {proposal_id}")
            raise ResourceNotFoundError("Section", section_id)

        # Validate status if provided
        if status is not None and status not in ["draft", "refined", "approved", "needs_review"]:
            raise ValidationError("Invalid status value")

        if content is not None:
            section.content = content
        if refined_content is not None:
            section.refined_content = refined_content
        if status is not None:
            section.status = status

        section.updated_at = datetime.utcnow()
        db.commit()

        logger.info(f"Section {section_id} updated successfully for proposal {proposal_id}")
        return {"message": "Section updated"}

    except (ResourceNotFoundError, AuthorizationError, ValidationError):
        raise
    except SQLAlchemyError as e:
        db.rollback()
        logger.error(f"Database error updating section: {str(e)}")
        raise DatabaseError("Failed to update section")
    except Exception as e:
        db.rollback()
        logger.error(f"Unexpected error updating section: {str(e)}")
        raise DatabaseError("Failed to update section")

@router.post("/{proposal_id}/submit")
def submit_proposal(
    proposal_id: int,
    current_user: User = Depends(get_current_vendor_or_manager),
    db: Session = Depends(get_db)
):
    """Submit proposal for evaluation"""
    try:
        logger.info(f"Submitting proposal {proposal_id} by user {current_user.username}")

        proposal = db.query(Proposal).filter(Proposal.id == proposal_id).first()
        if not proposal:
            logger.warning(f"Proposal not found: {proposal_id}")
            raise ResourceNotFoundError("Proposal", proposal_id)

        # Check ownership
        if current_user.role not in ["admin", "proposal_manager"] and proposal.vendor_id != current_user.id:
            logger.warning(f"Access denied for proposal {proposal_id} by user {current_user.username}")
            raise AuthorizationError("Not authorized to submit this proposal")

        # Check if proposal is already submitted
        if proposal.status == "submitted":
            raise ValidationError("Proposal is already submitted")

        # Check if all required sections are complete
        sections = db.query(ProposalSection).filter(ProposalSection.proposal_id == proposal_id).all()

        # Pass/fail sections must be approved
        pass_fail_sections = ["vendorLegal", "certifications"]
        incomplete_sections = []

        for section in sections:
            if section.section_id in pass_fail_sections and section.status != "approved":
                incomplete_sections.append(section.section_name)

        if incomplete_sections:
            raise ValidationError(f"Required sections must be approved before submission: {', '.join(incomplete_sections)}")

        proposal.status = "submitted"
        proposal.submission_date = datetime.utcnow()
        db.commit()

        logger.info(f"Proposal {proposal_id} submitted successfully")
        return {"message": "Proposal submitted successfully"}

    except (ResourceNotFoundError, AuthorizationError, ValidationError):
        raise
    except SQLAlchemyError as e:
        db.rollback()
        logger.error(f"Database error submitting proposal: {str(e)}")
        raise DatabaseError("Failed to submit proposal")
    except Exception as e:
        db.rollback()
        logger.error(f"Unexpected error submitting proposal: {str(e)}")
        raise DatabaseError("Failed to submit proposal")

@router.get("/")
def list_proposals(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """List proposals with optional filtering"""
    try:
        logger.info(f"Listing proposals for user {current_user.username} (role: {current_user.role})")

        query = db.query(Proposal)

        # Filter based on user role
        if current_user.role == "vendor":
            query = query.filter(Proposal.vendor_id == current_user.id)
        # Managers and admins can see all proposals

        proposals = query.all()

        logger.info(f"Retrieved {len(proposals)} proposals for user {current_user.username}")
        return [{
            "id": p.id,
            "slug": p.slug,
            "title": p.title,
            "rfp_reference": p.rfp_reference,
            "solicitation_number": p.solicitation_number,
            "issuing_agency": p.issuing_agency,
            "due_date": p.due_date,
            "status": p.status,
            "submission_date": p.submission_date,
            "capture_lead": p.capture_lead,
            "win_probability": p.win_probability,
            "target_value_usd": p.target_value_usd,
            "description": p.description,
            "created_at": p.created_at,
            "updated_at": p.updated_at,
        } for p in proposals]

    except SQLAlchemyError as e:
        logger.error(f"Database error listing proposals: {str(e)}")
        raise DatabaseError("Failed to retrieve proposals")
    except Exception as e:
        logger.error(f"Unexpected error listing proposals: {str(e)}")
        raise DatabaseError("Failed to retrieve proposals")