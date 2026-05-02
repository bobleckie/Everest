"""Response-disposition classifier for RFP requirements.

Tunable via env var:
  RDC_PARALLEL  default 4 — number of parallel LLM workers
  RDC_BATCH     default 10 — requirements per LLM call

The Parsons-coverage scan answers ONE question: "do we have past evidence
for this requirement?" That's correct for capability claims, but it
mislabels a huge class of requirements as `gap` when they don't NEED
historical evidence.

Examples that the coverage scan correctly says "gap" but the user just
needs to commit to (no past proposal needed):

  * "Attend Mandatory Site Visit at Bakers Basin on May 9, 2026"
    → event_attendance — calendar commitment, not capability evidence.

  * "State's final decision prevails over conflicts"
    → acknowledgment_only — accept the clause.

  * "Submit Ownership Disclosure Form if ownership changed within 6 months"
    → form_to_complete — fill out at proposal time.

  * "Identify and secure SCM-approved schedule updates"
    → commitment_only — process Parsons will follow.

This classifier returns ONE of six values for each requirement:

    evidence_required     — past performance / capability claim
    commitment_only       — process/procedure Parsons will follow
    acknowledgment_only   — accept a clause / right
    event_attendance      — scheduled event we'll attend
    form_to_complete      — form / cert submitted with the bid
    informational_only    — no response required (RFP background)

Two-pass design (cost-optimized):
  1. **Regex pass** — covers the obvious cases for free.
  2. **LLM pass** — only for ambiguous remainders, batched ~10/call.

Output is persisted to ``RfpRequirement.response_disposition`` plus
notes + classifier provenance. The classifier NEVER overwrites a value
set by ``response_disposition_classifier == 'manual'`` — user-edits win.
"""
from __future__ import annotations

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..models import RfpRequirement
from .competitor_analyst import _call_ai

logger = logging.getLogger(__name__)


def _parallel_workers() -> int:
    try:
        return max(1, min(16, int(os.environ.get("RDC_PARALLEL", "4"))))
    except ValueError:
        return 4


def _batch_size_env() -> int:
    try:
        return max(1, min(50, int(os.environ.get("RDC_BATCH", "10"))))
    except ValueError:
        return 10


VALID_DISPOSITIONS = {
    "evidence_required",
    "commitment_only",
    "acknowledgment_only",
    "event_attendance",
    "form_to_complete",
    "informational_only",
}


# ─────────────────────────────────────────────────────────────────────
# Regex pass — high-confidence rule-based classifier
# ─────────────────────────────────────────────────────────────────────

# Each pattern is (regex, disposition, confidence_label). Pattern order
# matters: earlier patterns win. Patterns are scored against the
# combined title + description (lowercased).

_EVENT_PATTERNS = [
    # Site visits (the user's example)
    re.compile(r"\battend\b.*\b(site visit|pre[- ]?bid|pre[- ]?proposal)\b", re.I),
    re.compile(r"\bmandatory site visit\b", re.I),
    re.compile(r"\bpre[- ]?(quote|bid|proposal)\s+(submission\s+)?conference\b", re.I),
    # Specific NJ MVC facility-name based site-visit requirements
    re.compile(r"\battend\b.*\b(bakers basin|flemington|washington|eatontown|rahway|wayne|paramus|newark)\s+(inspection|station|facility)\b", re.I),
    # General attendance language
    re.compile(r"\battend\b.*\b(meeting|conference|orientation|kickoff|kick[- ]off)\b", re.I),
]

_ACKNOWLEDGMENT_PATTERNS = [
    # "State retains right", "State may", "Director may", "at any time"
    re.compile(r"^(?:the\s+)?(state|director|division|agency|scm)\s+(retains|reserves|may|shall have|will have|has)\s+the\s+right\b", re.I),
    re.compile(r"\bthe\s+(state|director|division|agency)\s+(reserves|retains)\s+(the\s+)?right\b", re.I),
    re.compile(r"\bin\s+the\s+(state'?s?|director'?s?|division'?s?|agency'?s?)\s+(sole\s+)?discretion\b", re.I),
    # "State's final decision prevails"
    re.compile(r"\bstate'?s?\s+(final|sole)\s+(decision|determination|judgment)\s+(prevails|controls|governs)\b", re.I),
    # "shall be at no cost to the State", "no liability to State"
    re.compile(r"\b(shall be|will be|is)\s+at\s+no\s+(cost|expense|liability)\s+to\s+the\s+state\b", re.I),
    # Indemnification clauses
    re.compile(r"^(contractor'?s?\s+)?indemnification\b", re.I),
    re.compile(r"\bindemnify\s+(and\s+hold\s+harmless\s+)?the\s+(state|agency|division)\b", re.I),
    # "in the event of conflicts ... will prevail"
    re.compile(r"\bin\s+the\s+event\s+of\s+(conflicts?|inconsistenc|discrepanc)", re.I),
    # Liability / penalty / damages clauses (acknowledgment of consequences)
    re.compile(r"^(liquidated\s+damages|penalty|penalt|consequential\s+damages|liability)\b", re.I),
    # Termination clauses
    re.compile(r"\b(termination|terminate)\s+for\s+(default|convenience|cause)\b", re.I),
]

_FORM_PATTERNS = [
    # "Submit Form X", "Complete Attachment Y", "include Appendix N"
    re.compile(r"\b(submit|complete|sign|provide|return|include)\s+(?:a\s+(?:new\s+)?|the\s+)?(?:completed\s+)?(?:signed\s+)?(?:standard\s+)?(form|attachment|exhibit|appendix\s+[a-z0-9]+|certification|disclosure|affidavit|questionnaire|cert\b)", re.I),
    # "Ownership Disclosure Form", "MacBride Principles Certification", "Source Disclosure"
    re.compile(r"\b(ownership disclosure|source disclosure|macbride|disclosure\s+statement|business\s+registration|tax\s+(?:clearance|certificate))\b", re.I),
    # Price sheet line items
    re.compile(r"\bprice\s+line(s)?\s+\d+", re.I),
    re.compile(r"\benter\s+.*\bpricing\b", re.I),
    re.compile(r"\bquote\s+price\s+(sheet|line|schedule)\b", re.I),
    # Required attachments to the bid (must be at sentence start to avoid
    # accidentally matching e.g. "system performance security" or "cash
    # discount" boilerplate that mentions "performance bond" tangentially)
    re.compile(r"^(?:provide|submit|furnish|include)\s+(?:a\s+)?bid\s+(security|bond|deposit)\b", re.I),
    re.compile(r"^(?:provide|submit|furnish|include)\s+(?:a\s+)?performance\s+(security|bond)\b", re.I),
]

_PRICING_PATTERNS = [
    # Pricing items are generally form_to_complete (price sheet line items)
    re.compile(r"^price\s+line(s)?\b", re.I),
    re.compile(r"\b(annual\s+all-inclusive|all-inclusive\s+annual)\s+pricing\b", re.I),
    re.compile(r"\bcost\s+per\s+inspection\b", re.I),
    re.compile(r"\bunit\s+price\b", re.I),
]

_COMMITMENT_PATTERNS = [
    # Process commitments — verbs that describe what Parsons will do
    re.compile(r"^(?:the\s+)?(?:contractor|bidder|vendor|parsons|the\s+contractor)\s+(?:shall|must|will)\s+", re.I),
    re.compile(r"^(?:identify|secure|maintain|monitor|document|track|report|coordinate|manage|develop|implement|administer|execute)\s+", re.I),
]

_INFORMATIONAL_PATTERNS = [
    # RFP framing / definitions / general background that asks no response
    re.compile(r"^(?:the\s+)?(?:state|division|agency|new jersey)\s+(?:is|has been|was|operates|maintains|currently|seeks|intends|will|wishes|requires|requests)\b", re.I),
    re.compile(r"^(?:overview|background|introduction|purpose|definition|scope of work|definitions?)\s*[:\-]?", re.I),
    re.compile(r"^this\s+(?:bid|RFP|solicitation|section|chapter|appendix)", re.I),
]


def _classify_one_regex(title: str, description: str, source_text: str = "") -> Optional[Tuple[str, str]]:
    """Run regex patterns against a single requirement. Returns
    (disposition, matched_pattern) or None if no rule fires."""
    text = f"{title or ''} {description or ''}".strip()
    if not text:
        return None
    text_lc = text.lower()

    # Order matters: most specific first.
    for pat in _EVENT_PATTERNS:
        if pat.search(text):
            return ("event_attendance", f"matched:{pat.pattern[:60]}")

    for pat in _FORM_PATTERNS:
        if pat.search(text):
            return ("form_to_complete", f"matched:{pat.pattern[:60]}")

    for pat in _PRICING_PATTERNS:
        if pat.search(text):
            return ("form_to_complete", f"pricing:{pat.pattern[:60]}")

    for pat in _ACKNOWLEDGMENT_PATTERNS:
        if pat.search(text):
            return ("acknowledgment_only", f"matched:{pat.pattern[:60]}")

    # Informational patterns are weaker — only fire if no other category caught it
    for pat in _INFORMATIONAL_PATTERNS:
        if pat.search(text):
            return ("informational_only", f"matched:{pat.pattern[:60]}")

    # No regex matched — let the LLM decide
    return None


# ─────────────────────────────────────────────────────────────────────
# LLM pass — for the residual ambiguous requirements
# ─────────────────────────────────────────────────────────────────────

_BATCH_SIZE = 10  # requirements per LLM call
_LLM_SYSTEM = (
    "You are a Parsons capture-team analyst classifying RFP requirements "
    "by HOW THEY MUST BE RESPONDED TO. You will receive a batch of "
    "requirement descriptions and must return a JSON array, one entry "
    "per input, in the SAME ORDER.\n\n"
    "DISPOSITION TAXONOMY (pick exactly one per requirement):\n\n"
    "  * evidence_required   — Needs past-performance proof or capability "
    "                          evidence to substantiate. Examples: 'system "
    "                          must support 99.9% uptime', 'demonstrate "
    "                          ISO 27001 compliance', 'NGWorkstation must "
    "                          lock out when X', 'Include Physical Data "
    "                          Models'. ANY technical capability claim, "
    "                          system spec, data format, hardware spec, "
    "                          past performance assertion → THIS.\n\n"
    "  * commitment_only     — A process or procedure Parsons will follow "
    "                          but doesn't need historical proof. The "
    "                          response is essentially 'Parsons will do "
    "                          this'. Examples: 'Identify and secure SCM-"
    "                          approved updates', 'Maintain CIF in good "
    "                          operating condition', 'Coordinate with "
    "                          State-designated contacts'.\n\n"
    "  * acknowledgment_only — A clause Parsons accepts. The response is "
    "                          'Parsons acknowledges and accepts'. "
    "                          Examples: 'State retains right to X', "
    "                          'State's decision prevails', "
    "                          'Indemnification obligations', termination "
    "                          for convenience clauses, liability caps.\n\n"
    "  * event_attendance    — A specific scheduled event Parsons will "
    "                          attend. Examples: 'Attend Mandatory Site "
    "                          Visit at Bakers Basin on May 9, 2026'.\n\n"
    "  * form_to_complete    — Bidder submits a form, certification, "
    "                          attachment, or fills price-sheet lines at "
    "                          proposal-submission time. Examples: 'Submit "
    "                          Ownership Disclosure Form', 'Enter annual "
    "                          pricing for Years 1-10', 'Provide bid "
    "                          security'.\n\n"
    "  * informational_only  — Pure context with no response action. "
    "                          Examples: 'The State has been operating "
    "                          this program since 2009', 'Definitions:'.\n\n"
    "DEFAULT TIE-BREAKER: when in doubt between evidence_required and "
    "commitment_only, prefer evidence_required if the requirement names "
    "a SPECIFIC TECHNICAL CAPABILITY (system, hardware, data format) and "
    "commitment_only if it describes a PROCESS Parsons will perform.\n\n"
    "Return STRICT JSON with this shape, no preamble:\n"
    '{"classifications": ['
    '{"id": <int>, "disposition": "<one_of_six>", '
    '"rationale": "<one short sentence>"}, ...]}'
)


def _build_batch_prompt(reqs: List[Dict[str, Any]]) -> str:
    bits = ["Classify each of the following requirements:\n"]
    for r in reqs:
        title = (r.get("title") or "")[:200]
        desc = (r.get("description") or "")[:400]
        bits.append(
            f"\n--- Requirement id={r['id']} (section {r.get('section_id') or '?'}) ---\n"
            f"Title: {title}\n"
            f"Description: {desc}"
        )
    bits.append("\n\nReturn JSON only. One entry per requirement above.")
    return "\n".join(bits)


def _parse_llm_batch(text: str) -> List[Dict[str, Any]]:
    """Parse the LLM's batch response. Tolerant of fences/preamble."""
    if not text:
        return []
    fenced = re.search(r"```(?:json)?\s*(\{.+?\})\s*```", text, re.DOTALL)
    candidate = (fenced.group(1) if fenced else text).strip()
    if not candidate.startswith("{"):
        m = re.search(r"\{.+\}", candidate, re.DOTALL)
        if m:
            candidate = m.group(0)
    try:
        env = json.loads(candidate)
    except json.JSONDecodeError as e:
        logger.warning(f"LLM batch JSON parse failed: {e}")
        return []
    items = env.get("classifications") if isinstance(env, dict) else env
    if not isinstance(items, list):
        return []
    return items


def _classify_batch_llm(reqs: List[Dict[str, Any]]) -> Dict[int, Tuple[str, str]]:
    """Classify a batch of up to _BATCH_SIZE requirements via one LLM call.
    Returns ``{req_id: (disposition, rationale)}`` for valid responses only.
    """
    if not reqs:
        return {}
    prompt = _build_batch_prompt(reqs)
    try:
        raw = _call_ai(prompt, system=_LLM_SYSTEM)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"LLM classification call failed: {e}")
        return {}
    items = _parse_llm_batch(raw)
    out: Dict[int, Tuple[str, str]] = {}
    for it in items:
        try:
            rid = int(it.get("id"))
        except (TypeError, ValueError):
            continue
        disp = str(it.get("disposition") or "").strip().lower()
        if disp not in VALID_DISPOSITIONS:
            logger.warning(f"LLM returned invalid disposition '{disp}' for req {rid}; skipping")
            continue
        rationale = str(it.get("rationale") or "").strip()[:300]
        out[rid] = (disp, rationale)
    return out


# ─────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────

def classify_requirements(
    db: Session,
    *,
    proposal_id: Optional[int] = None,
    coverage_statuses: Optional[List[str]] = None,
    limit: Optional[int] = None,
    use_llm_fallback: bool = True,
    progress_cb: Optional[Any] = None,
) -> Dict[str, Any]:
    """Classify requirements by ``response_disposition``.

    Args:
        db: SQLAlchemy session.
        proposal_id: scope to one proposal (None = all).
        coverage_statuses: only classify requirements with these
            ``parsons_coverage_status`` values. None = all. Default for the
            CLI re-classify call: ``["gap", "uncertain"]``.
        limit: optional cap (handy for testing).
        use_llm_fallback: if True, run an LLM pass on the requirements that
            no regex caught. If False, those are left as None (caller can
            run later).
        progress_cb: optional ``(processed:int, total:int) -> None``.

    Returns a summary dict with per-disposition counts.
    """
    q = db.query(RfpRequirement)
    if proposal_id is not None:
        q = q.filter(RfpRequirement.proposal_id == proposal_id)
    if coverage_statuses:
        q = q.filter(RfpRequirement.parsons_coverage_status.in_(coverage_statuses))
    # Never overwrite manual user edits
    q = q.filter(
        (RfpRequirement.response_disposition_classifier != "manual")
        | (RfpRequirement.response_disposition_classifier.is_(None))
    )
    if limit:
        q = q.limit(limit)
    reqs = q.all()
    total = len(reqs)
    if total == 0:
        return {"processed": 0, "by_disposition": {}, "regex_hits": 0,
                "llm_hits": 0, "errors": 0}

    logger.info(f"classifier: starting on {total} requirements")

    # ── Pass 1: regex ────────────────────────────────────────────────
    now = datetime.utcnow()
    regex_hits = 0
    by_disposition: Dict[str, int] = {}
    ambiguous: List[Dict[str, Any]] = []

    for r in reqs:
        verdict = _classify_one_regex(r.title or "", r.description or "",
                                       r.source_text or "")
        if verdict is None:
            ambiguous.append({
                "id": r.id,
                "title": r.title or "",
                "description": r.description or "",
                "section_id": r.section_id,
            })
            continue
        disp, rationale = verdict
        r.response_disposition = disp
        r.response_disposition_notes = rationale
        r.response_disposition_classified_at = now
        r.response_disposition_classifier = "regex"
        by_disposition[disp] = by_disposition.get(disp, 0) + 1
        regex_hits += 1
    db.commit()
    logger.info(f"classifier regex pass: {regex_hits}/{total} matched, "
                f"{len(ambiguous)} ambiguous")

    # ── Pass 2: LLM (batched, parallel) on ambiguous remainders ─────
    llm_hits = 0
    errors = 0
    if use_llm_fallback and ambiguous:
        batch_size = _batch_size_env()
        workers = _parallel_workers()
        # Snapshot ID→ORM map so workers don't touch the SQLAlchemy session
        reqs_by_id = {r.id: r for r in reqs}
        batches = [
            ambiguous[i:i + batch_size]
            for i in range(0, len(ambiguous), batch_size)
        ]
        logger.info(f"classifier LLM pass: {len(batches)} batches, "
                    f"{workers} parallel workers")

        # Each worker just calls the LLM (no DB access). Main thread
        # consumes results and writes in commit-every-N batches.
        if workers <= 1 or len(batches) <= 1:
            results_iter = ((b, _classify_batch_llm(b)) for b in batches)
        else:
            ex = ThreadPoolExecutor(max_workers=workers)
            futures = {ex.submit(_classify_batch_llm, b): b for b in batches}
            def _gen():
                for fut in as_completed(futures):
                    yield futures[fut], fut.result()
            results_iter = _gen()

        commit_every = 50
        committed_since = 0
        for batch, results in results_iter:
            now = datetime.utcnow()
            for rid, (disp, rationale) in results.items():
                r = reqs_by_id.get(rid)
                if r is None:
                    continue
                r.response_disposition = disp
                r.response_disposition_notes = rationale
                r.response_disposition_classified_at = now
                r.response_disposition_classifier = "llm"
                by_disposition[disp] = by_disposition.get(disp, 0) + 1
                llm_hits += 1
                committed_since += 1
            errors += len(batch) - len(results)
            if committed_since >= commit_every:
                db.commit()
                committed_since = 0
            if progress_cb:
                try:
                    progress_cb(regex_hits + llm_hits + errors, total)
                except Exception:
                    pass
        # Final commit
        db.commit()
        logger.info(f"classifier LLM pass complete: {llm_hits} classified, "
                    f"{errors} errors")

    summary = {
        "processed": regex_hits + llm_hits,
        "total": total,
        "regex_hits": regex_hits,
        "llm_hits": llm_hits,
        "errors": errors,
        "by_disposition": by_disposition,
    }
    logger.info(f"classifier complete: {summary}")
    return summary
