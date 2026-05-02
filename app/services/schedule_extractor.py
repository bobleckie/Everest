"""
Schedule extractor — reads an ingested RFP / Notice of Bid Solicitation
and extracts all date-bearing key events (proposal due date, pre-bid
conference, Q&A cutoff, addenda cutoff, site visit, period of performance,
award notification, etc.) into structured RfpScheduleEvent rows.

Design mirrors orchestrator_agent's requirement extractor:
  - reuses _call_ai (SSL-aware, DB-driven provider/model config)
  - reuses _salvage_json_objects for robust JSON recovery
  - idempotent per (document_id, event_type, event_date): re-extraction
    updates existing rows instead of inserting duplicates

Also derives internal working-backwards deadlines from the extracted
proposal_due date when the proposal_id is known (red team, SME sign-off,
pricing final, production). Derived events are marked extracted_by_ai=False,
event_type prefix "internal_".
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy.orm import Session

from ..models import (
    DocumentChunk, IngestedDocument, Proposal, RfpScheduleEvent,
)
from .orchestrator_agent import _call_ai, _salvage_json_objects

logger = logging.getLogger(__name__)

# ── Canonical event types (the AI is asked to use these) ─────────────

CANONICAL_EVENT_TYPES = [
    "question_period_start",
    "question_period_end",
    "pre_bid_conference",
    "mandatory_site_visit",
    "addenda_cutoff",
    "proposal_due",
    "bid_opening",
    "evaluation_period_start",
    "evaluation_period_end",
    "bafo_due",
    "award_notification",
    "contract_start",
    "period_of_performance_start",
    "period_of_performance_end",
    # Post-award delivery / implementation milestones the contractor
    # must hit (e.g. "operational by X", "go-live phase 2 by Y",
    # "transition complete within 90 days of contract start").
    "implementation_milestone",
    "other",
]

# Working-backwards defaults: business days BEFORE proposal_due.
# Configurable later via settings.
DERIVED_INTERNAL_DEADLINES = [
    ("internal_production",      1,  "Final production & packaging"),
    ("internal_pricing_final",   3,  "Pricing locked & reviewed"),
    ("internal_red_team",        7,  "Red-team review complete"),
    ("internal_sme_signoff",    10,  "SME sign-off on technical volume"),
    ("internal_review",         14,  "First internal review pass"),
]


# ── AI prompt ────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are a procurement specialist extracting the schedule of key events from a Notice of Bid Solicitation or RFP.

Return ONLY valid JSON (an array). No prose, no markdown fences.

Each object describes one scheduled event, milestone, or deadline. Include
BOTH the procurement-process events (Q&A, site visit, proposal due, etc.)
AND the post-award contractor delivery / implementation milestones the RFP
specifies (e.g. "Phase 2 operational within 120 days of contract start",
"transition plan complete by X", "go-live by Y"). The full timeline matters.

Include:
  event_type      — one of: question_period_start, question_period_end,
                    pre_bid_conference, mandatory_site_visit, addenda_cutoff,
                    proposal_due, bid_opening, evaluation_period_start,
                    evaluation_period_end, bafo_due, award_notification,
                    contract_start, period_of_performance_start,
                    period_of_performance_end, implementation_milestone,
                    other
                    Use "implementation_milestone" for any contractor
                    delivery deadline (transition plan due, system go-live,
                    operational by, training complete by, phase X
                    operational, etc.). These are AS-IMPORTANT as the
                    procurement events but are often missed.
  label           — human-readable name (e.g. "Proposal Due Date",
                    "Mandatory Site Visit — Bakers Basin")
  event_date      — ISO 8601 datetime ("2024-11-15T14:00:00") or date-only
                    ("2024-11-15"). REQUIRED if you can parse it. Omit if truly unknown.
  end_date        — ISO 8601, for date ranges (e.g. Q&A period end, period of
                    performance end). Omit if not a range.
  is_mandatory    — true for mandatory pre-bid/site-visit/submission dates, false otherwise
  source_page     — page number in the document where this appears (integer)
  source_text     — short verbatim excerpt (<= 300 chars) containing the date
  notes           — brief context (e.g. "At 2:00 PM ET via NJSTART", "attendance required",
                    "contract term 5 years with 3 one-year extensions")
  confidence      — "high" if a clear date is stated; "medium" if inferred from context;
                    "low" if the date is placeholder-like (e.g. "TBD") or ambiguous
  offset_days     — integer N for deadlines expressed RELATIVE to a contract
                    anchor instead of as a calendar date. Use this when the
                    RFP says things like "Start + 30 days", "30 calendar days
                    after Contract Effective Date", "End - 360d", etc.
                    Negative values are valid (e.g. closeout plan 360 days
                    BEFORE Period-of-Performance End). When you set
                    offset_days, OMIT event_date — it gets computed once
                    the contract start date is known.
  offset_anchor   — required if offset_days is set. One of:
                    "contract_start" (the default for "Start + Nd",
                    "Contract Effective Date + Nd")
                    "pop_end" (for "End - Nd")
  is_draft_with_quote — true if the RFP requires a draft of this plan to
                    be submitted WITH the proposal (often shown in a
                    "Draft Plan Required with Quote Submission" column).
  section_refs    — comma-separated RFP section numbers that govern this
                    deliverable (e.g. "4.2.1, 4.29.3, 3.18"). Omit if
                    not specified.

Include every distinct event you can find — multiple site visits, multiple Q&A windows,
multiple addenda cutoffs, etc. are all separate objects. ALSO include every plan /
deliverable in any "Additional Plans" / "Deliverables" / "Implementation Schedule"
table — Mobilization Plan, Transition Plan, QA Plan, Security Plan, etc. — each as
a separate `implementation_milestone` object with offset_days set.

If the document says a date is on a cover sheet / attachment / amendment but does not
contain it inline, include the event with event_date omitted and confidence "low",
so the user knows to fill it in manually.

Do NOT invent dates. Do NOT include duplicates. Do NOT include events that are not
tied to this specific procurement (skip historical program dates unless they define
the period of performance).

Output format:
[
  {"event_type": "...", "label": "...", "event_date": "...", ...},
  ...
]
"""


# ── Helpers ──────────────────────────────────────────────────────────

def _parse_iso_datetime(s: str) -> Optional[datetime]:
    """Parse ISO date or datetime; return None on failure."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    # Common model outputs
    for fmt in (
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    # Fallback: strip timezone suffix (Z, +00:00, etc.)
    try:
        cleaned = re.sub(r"(Z|[+-]\d{2}:?\d{2})$", "", s)
        return datetime.fromisoformat(cleaned)
    except (ValueError, TypeError):
        return None


def _collect_doc_text(db: Session, document_id: int,
                     max_chars: int = 120_000) -> str:
    """Concatenate all chunks of a document with page markers."""
    chunks = (
        db.query(DocumentChunk)
        .filter(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.chunk_index)
        .all()
    )
    parts: List[str] = []
    total = 0
    last_page = None
    for c in chunks:
        pg = c.page_number or 0
        if pg != last_page:
            parts.append(f"\n\n[[page {pg}]]\n")
            last_page = pg
        txt = c.content or ""
        parts.append(txt)
        total += len(txt)
        if total >= max_chars:
            parts.append("\n\n[[... remainder truncated]]")
            break
    return "".join(parts)


# ── Main entrypoint ──────────────────────────────────────────────────

def extract_schedule_events(
    db: Session,
    document_id: int,
    proposal_id: Optional[int] = None,
    replace_existing: bool = True,
) -> dict:
    """
    Run the AI schedule extractor over `document_id`. Stores results as
    RfpScheduleEvent rows. Returns summary dict.
    """
    doc = db.query(IngestedDocument).filter(IngestedDocument.id == document_id).first()
    if not doc:
        raise ValueError(f"Document {document_id} not found")

    text = _collect_doc_text(db, document_id)
    if not text.strip():
        logger.warning(f"Doc {document_id} has no extractable text")
        return {"extracted": 0, "saved": 0, "errors": ["no text"]}

    prompt = (
        f"Document: {doc.original_filename}\n"
        f"Document type: {doc.document_type or doc.source_type or 'rfp'}\n"
        f"Total pages: {doc.total_pages}\n"
        f"-----\n"
        f"{text}\n"
        f"-----\n\n"
        f"Extract every scheduled event / milestone / deadline you can find "
        f"into the JSON array described in the system prompt."
    )

    logger.info(f"[schedule] Calling AI for doc {document_id}...")
    raw = _call_ai(prompt, _SYSTEM_PROMPT, max_tokens=8000)
    logger.info(f"[schedule] AI returned {len(raw)} chars")

    events = _parse_events(raw)
    logger.info(f"[schedule] Parsed {len(events)} events")

    # If replace_existing, wipe prior AI-extracted events for this doc
    if replace_existing:
        deleted = (
            db.query(RfpScheduleEvent)
            .filter(
                RfpScheduleEvent.document_id == document_id,
                RfpScheduleEvent.extracted_by_ai == True,  # noqa: E712
            )
            .delete(synchronize_session=False)
        )
        logger.info(f"[schedule] Deleted {deleted} prior AI-extracted events")

    saved = 0
    errors: List[str] = []
    for ev in events:
        try:
            # Relative-deadline fields (added 2026-04-28). When the RFP
            # expresses a deadline as "Start + Nd", the model returns
            # offset_days + offset_anchor and OMITS event_date.
            offset_days = ev.get("offset_days")
            if not isinstance(offset_days, int):
                offset_days = None
            offset_anchor = (ev.get("offset_anchor") or "").strip().lower() or None
            if offset_anchor not in (None, "contract_start", "pop_end"):
                offset_anchor = None
            # If offset_days is set without an anchor, default to contract_start.
            if offset_days is not None and offset_anchor is None:
                offset_anchor = "contract_start"

            row = RfpScheduleEvent(
                document_id=document_id,
                proposal_id=proposal_id,
                event_type=_normalize_event_type(ev.get("event_type")),
                label=(ev.get("label") or "").strip()[:250] or "(unnamed event)",
                event_date=_parse_iso_datetime(ev.get("event_date")),
                end_date=_parse_iso_datetime(ev.get("end_date")),
                is_mandatory=bool(ev.get("is_mandatory", False)),
                source_page=ev.get("source_page") if isinstance(ev.get("source_page"), int) else None,
                source_text=(ev.get("source_text") or "")[:1000],
                notes=(ev.get("notes") or "")[:2000],
                confidence=(ev.get("confidence") or "medium").lower(),
                extracted_by_ai=True,
                verified=False,
                offset_days=offset_days,
                offset_anchor=offset_anchor,
                is_draft_with_quote=bool(ev.get("is_draft_with_quote", False)),
                section_refs=(ev.get("section_refs") or None),
                event_date_resolved=False,
            )
            db.add(row)
            saved += 1
        except Exception as e:
            errors.append(f"{ev.get('label')}: {e}")

    db.commit()

    # Propagate proposal_due to the Proposal row if we have one
    if proposal_id:
        _update_proposal_due_date(db, proposal_id, document_id)
        derive_internal_deadlines(db, proposal_id)
        # Compute absolute event_date for any rows that came in as offsets
        # (no-op when the proposal doesn't yet have a contract anchor).
        try:
            _resolve_relative_dates(db, proposal_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[schedule] resolver failed for proposal {proposal_id}: {e}")

    return {
        "document_id": document_id,
        "extracted": len(events),
        "saved": saved,
        "errors": errors[:20],
    }


def _parse_events(raw: str) -> List[dict]:
    """Parse the model's response. Accepts fenced JSON, plain JSON array,
    or a blob we have to salvage."""
    if not raw:
        return []
    cleaned = raw.strip()

    # Strip markdown fences
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned)
    if m:
        cleaned = m.group(1).strip()

    # Try to find the first [ ... ] block
    first = cleaned.find("[")
    last = cleaned.rfind("]")
    if first != -1 and last > first:
        candidate = cleaned[first:last + 1]
        try:
            data = json.loads(candidate)
            if isinstance(data, list):
                return [e for e in data if isinstance(e, dict)]
        except json.JSONDecodeError:
            # try trimming trailing commas
            trimmed = re.sub(r",(\s*[}\]])", r"\1", candidate)
            try:
                data = json.loads(trimmed)
                if isinstance(data, list):
                    return [e for e in data if isinstance(e, dict)]
            except json.JSONDecodeError:
                pass

    # Salvage individual {...} blocks
    salvaged = _salvage_json_objects(cleaned)
    return [e for e in salvaged if isinstance(e, dict)]


def _normalize_event_type(t: Optional[str]) -> str:
    if not t:
        return "other"
    t = t.strip().lower().replace("-", "_").replace(" ", "_")
    if t in CANONICAL_EVENT_TYPES:
        return t
    # soft aliases
    aliases = {
        "q_and_a": "question_period_end",
        "qa_end": "question_period_end",
        "qa_cutoff": "question_period_end",
        "question_cutoff": "question_period_end",
        "questions_due": "question_period_end",
        "site_visit": "mandatory_site_visit",
        "pre_bid": "pre_bid_conference",
        "prebid_conference": "pre_bid_conference",
        "pre_quote_conference": "pre_bid_conference",
        "proposal_submission": "proposal_due",
        "proposal_deadline": "proposal_due",
        "bid_due": "proposal_due",
        "quote_due": "proposal_due",
        "quote_opening": "bid_opening",
        "contract_award": "award_notification",
        "contract_term_start": "period_of_performance_start",
        "pop_start": "period_of_performance_start",
        "pop_end": "period_of_performance_end",
    }
    return aliases.get(t, "other")


def _update_proposal_due_date(db: Session, proposal_id: int, document_id: int) -> None:
    """Copy the extracted proposal_due event into Proposal.due_date."""
    evt = (
        db.query(RfpScheduleEvent)
        .filter(
            RfpScheduleEvent.proposal_id == proposal_id,
            RfpScheduleEvent.event_type == "proposal_due",
            RfpScheduleEvent.event_date.isnot(None),
        )
        .order_by(RfpScheduleEvent.event_date.asc())
        .first()
    )
    if not evt:
        return
    p = db.query(Proposal).filter(Proposal.id == proposal_id).first()
    if p:
        p.due_date = evt.event_date
        db.commit()
        logger.info(f"[schedule] Set Proposal {proposal_id}.due_date = {p.due_date}")


# ── Internal working-backwards deadlines ────────────────────────────

def _add_business_days(start: datetime, days: int) -> datetime:
    """Subtract `days` business days (Mon-Fri) from start."""
    d = start
    remaining = days
    while remaining > 0:
        d -= timedelta(days=1)
        if d.weekday() < 5:  # Mon-Fri
            remaining -= 1
    return d


def derive_internal_deadlines(db: Session, proposal_id: int) -> int:
    """Build internal working-backwards deadlines from Proposal.due_date."""
    p = db.query(Proposal).filter(Proposal.id == proposal_id).first()
    if not p or not p.due_date:
        return 0

    # Clear any prior auto-derived rows
    db.query(RfpScheduleEvent).filter(
        RfpScheduleEvent.proposal_id == proposal_id,
        RfpScheduleEvent.extracted_by_ai == False,  # noqa: E712
        RfpScheduleEvent.event_type.like("internal_%"),
    ).delete(synchronize_session=False)

    created = 0
    for event_type, bd_before, label in DERIVED_INTERNAL_DEADLINES:
        d = _add_business_days(p.due_date, bd_before)
        db.add(RfpScheduleEvent(
            proposal_id=proposal_id,
            event_type=event_type,
            label=label,
            event_date=d,
            is_mandatory=True,
            notes=f"Auto-derived: {bd_before} business days before proposal due date",
            confidence="high",
            extracted_by_ai=False,
            verified=False,
            status="upcoming",
        ))
        created += 1
    db.commit()
    return created

# ── Relative-deadline resolver (added 2026-04-28) ────────────────────

def _resolve_relative_dates(db: Session, proposal_id: int) -> int:
    """Compute absolute `event_date` for every event with offset_days set.

    Anchors:
      * `contract_start` -> proposals.contract_effective_date
      * `pop_end`        -> the latest period_of_performance_end event_date
                            for this proposal (best available proxy)

    Idempotent. Only sets event_date when:
      * the row has offset_days + offset_anchor
      * the relevant anchor is known
      * the row's existing event_date was previously offset-resolved
        (event_date_resolved=1) OR was NULL — we never overwrite a date
        the user typed in by hand.

    Returns the number of rows whose event_date was (re)computed.
    """
    p = db.query(Proposal).filter(Proposal.id == proposal_id).first()
    if not p:
        return 0
    anchors: dict[str, datetime] = {}
    if getattr(p, "contract_effective_date", None):
        anchors["contract_start"] = p.contract_effective_date
    pop_end = (
        db.query(RfpScheduleEvent)
        .filter(
            RfpScheduleEvent.proposal_id == proposal_id,
            RfpScheduleEvent.event_type == "period_of_performance_end",
            RfpScheduleEvent.event_date.isnot(None),
        )
        .order_by(RfpScheduleEvent.event_date.desc())
        .first()
    )
    if pop_end:
        anchors["pop_end"] = pop_end.event_date

    if not anchors:
        return 0

    rows = (
        db.query(RfpScheduleEvent)
        .filter(
            RfpScheduleEvent.proposal_id == proposal_id,
            RfpScheduleEvent.offset_days.isnot(None),
            RfpScheduleEvent.offset_anchor.isnot(None),
        )
        .all()
    )
    n = 0
    for r in rows:
        anchor = anchors.get(r.offset_anchor)
        if anchor is None:
            continue
        # Only (re)compute when the existing event_date is missing OR was
        # previously set by the resolver. Don't clobber user-entered dates.
        if r.event_date is not None and not getattr(r, "event_date_resolved", False):
            continue
        new_date = anchor + timedelta(days=int(r.offset_days))
        if r.event_date != new_date:
            r.event_date = new_date
            r.event_date_resolved = True
            n += 1
    if n > 0:
        db.commit()
        logger.info(f"[schedule] Resolved {n} relative dates for proposal {proposal_id}")
    return n