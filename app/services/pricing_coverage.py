"""Pricing Coverage Analysis.

Given a `PricingModel` (our bid's line-item build-up) and a set of extracted
`RfpRequirement`s, determine:

  1. Which requirements are cost-bearing (imply money).
  2. For each cost-bearing requirement, whether the current pricing model
     already has one or more line items covering it.
  3. For requirements that are partially covered or not covered, propose a
     new line item (category, name, unit, allocation, rationale).

All decisions are persisted to `PricingCoverageSuggestion` rows tied to a
`PricingCoverageRun` header. A reviewer accepts or rejects each suggestion
via the API. Accepting a "gap" suggestion creates the proposed line item in
the target pricing model.

This module is intentionally safe to run while other extraction/diff
processes are active — it is read-only against RfpRequirement and only
writes new rows into two new tables (plus optional new line items on
acceptance).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..models import (
    IngestedDocument,
    PricingCategory,
    PricingCoverageRun,
    PricingCoverageSuggestion,
    PricingLineItem,
    PricingModel,
    Proposal,
    RfpRequirement,
)
from .orchestrator_agent import _call_ai

logger = logging.getLogger(__name__)


# ── Cost-bearing filter ──────────────────────────────────────────────
# Requirements whose source_text / description / title contains any of
# these tokens are routed to the LLM coverage pass. Everything else is
# treated as non-cost-bearing and skipped (but still counted).
_COST_KEYWORDS = [
    # Money words
    "price", "cost", "fee", "rate", "$", "dollar", "payment", "pay ",
    "invoice", "charge", "budget", "fund", "compensat",
    # Labor / staffing
    "staff", "fte", "full-time equivalent", "personnel", "employee", "wage",
    "salary", "hourly", "headcount", "position", "shift", "inspector",
    "supervisor", "technician", "training",
    # Equipment / capital
    "equipment", "hardware", "software", "license", "workstation", "pc-based",
    "tablet", "laptop", "device", "sensor", "camera", "printer", "scanner",
    "vehicle", "facility", "lease", "rent",
    # Operations / ODCs
    "maintenance", "warranty", "support", "repair", "replace", "consumable",
    "supply", "supplies", "travel", "shipping", "freight", "logistics",
    "subcontract", "insurance", "bond", "indemnif", "utility", "utilities",
    "communication", "connectivity", "network", "internet", "data plan",
    # IT / systems
    "server", "storage", "hosting", "cloud", "database", "backup", "disaster",
    "security", "cyber", "audit", "compliance program", "certification",
    # Transaction volumes
    "per inspection", "per transaction", "per-txn", "cif", "pif",
    "inspection fee", "transaction fee",
    # Program mgmt
    "project manager", "program manager", "deliverable", "report", "reporting",
    "deliver ", "provide", "furnish", "supply ",
]

# Anchor regex — broad scan, then we still pass through the LLM for adjudication.
_COST_RE = re.compile("|".join(re.escape(k) for k in _COST_KEYWORDS), re.IGNORECASE)


def _is_cost_bearing(req: RfpRequirement) -> bool:
    """Cheap heuristic to pre-filter requirements before LLM analysis."""
    haystack = " ".join([
        req.title or "",
        req.description or "",
        req.source_text or "",
    ])
    if not haystack.strip():
        return False
    return bool(_COST_RE.search(haystack))


# ── Line-item summaries for LLM prompt ───────────────────────────────
def _build_line_item_catalog(db: Session, pricing_model_id: int) -> List[Dict[str, Any]]:
    """Flat list of {id, tab, category, name, unit, allocation_basis,
    description} for every line item in the model — fed to the LLM so it
    can reference them by id when deciding coverage."""
    cats = (
        db.query(PricingCategory)
        .filter(PricingCategory.pricing_model_id == pricing_model_id)
        .order_by(PricingCategory.sort_order, PricingCategory.id)
        .all()
    )
    if not cats:
        return []
    cat_by_id = {c.id: c for c in cats}
    lines = (
        db.query(PricingLineItem)
        .filter(PricingLineItem.category_id.in_([c.id for c in cats]))
        .order_by(PricingLineItem.category_id, PricingLineItem.sort_order, PricingLineItem.id)
        .all()
    )
    out = []
    for l in lines:
        c = cat_by_id.get(l.category_id)
        out.append({
            "id": l.id,
            "tab": (c.tab if c else "") or "",
            "category": (c.name if c else "") or "",
            "name": l.name or "",
            "unit": l.unit or "",
            "allocation_basis": l.allocation_basis or "",
            "description": (l.description or "")[:240],
        })
    return out


def _format_catalog_for_prompt(catalog: List[Dict[str, Any]]) -> str:
    if not catalog:
        return "(no existing line items)"
    lines = []
    for li in catalog:
        lines.append(
            f"  [id={li['id']}] ({li['tab']} / {li['category']}) "
            f"{li['name']} [{li['unit']}, {li['allocation_basis']}]"
            + (f" — {li['description']}" if li['description'] else "")
        )
    return "\n".join(lines)


# ── LLM adjudication ─────────────────────────────────────────────────
_COVERAGE_SYSTEM = (
    "You are the capture lead's pricing analyst. Given an RFP requirement and "
    "a catalog of pricing line items in our cost model, you decide whether the "
    "requirement is already covered by existing lines, partially covered, or "
    "a gap that needs a new line. You MUST be precise — missing a cost in the "
    "model means the bid under-prices the scope. You respond with a single "
    "JSON object only, no preamble, no code fences."
)


def _parse_json_object(raw: str) -> Dict[str, Any]:
    """Tolerant JSON parser: finds first { ... } block and loads it."""
    if not raw:
        return {}
    # Strip markdown code fences if present
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    # Find first balanced object
    depth = 0
    start = -1
    for i, ch in enumerate(raw):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    return json.loads(raw[start : i + 1])
                except Exception:
                    continue
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _adjudicate_coverage(req: RfpRequirement, catalog_prompt: str) -> Dict[str, Any]:
    """Ask the LLM whether this requirement is covered. Returns a normalized dict."""
    prompt = f"""REQUIREMENT under review:
  id:          {req.id}
  section:     {req.section_id or ''}
  category:    {req.category or ''}
  title:       {req.title or ''}
  description: {req.description or ''}
  source_text: {(req.source_text or '')[:1200]}

EXISTING PRICING LINE ITEMS (choose by [id=...] when referring):
{catalog_prompt}

Decide coverage.

coverage_status options:
  "not_cost_bearing" — this requirement has no material cost impact on the
      bid (e.g. informational, a form we already submit with our standard
      forms kit, a definition, a clause acknowledgment). Do not propose
      a new line. Provide a 1-sentence rationale.
  "covered"          — one or more existing line items fully address the
      cost of this requirement. List their ids in matched_line_item_ids.
  "partial"          — existing lines address part of the cost but
      something is missing (e.g. the form is covered but on-site training
      hours to use it are not). List the partial matches AND propose the
      missing new line item.
  "gap"              — no existing line covers this. Propose a new line
      item with category, name, unit, and allocation basis.

proposed_tab options: assumptions | labor | odcs | capital | hourly_rates | submittal | cashflow | comparison
proposed_allocation_basis options: CIF | PIF | SHARED | CAPITAL_AMORT
proposed_unit examples: hours | annual | monthly | per-txn | each | station | lump_sum | % of labor

severity options: critical (DQ or major pricing miss) | high | medium | low | informational
confidence options: high | medium | low

Output a single JSON object with exactly these keys:
{{
  "coverage_status": "not_cost_bearing|covered|partial|gap",
  "matched_line_item_ids": [int, ...],
  "proposed_tab": "...",
  "proposed_category_name": "...",
  "proposed_line_name": "...",
  "proposed_description": "...",
  "proposed_unit": "...",
  "proposed_allocation_basis": "...",
  "proposed_qty": 1.0,
  "rationale": "...",
  "confidence": "high|medium|low",
  "severity": "critical|high|medium|low|informational"
}}

For "covered" and "not_cost_bearing": leave proposed_* fields as "" (empty string) or 0.0.
For "partial" and "gap": always fill the proposed_* fields.
No preamble, no code fences. JSON object only.
"""
    try:
        raw = _call_ai(prompt, _COVERAGE_SYSTEM, max_tokens=900)
    except Exception as e:
        logger.warning(f"Coverage LLM call failed for req={req.id}: {e}")
        return {
            "coverage_status": "partial",
            "matched_line_item_ids": [],
            "proposed_line_name": "[needs human review]",
            "rationale": f"LLM error: {e}",
            "confidence": "low",
            "severity": "medium",
        }

    obj = _parse_json_object(raw) or {}

    # Normalize
    status = str(obj.get("coverage_status") or "").strip().lower()
    if status not in {"not_cost_bearing", "covered", "partial", "gap"}:
        status = "partial"

    matched = obj.get("matched_line_item_ids") or []
    if not isinstance(matched, list):
        matched = []
    matched = [int(x) for x in matched if isinstance(x, (int, float)) or (isinstance(x, str) and x.isdigit())]

    conf = str(obj.get("confidence") or "medium").strip().lower()
    if conf not in {"high", "medium", "low"}:
        conf = "medium"
    sev = str(obj.get("severity") or "medium").strip().lower()
    if sev not in {"critical", "high", "medium", "low", "informational"}:
        sev = "medium"

    def _s(k: str) -> str:
        v = obj.get(k)
        return str(v).strip() if v is not None else ""

    try:
        qty = float(obj.get("proposed_qty") or 0.0)
    except Exception:
        qty = 0.0

    return {
        "coverage_status": status,
        "matched_line_item_ids": matched,
        "proposed_tab": _s("proposed_tab"),
        "proposed_category_name": _s("proposed_category_name"),
        "proposed_line_name": _s("proposed_line_name"),
        "proposed_description": _s("proposed_description"),
        "proposed_unit": _s("proposed_unit"),
        "proposed_allocation_basis": _s("proposed_allocation_basis"),
        "proposed_qty": qty,
        "rationale": _s("rationale"),
        "confidence": conf,
        "severity": sev,
    }


# ── Public entrypoint ────────────────────────────────────────────────
DEFAULT_COVERAGE_CLASSES: Tuple[str, ...] = (
    "proposal_obligation",
    "technical_spec",
    "deadline",
    # "unclassified" intentionally INCLUDED — safer to let the LLM veto
    # than silently skip rows we don't know how to classify.
    "unclassified",
)


def run_coverage_analysis(
    db: Session,
    pricing_model_id: int,
    proposal_id: Optional[int] = None,
    document_ids: Optional[List[int]] = None,
    label: Optional[str] = None,
    created_by_user_id: Optional[int] = None,
    max_requirements: Optional[int] = None,
    requirement_classes: Optional[List[str]] = None,
) -> int:
    """Run a full coverage pass.

    Scope:
      - ``proposal_id`` given  → analyze every requirement where
        RfpRequirement.proposal_id == proposal_id.
      - ``document_ids`` given → analyze every requirement where
        RfpRequirement.document_id IN document_ids.
      - both given → intersection.
      - neither  → raises ValueError (guard against accidental full-DB sweeps).
      - ``requirement_classes`` → restrict to rows with requirement_class in
        this list. Defaults to DEFAULT_COVERAGE_CLASSES (obligation/spec/
        deadline/unclassified). Pass an empty list [] to disable filtering
        entirely (old behavior — scans every row).

    Returns the ``PricingCoverageRun.id``.
    """
    if not proposal_id and not document_ids:
        raise ValueError("Must supply proposal_id or document_ids.")

    model = db.query(PricingModel).get(pricing_model_id)
    if not model:
        raise ValueError(f"PricingModel id={pricing_model_id} not found.")

    # Header row
    run = PricingCoverageRun(
        pricing_model_id=pricing_model_id,
        proposal_id=proposal_id,
        label=label or f"Coverage analysis {datetime.utcnow().isoformat(timespec='seconds')}",
        status="running",
        created_by=created_by_user_id,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    logger.info(
        f"Coverage run {run.id}: model={pricing_model_id} proposal={proposal_id} "
        f"docs={document_ids} label={run.label!r}"
    )

    # Build query
    q = db.query(RfpRequirement)
    if proposal_id:
        q = q.filter(RfpRequirement.proposal_id == proposal_id)
    if document_ids:
        q = q.filter(RfpRequirement.document_id.in_(document_ids))

    # Default: restrict to cost-relevant classes (obligation/spec/deadline/unclassified).
    # Pass requirement_classes=[] to disable (legacy behavior = scan all).
    effective_classes: Optional[List[str]]
    if requirement_classes is None:
        effective_classes = list(DEFAULT_COVERAGE_CLASSES)
    elif requirement_classes == []:
        effective_classes = None
    else:
        effective_classes = list(requirement_classes)

    if effective_classes:
        q = q.filter(RfpRequirement.requirement_class.in_(effective_classes))

    q = q.order_by(RfpRequirement.id)
    if max_requirements:
        q = q.limit(max_requirements)

    requirements = q.all()
    logger.info(
        f"Coverage run {run.id}: scanning {len(requirements)} requirements "
        f"(classes_filter={effective_classes})"
    )

    catalog = _build_line_item_catalog(db, pricing_model_id)
    catalog_prompt = _format_catalog_for_prompt(catalog)

    covered_ct = partial_ct = gap_ct = cost_bearing_ct = 0
    scanned_ct = 0

    try:
        for i, req in enumerate(requirements, 1):
            scanned_ct += 1

            if not _is_cost_bearing(req):
                # Skip non-cost-bearing — don't waste an LLM call.
                continue

            cost_bearing_ct += 1
            result = _adjudicate_coverage(req, catalog_prompt)

            status = result["coverage_status"]
            if status == "not_cost_bearing":
                # LLM second-opinion agrees this isn't cost-bearing after all;
                # don't persist a row, just unwind the heuristic.
                cost_bearing_ct -= 1
                continue

            if status == "covered":
                covered_ct += 1
            elif status == "partial":
                partial_ct += 1
            elif status == "gap":
                gap_ct += 1

            sugg = PricingCoverageSuggestion(
                run_id=run.id,
                pricing_model_id=pricing_model_id,
                requirement_id=req.id,
                coverage_status=status,
                matched_line_item_ids_json=json.dumps(result["matched_line_item_ids"]),
                proposed_tab=result["proposed_tab"] or None,
                proposed_category_name=result["proposed_category_name"] or None,
                proposed_line_name=result["proposed_line_name"] or None,
                proposed_description=result["proposed_description"] or None,
                proposed_unit=result["proposed_unit"] or None,
                proposed_allocation_basis=result["proposed_allocation_basis"] or None,
                proposed_qty=result["proposed_qty"] or None,
                rationale=result["rationale"] or None,
                confidence=result["confidence"],
                severity=result["severity"],
                status="pending",
            )
            db.add(sugg)

            # Commit in batches so progress is durable even on long runs.
            if i % 25 == 0:
                db.commit()
                logger.info(
                    f"Coverage run {run.id}: {i}/{len(requirements)} scanned "
                    f"(covered={covered_ct} partial={partial_ct} gap={gap_ct})"
                )

        db.commit()
        run.requirements_scanned = scanned_ct
        run.cost_bearing_count = cost_bearing_ct
        run.covered_count = covered_ct
        run.partial_count = partial_ct
        run.gap_count = gap_ct
        run.status = "completed"
        run.completed_at = datetime.utcnow()
        db.commit()
        logger.info(
            f"Coverage run {run.id} COMPLETE: scanned={scanned_ct} "
            f"cost_bearing={cost_bearing_ct} covered={covered_ct} "
            f"partial={partial_ct} gap={gap_ct}"
        )
    except Exception as e:
        logger.exception(f"Coverage run {run.id} FAILED")
        db.rollback()
        run = db.query(PricingCoverageRun).get(run.id)
        run.status = "failed"
        run.error_message = str(e)[:4000]
        run.completed_at = datetime.utcnow()
        db.commit()
        raise

    return run.id


# ── Accept / reject suggestions ──────────────────────────────────────
def accept_suggestion(
    db: Session,
    suggestion_id: int,
    reviewer_user_id: Optional[int] = None,
    review_notes: Optional[str] = None,
    target_category_id: Optional[int] = None,
) -> Optional[int]:
    """Apply a suggestion to the pricing model.

    For ``gap`` and ``partial`` suggestions, creates a new
    ``PricingLineItem`` in the specified category (or creates a new
    category if needed based on proposed_tab + proposed_category_name).
    Returns the new line item id, or None for ``covered`` suggestions
    (which don't require a change, just confirmation).
    """
    sugg = db.query(PricingCoverageSuggestion).get(suggestion_id)
    if not sugg:
        raise ValueError(f"Suggestion {suggestion_id} not found.")
    if sugg.status != "pending":
        raise ValueError(f"Suggestion {suggestion_id} already reviewed (status={sugg.status}).")

    new_line_id: Optional[int] = None

    if sugg.coverage_status in ("gap", "partial") and (sugg.proposed_line_name or "").strip():
        # Locate / create the target category.
        cat: Optional[PricingCategory] = None
        if target_category_id:
            cat = db.query(PricingCategory).get(target_category_id)
            if not cat or cat.pricing_model_id != sugg.pricing_model_id:
                raise ValueError("target_category_id does not belong to this model.")
        else:
            cat_name = (sugg.proposed_category_name or "Suggested Additions").strip()
            tab = (sugg.proposed_tab or "odcs").strip().lower()
            cat = (
                db.query(PricingCategory)
                .filter(
                    PricingCategory.pricing_model_id == sugg.pricing_model_id,
                    PricingCategory.name == cat_name,
                    PricingCategory.tab == tab,
                )
                .first()
            )
            if not cat:
                cat = PricingCategory(
                    pricing_model_id=sugg.pricing_model_id,
                    tab=tab,
                    name=cat_name,
                    description="Added via pricing coverage suggestion.",
                    sort_order=999,
                    user_defined=True,
                )
                db.add(cat)
                db.flush()

        line = PricingLineItem(
            category_id=cat.id,
            name=(sugg.proposed_line_name or "").strip() or "[unnamed]",
            description=sugg.proposed_description,
            allocation_basis=(sugg.proposed_allocation_basis or "SHARED").upper(),
            included=True,
            current_cost=0.0,
            future_cost=0.0,
            qty=sugg.proposed_qty or 1.0,
            unit=(sugg.proposed_unit or "each").strip(),
            sort_order=999,
            user_defined=True,
            notes=f"Suggested from RFP requirement id={sugg.requirement_id}. {sugg.rationale or ''}",
        )
        db.add(line)
        db.flush()
        new_line_id = line.id
        sugg.accepted_line_item_id = new_line_id
        sugg.status = "applied"
    else:
        # "covered" → just mark accepted, no model change.
        sugg.status = "accepted"

    sugg.reviewed_by = reviewer_user_id
    sugg.reviewed_at = datetime.utcnow()
    if review_notes:
        sugg.review_notes = review_notes
    db.commit()
    return new_line_id


def reject_suggestion(
    db: Session,
    suggestion_id: int,
    reviewer_user_id: Optional[int] = None,
    review_notes: Optional[str] = None,
) -> None:
    sugg = db.query(PricingCoverageSuggestion).get(suggestion_id)
    if not sugg:
        raise ValueError(f"Suggestion {suggestion_id} not found.")
    if sugg.status != "pending":
        raise ValueError(f"Suggestion {suggestion_id} already reviewed (status={sugg.status}).")
    sugg.status = "rejected"
    sugg.reviewed_by = reviewer_user_id
    sugg.reviewed_at = datetime.utcnow()
    if review_notes:
        sugg.review_notes = review_notes
    db.commit()
