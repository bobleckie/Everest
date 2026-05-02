"""Dashboard KPI endpoints — richer than /orchestrator/stats."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta

from ..database import get_db
from ..models import (
    Document, WorkflowData, Proposal, ProposalSection, SectionScore,
    ScoringRubricSection, ScoringRubric, CompetitorNewsItem, Competitor, User,
)
from ..auth import get_current_user

router = APIRouter()


@router.get("/kpis")
def get_dashboard_kpis(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Rich KPI payload for the Dashboard page."""

    # ── Basic counts ─────────────────────────────────────────────────
    total_documents = db.query(Document).count()
    total_proposals = db.query(Proposal).count()

    # By status breakdown
    proposals = db.query(Proposal).all()
    status_counts = {}
    for p in proposals:
        status_counts[p.status] = status_counts.get(p.status, 0) + 1

    active_proposals = sum(v for k, v in status_counts.items() if k in ('draft', 'under_review'))

    # ── Workflow counts ──────────────────────────────────────────────
    active_workflows = (
        db.query(WorkflowData)
        .filter(WorkflowData.status.in_(["pending", "in_progress"]))
        .count()
    )
    completed_workflows = (
        db.query(WorkflowData)
        .filter(WorkflowData.status.in_(["completed", "approved"]))
        .count()
    )
    pending_approvals = (
        db.query(WorkflowData)
        .filter(WorkflowData.approval_level == 0)
        .filter(WorkflowData.status == "pending")
        .count()
    )

    # ── Scoring / Quality ────────────────────────────────────────────
    # Average Parsons score across all scored sections (all proposals)
    parsons_scores = (
        db.query(func.avg(SectionScore.score))
        .filter(SectionScore.scorer_type == "parsons")
        .scalar()
    )
    avg_parsons_score = round(parsons_scores, 1) if parsons_scores else 0

    # Per-proposal completion % (sections with content / total sections)
    rubric = db.query(ScoringRubric).filter(ScoringRubric.is_default == True).first()
    total_rubric_sections = 0
    if rubric:
        total_rubric_sections = db.query(ScoringRubricSection).filter(
            ScoringRubricSection.rubric_id == rubric.id
        ).count()

    # Avg completion across proposals
    proposal_completions = []
    for p in proposals:
        sections = db.query(ProposalSection).filter(ProposalSection.proposal_id == p.id).all()
        filled = sum(1 for s in sections if s.content and s.content.strip())
        denom = total_rubric_sections if total_rubric_sections > 0 else 13
        proposal_completions.append(round(filled / denom * 100, 1))
    avg_completion = round(sum(proposal_completions) / len(proposal_completions), 1) if proposal_completions else 0

    # ── Pipeline funnel ──────────────────────────────────────────────
    funnel = {
        "drafting": status_counts.get("draft", 0),
        "under_review": status_counts.get("under_review", 0),
        "approved": status_counts.get("approved", 0),
        "submitted": status_counts.get("submitted", 0),
        "rejected": status_counts.get("rejected", 0),
    }

    # ── Proposals in flight ──────────────────────────────────────────
    proposals_in_flight = []
    for p in proposals:
        if p.status in ("draft", "under_review"):
            sections = db.query(ProposalSection).filter(ProposalSection.proposal_id == p.id).all()
            filled = sum(1 for s in sections if s.content and s.content.strip())
            denom = total_rubric_sections if total_rubric_sections > 0 else 13
            pct = round(filled / denom * 100, 1)

            # Avg parsons score for this proposal
            p_scores = (
                db.query(func.avg(SectionScore.score))
                .filter(SectionScore.proposal_id == p.id, SectionScore.scorer_type == "parsons")
                .scalar()
            )
            proposals_in_flight.append({
                "id": p.id,
                "title": p.title or f"Proposal #{p.id}",
                "status": p.status,
                "completion_pct": pct,
                "avg_score": round(p_scores, 1) if p_scores else None,
                "due_date": p.submission_date.isoformat() if p.submission_date else None,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            })

    # ── Section quality heatmap data ─────────────────────────────────
    heatmap = []
    if rubric:
        rubric_secs = db.query(ScoringRubricSection).filter(
            ScoringRubricSection.rubric_id == rubric.id
        ).order_by(ScoringRubricSection.sort_order).all()
        for p in proposals:
            row = {"proposal_id": p.id, "title": p.title or f"Proposal #{p.id}", "sections": {}}
            for rs in rubric_secs:
                score = (
                    db.query(SectionScore.score)
                    .filter(SectionScore.proposal_id == p.id, SectionScore.section_id == rs.section_id, SectionScore.scorer_type == "parsons")
                    .scalar()
                )
                row["sections"][rs.section_id] = score
            heatmap.append(row)

    # ── Recent activity (latest workflow steps + scored events) ──────
    recent = []
    recent_wf = (
        db.query(WorkflowData)
        .order_by(WorkflowData.id.desc())
        .limit(5)
        .all()
    )
    for w in recent_wf:
        recent.append({
            "type": "workflow",
            "label": f"{w.step or 'Step'} ({w.status})",
            "detail": w.workflow_type or "",
            "id": w.id,
        })

    recent_scores = (
        db.query(SectionScore)
        .order_by(SectionScore.scored_at.desc())
        .limit(5)
        .all()
    )
    for s in recent_scores:
        recent.append({
            "type": "score",
            "label": f"Scored {s.section_id}: {s.score}/100",
            "detail": f"{s.scorer_type}" + (f" ({s.scorer_name})" if s.scorer_name else ""),
            "id": s.id,
        })

    # ── Competitor news ticker ───────────────────────────────────────
    watchlisted_ids = [c.id for c in db.query(Competitor).filter(Competitor.watchlist == True).all()]
    news_items = []
    if watchlisted_ids:
        items = (
            db.query(CompetitorNewsItem)
            .filter(CompetitorNewsItem.competitor_id.in_(watchlisted_ids))
            .order_by(CompetitorNewsItem.fetched_at.desc())
            .limit(10)
            .all()
        )
        comp_map = {c.id: c.name for c in db.query(Competitor).all()}
        for n in items:
            news_items.append({
                "id": n.id,
                "competitor": comp_map.get(n.competitor_id, "Unknown"),
                "title": n.title,
                "url": n.url,
                "source": n.source,
                "published_at": n.published_at.isoformat() if n.published_at else None,
            })

    return {
        "totalDocuments": total_documents,
        "totalProposals": total_proposals,
        "activeProposals": active_proposals,
        "activeWorkflows": active_workflows,
        "completedWorkflows": completed_workflows,
        "pendingApprovals": pending_approvals,
        "avgParsonsScore": avg_parsons_score,
        "avgCompletion": avg_completion,
        "funnel": funnel,
        "proposalsInFlight": proposals_in_flight,
        "heatmap": heatmap,
        "recentActivity": recent,
        "competitorNews": news_items,
    }
