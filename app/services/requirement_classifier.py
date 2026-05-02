"""Requirement classifier — assigns `requirement_class` to RfpRequirement rows.

Two-pass design:

Pass 1 — `classify_heuristic`:
    Free, deterministic, ~1 second for thousands of rows.
    Uses existing `category`, `document_id`, `source_text`, `title` patterns
    to directly assign a `requirement_class` where the signal is strong.
    Leaves ambiguous rows as "unclassified" for pass 2.

Pass 2 — `classify_llm_batch`:
    Claude Opus, batched 10-20 rows per call. Gets the short title +
    description + source_text + current category + document label and
    returns a class per row. Only runs on rows left as "unclassified"
    after pass 1.

Class vocabulary (stored in RfpRequirement.requirement_class):
    proposal_obligation  — "The Contractor/Vendor shall/must do X"
    technical_spec       — equipment/software/system spec
    checklist_row        — row from an inspection/spec/business-rule table
    deadline             — dated vendor obligation with trigger/due/LD
    inherited_hr         — CBA/union clause (inherited IF staff transition)
    sf_form              — "Submit Form/Attachment X" / certification / signature
    informational        — definition, background, context; no obligation
    unclassified         — classifier couldn't decide (should be rare after pass 2)

Everything is additive — never deletes requirements, only TAGS them.
Use `reset=True` on reclassify to re-run the entire pipeline cleanly.
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models import IngestedDocument, RfpRequirement

logger = logging.getLogger(__name__)

# ─── Heuristic patterns ──────────────────────────────────────────────

# Strong "this is a checklist-table row" signals:
#   - starts with 4+ digit numeric code and pipe separators
#   - contains at least 2 pipe characters (common table delimiter)
#   - extremely short (< 80 chars) AND contains a pipe
_CHECKLIST_PIPE_RE = re.compile(r"^\s*\d{3,5}\s*[\|\uFF5C]")
_TABLE_PIPE_COUNT_RE = re.compile(r"[\|\uFF5C]")
# Trigger-date tables like "I-8 | Contractor submits X | After 335 days | $1,000/day"
_LD_PENALTY_RE = re.compile(r"\$[\d,]+\s*(USD|per\s+(Business\s+)?Day|/day)", re.IGNORECASE)

_OBLIGATION_SUBJECT_RE = re.compile(
    r"\b(contractor|vendor|bidder|offeror|proposer|provider|supplier)\b",
    re.IGNORECASE,
)
_OBLIGATION_VERB_RE = re.compile(
    r"\b(shall|must|will|is\s+required\s+to|are\s+required\s+to|agrees\s+to)\b",
    re.IGNORECASE,
)
_EMPLOYER_UNION_RE = re.compile(
    r"\b(employer|union|bargaining\s+unit|employee|employees|local\s+\d+)\b",
    re.IGNORECASE,
)

_FORM_TITLE_RE = re.compile(
    r"\b(submit|complete|sign|execute|attach|return|provide)\s+"
    r"(form|attachment|exhibit|certification|affidavit|schedule)\b",
    re.IGNORECASE,
)
_SF_FORM_CODE_RE = re.compile(
    r"\b(SF-?\d+|DBE|EEO|W-?9|CONTRACTOR\s+CERTIFICATION|NON-?COLLUSION)\b",
    re.IGNORECASE,
)

_DEFINITION_LEAD_RE = re.compile(
    # e.g. "Equivalent Products — Products that..." or "RFP means a request..."
    r"^\s*[A-Z][A-Za-z0-9\-/\s]{1,60}\s+(\u2014|\u2013|-|means)\s+",
)
_SECTION_HEADING_RE = re.compile(
    r"^\s*(\d+\.\d+(\.\d+)*\s+|SECTION\s+\d+|APPENDIX\s+[A-Z\d])",
    re.IGNORECASE,
)


def _has_date_signal(text: str) -> bool:
    """True if text mentions a date, a day count, or a trigger clause."""
    if not text:
        return False
    t = text.lower()
    if re.search(r"\b\d{1,3}\s+(calendar\s+|business\s+)?days?\b", t):
        return True
    if re.search(r"\b(start\s*\+\s*\d+d?|after\s+\d+\s+days?|within\s+\d+\s+days?)\b", t):
        return True
    if re.search(r"\b(no\s+later\s+than|on\s+or\s+before|by\s+(\d{1,2}[:/]\d{2}|\d{4}))\b", t):
        return True
    if re.search(r"\b(20\d{2}|19\d{2})-\d{1,2}-\d{1,2}\b", t):  # ISO date
        return True
    if re.search(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", t):
        return True
    return False


def _looks_like_checklist_row(source_text: str) -> bool:
    if not source_text:
        return False
    st = source_text.strip()
    # "5210 | Spec: Gear Shift Indicator Mounting | S | Q27 | 287"
    if _CHECKLIST_PIPE_RE.match(st):
        return True
    # "Item | Description | Code | Ref | Page" type tables (3+ pipes, short line)
    pipe_count = len(_TABLE_PIPE_COUNT_RE.findall(st))
    if pipe_count >= 3 and len(st) < 200:
        return True
    # Very short source_text + not obviously an obligation
    if len(st) < 80 and pipe_count >= 2:
        return True
    return False


def _looks_like_technical_spec(title: str, description: str, source: str) -> bool:
    """
    Equipment/system specs feel different from vendor obligations:
    - They describe the THING ("The printout must graphically demonstrate...")
    - Subject is a noun (equipment/display/software/report)
    - Usually NO vendor/contractor subject anywhere nearby
    """
    blob = " ".join([title or "", description or "", source or ""]).lower()
    if _OBLIGATION_SUBJECT_RE.search(blob):
        return False  # has contractor/vendor subject → obligation
    # Signal: tech nouns
    tech_nouns = r"\b(printout|display|screen|monitor|equipment|sensor|software|hardware|database|report|field|column|record|row|button|interface|api|output|signal|gauge|reading|curve|value|table|schema|format)\b"
    if re.search(tech_nouns, blob):
        return True
    return False


# ─── Pass 1: heuristic ────────────────────────────────────────────────

def classify_one_heuristic(
    req: RfpRequirement,
    doc_filename_lower: str,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Return (class, reason) if we can confidently classify, else (None, None).
    """
    cat = (req.category or "").lower()
    title = req.title or ""
    desc = req.description or ""
    source = req.source_text or ""
    blob = f"{title} {desc} {source}".lower()

    # --- Strongest signals first ---

    # 1. CBA / union contract documents → inherited_hr (unless truly generic)
    if "collective" in doc_filename_lower or "bargaining" in doc_filename_lower or "cba" in doc_filename_lower:
        # Employer/Union/Employee language → inherited HR
        if _EMPLOYER_UNION_RE.search(blob):
            return "inherited_hr", "CBA document + employer/union language"
        # Still on the CBA doc, tag as inherited_hr by default — pass 2 can override
        return "inherited_hr", "CBA document (default)"

    # 2. Existing category says "deadline" → deadline
    if cat == "deadline":
        return "deadline", "category=deadline"
    # deadlines can also look like mandatory + LD penalty
    if _LD_PENALTY_RE.search(blob) and _has_date_signal(blob):
        return "deadline", "LD penalty + date signal"

    # 3. Form / signature / certification categories → sf_form
    if cat in ("form", "signature", "certification"):
        return "sf_form", f"category={cat}"
    if _FORM_TITLE_RE.search(title) or _SF_FORM_CODE_RE.search(title + " " + source):
        return "sf_form", "form/certification pattern in title"

    # 4. Informational → informational
    if cat in ("informational", "information", "reference", "definition"):
        return "informational", f"category={cat}"
    if _DEFINITION_LEAD_RE.match(source) or _DEFINITION_LEAD_RE.match(title):
        return "informational", "definition-style leading pattern"

    # 5. Checklist-table rows → checklist_row
    if _looks_like_checklist_row(source):
        return "checklist_row", "table-row pattern in source_text"

    # 6. Classic proposal obligation — "Contractor shall X" with a verb
    if _OBLIGATION_SUBJECT_RE.search(blob) and _OBLIGATION_VERB_RE.search(blob):
        return "proposal_obligation", "contractor/vendor subject + obligation verb"

    # 7. Technical spec (no obligation subject, technical-sounding)
    if _looks_like_technical_spec(title, desc, source):
        return "technical_spec", "technical noun subject, no contractor subject"

    # 8. Fallback: if category is "mandatory" and source has shall/must, treat as obligation
    if cat == "mandatory" and _OBLIGATION_VERB_RE.search(blob):
        return "proposal_obligation", "category=mandatory + obligation verb"

    # Not confident — punt to pass 2
    return None, None


def classify_heuristic(
    db: Session,
    proposal_id: int,
    *,
    reset: bool = False,
    only_unclassified: bool = True,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Run pass 1 on every requirement for `proposal_id`.

    reset=True — wipe existing classifications (set to 'unclassified', class_source=None)
    only_unclassified=True — only touch rows currently 'unclassified' or NULL
    dry_run=True — compute stats but don't commit
    """
    docs = {d.id: (d.filename or "").lower() for d in db.query(IngestedDocument).all()}

    q = db.query(RfpRequirement).filter(RfpRequirement.proposal_id == proposal_id)
    reqs = q.all()

    if reset:
        for r in reqs:
            r.requirement_class = "unclassified"
            r.class_source = None
            r.class_reason = None
        if not dry_run:
            db.commit()
        logger.info("reset: cleared class on %d requirements", len(reqs))

    counts = defaultdict(int)
    updated = 0
    skipped = 0

    for r in reqs:
        current = (r.requirement_class or "unclassified")
        # Don't stomp manual/LLM tags unless reset
        if only_unclassified and current != "unclassified" and r.class_source in ("llm", "manual"):
            skipped += 1
            counts[current] += 1
            continue

        doc_fn = docs.get(r.document_id or -1, "")
        cls, reason = classify_one_heuristic(r, doc_fn)

        if cls is None:
            counts["unclassified"] += 1
            # Leave as-is (no change)
            continue

        r.requirement_class = cls
        r.class_source = "heuristic"
        r.class_reason = reason
        counts[cls] += 1
        updated += 1

    if not dry_run:
        db.commit()

    return {
        "total": len(reqs),
        "updated": updated,
        "skipped_preserved": skipped,
        "by_class": dict(counts),
        "dry_run": dry_run,
    }


# ─── Pass 2: LLM batch ────────────────────────────────────────────────

_LLM_SYSTEM = """You are classifying procurement-document requirements into buckets.

Each requirement has a title, description, source_text excerpt, and category.
Return a JSON array (one object per input, same order) with "id" and "class".

Classes (choose ONE per requirement):
- proposal_obligation : "The Contractor/Vendor shall/must do X" — a vendor commitment in the proposal/contract
- technical_spec      : equipment/software/system/report specification — describes a THING, not a contractor action
- checklist_row       : row from an inspection/spec/business-rule TABLE (short, codes/pipes/IDs)
- deadline            : dated vendor obligation with trigger + due + often penalty/LD
- inherited_hr        : CBA/union contract clause — inherited only if Parsons absorbs the union workforce
- sf_form             : "Submit Form X" / attachment / certification / signature page
- informational       : definition, background, context — no obligation or spec

Return JSON only, shape: [{"id": 123, "class": "proposal_obligation", "reason": "short 1-line rationale"}, ...]
"""

_LLM_BATCH_SIZE = 15


def _llm_batch_one(batch: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Single LLM call over up to _LLM_BATCH_SIZE items. Returns list of {id, class, reason}."""
    from app.services.orchestrator_agent import _call_ai  # local import, avoids circular

    user_payload = json.dumps(batch, ensure_ascii=False)
    raw = _call_ai(
        prompt=(
            "Classify these requirements. Respond with a JSON array matching input order.\n\n"
            f"Input:\n{user_payload}"
        ),
        system=_LLM_SYSTEM,
        max_tokens=2000,
    )
    # Extract JSON array from response
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        logger.warning("LLM classifier returned no JSON array; raw=%s", raw[:300])
        return []
    try:
        parsed = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        logger.warning("LLM classifier JSON decode failed: %s; raw=%s", e, raw[:300])
        return []
    out: List[Dict[str, Any]] = []
    for p in parsed:
        if not isinstance(p, dict):
            continue
        rid = p.get("id")
        cls = p.get("class")
        rsn = p.get("reason") or ""
        if isinstance(rid, int) and isinstance(cls, str):
            out.append({"id": rid, "class": cls, "reason": str(rsn)[:200]})
    return out


def classify_llm_batch(
    db: Session,
    proposal_id: int,
    *,
    limit: Optional[int] = None,
    dry_run: bool = False,
    commit_every: int = 50,
) -> Dict[str, Any]:
    """
    Run pass 2 (LLM) on rows still marked 'unclassified' for this proposal.
    """
    pending = (
        db.query(RfpRequirement)
        .filter(
            RfpRequirement.proposal_id == proposal_id,
            (RfpRequirement.requirement_class == "unclassified")
            | (RfpRequirement.requirement_class.is_(None)),
        )
        .order_by(RfpRequirement.id)
        .all()
    )
    if limit:
        pending = pending[:limit]

    logger.info("LLM classifier: %d pending rows", len(pending))

    processed = 0
    counts: Dict[str, int] = defaultdict(int)
    since_commit = 0

    for i in range(0, len(pending), _LLM_BATCH_SIZE):
        chunk = pending[i : i + _LLM_BATCH_SIZE]
        batch_payload = [
            {
                "id": r.id,
                "title": (r.title or "")[:200],
                "description": (r.description or "")[:300],
                "source_text": (r.source_text or "")[:400],
                "category": r.category or "",
            }
            for r in chunk
        ]
        try:
            results = _llm_batch_one(batch_payload)
        except Exception as e:
            logger.exception("LLM batch failed (items %d..%d): %s", i, i + len(chunk), e)
            continue

        by_id = {r.id: r for r in chunk}
        for res in results:
            r = by_id.get(res["id"])
            if not r:
                continue
            cls = res["class"]
            if cls not in {
                "proposal_obligation", "technical_spec", "checklist_row",
                "deadline", "inherited_hr", "sf_form", "informational",
            }:
                continue  # ignore unknown class
            r.requirement_class = cls
            r.class_source = "llm"
            r.class_reason = res.get("reason") or "llm-assigned"
            counts[cls] += 1
            processed += 1
            since_commit += 1

        if not dry_run and since_commit >= commit_every:
            db.commit()
            since_commit = 0
            logger.info("LLM classifier: committed, %d/%d processed", processed, len(pending))

    if not dry_run:
        db.commit()

    remaining = (
        db.query(RfpRequirement)
        .filter(
            RfpRequirement.proposal_id == proposal_id,
            (RfpRequirement.requirement_class == "unclassified")
            | (RfpRequirement.requirement_class.is_(None)),
        )
        .count()
    )

    return {
        "pending_at_start": len(pending),
        "classified": processed,
        "remaining_unclassified": remaining,
        "by_class": dict(counts),
        "dry_run": dry_run,
    }


# ─── Reporting ────────────────────────────────────────────────────────

def class_summary(db: Session, proposal_id: int) -> Dict[str, Any]:
    """Return class distribution + per-doc breakdown."""
    reqs = (
        db.query(RfpRequirement)
        .filter(RfpRequirement.proposal_id == proposal_id)
        .all()
    )
    overall: Dict[str, int] = defaultdict(int)
    by_doc: Dict[int, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_source: Dict[str, int] = defaultdict(int)

    for r in reqs:
        cls = r.requirement_class or "unclassified"
        overall[cls] += 1
        by_doc[r.document_id or 0][cls] += 1
        by_source[r.class_source or "none"] += 1

    return {
        "total": len(reqs),
        "by_class": dict(overall),
        "by_doc": {k: dict(v) for k, v in by_doc.items()},
        "by_source": dict(by_source),
    }
