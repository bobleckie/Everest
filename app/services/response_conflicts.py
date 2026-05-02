"""Cross-section conflict scanner.

After the per-requirement responses are drafted, sections can drift —
Section 3 says "we use Method A," Section 7 says "Method B," and the
Cost Volume assumes A. This service runs an LLM-based scan over pairs
of sections (focused on the sections actually drafted) and persists
findings into ``cross_section_conflicts``.

Design choices:
  * Only scan sections with ≥2 drafted responses (anything less can't
    really conflict).
  * Cap response payload per section (prompt-budget).
  * Group findings under one ``scan_id`` so the UI can show a fresh
    scan or roll back an old one.
  * Idempotent: re-running clears prior open findings for the same
    proposal so the user always sees the latest verdict (resolved /
    dismissed findings are preserved).
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime
from itertools import combinations
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ..models import CrossSectionConflict, RfpRequirement
from .competitor_analyst import _call_ai
from .parsons_response import _section_label, _section_root

logger = logging.getLogger(__name__)


_SYSTEM = (
    "You are a Parsons proposal review lead scanning for cross-section "
    "conflicts in a proposal draft. You receive per-requirement responses "
    "from TWO different sections of the same proposal. Your job is to find "
    "places where the two sections contradict each other or commit to "
    "incompatible approaches.\n\n"
    "Examples of conflicts:\n"
    "  * Section 3 commits to monthly reporting, Section 7 commits to weekly\n"
    "  * Technical volume specifies Class 8 vehicles, Cost volume prices Class 6\n"
    "  * One section uses 'subcontractor', another says 'no subcontractors'\n"
    "  * Different staffing levels, schedules, methodologies\n\n"
    "Be precise. Do NOT flag style differences, redundancy, or things that "
    "are merely 'unaligned' but not actually contradictory. Only flag a "
    "conflict if a state evaluator would mark the proposal down for "
    "internal inconsistency.\n\n"
    "Output a JSON object exactly like:\n"
    "{\n"
    '  "conflicts": [\n'
    "    {\n"
    '      "severity": "high" | "medium" | "low",\n'
    '      "summary": "<short headline>",\n'
    '      "detail": "<2-3 sentence explanation citing both sides>",\n'
    '      "requirement_ids_a": [<int ids in section A that you cite>],\n'
    '      "requirement_ids_b": [<int ids in section B that you cite>]\n'
    "    }\n"
    "  ]\n"
    "}\n"
    'If no conflicts, output {"conflicts": []}. JSON only — no fence, no prose.'
)


def _eligible_drafted(reqs: List[RfpRequirement]) -> List[RfpRequirement]:
    return [r for r in reqs if r.parsons_response and
            (r.parsons_response_status or "") in
            ("ai_drafted", "user_edited", "approved", "exported")]


def _build_pair_prompt(root_a: str, reqs_a: List[RfpRequirement],
                       root_b: str, reqs_b: List[RfpRequirement]) -> str:
    bits: List[str] = []

    def render_side(label: str, root: str, reqs: List[RfpRequirement]):
        bits.append(f"=== SECTION {label}: {root} — {len(reqs)} responses ===")
        budget = 6000
        used = 0
        for r in reqs:
            block = (
                f"[id={r.id}] section={r.section_id or '?'} "
                f"disp={r.compliance_disposition or '?'}\n"
                f"title: {(r.title or '').strip()[:160]}\n"
                f"response: {(r.parsons_response or '').strip()[:600]}\n"
            )
            if used + len(block) > budget:
                bits.append(f"…and {len(reqs) - reqs.index(r)} more truncated.")
                break
            bits.append(block)
            used += len(block)

    render_side("A", root_a, reqs_a)
    bits.append("")
    render_side("B", root_b, reqs_b)
    bits.append("")
    bits.append("Find cross-section conflicts. JSON only.")
    return "\n".join(bits)


def _parse_envelope(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.+?\})\s*```", text, re.DOTALL)
    cand = (fenced.group(1) if fenced else text).strip()
    if not cand.startswith("{"):
        m = re.search(r"\{.+\}", cand, re.DOTALL)
        if m:
            cand = m.group(0)
    try:
        return json.loads(cand)
    except json.JSONDecodeError:
        return None


def scan_proposal_for_conflicts(
    db: Session,
    proposal_id: int,
    *,
    min_drafted: int = 2,
    max_pairs: int = 28,
) -> Dict[str, Any]:
    """Run a cross-section conflict scan.

    Returns a summary + the persisted conflict rows. Open findings from
    prior scans on this proposal are cleared first so the UI shows the
    latest state. Resolved/dismissed findings are preserved.
    """
    reqs = (db.query(RfpRequirement)
            .filter(RfpRequirement.proposal_id == proposal_id)
            .all())
    if not reqs:
        return {"proposal_id": proposal_id, "conflicts": [],
                "warning": "No requirements."}

    by_root: Dict[str, List[RfpRequirement]] = {}
    for r in reqs:
        by_root.setdefault(_section_root(r.section_id), []).append(r)

    eligible_roots: List[str] = []
    for root, items in by_root.items():
        if len(_eligible_drafted(items)) >= min_drafted:
            eligible_roots.append(root)
    eligible_roots.sort()

    if len(eligible_roots) < 2:
        return {
            "proposal_id": proposal_id,
            "scan_id": None,
            "pairs_scanned": 0,
            "conflicts_found": 0,
            "conflicts": [],
            "warning": "Need at least 2 sections with ≥2 drafted responses each.",
        }

    # Clear prior OPEN findings on this proposal — keep dismissed/resolved
    deleted = (db.query(CrossSectionConflict)
               .filter(CrossSectionConflict.proposal_id == proposal_id,
                       CrossSectionConflict.status == "open")
               .delete(synchronize_session=False))
    db.commit()

    scan_id = f"scan-{uuid.uuid4().hex[:12]}"
    pair_list = list(combinations(eligible_roots, 2))[:max_pairs]
    saved: List[CrossSectionConflict] = []

    for root_a, root_b in pair_list:
        reqs_a = _eligible_drafted(by_root[root_a])
        reqs_b = _eligible_drafted(by_root[root_b])
        if not reqs_a or not reqs_b:
            continue
        try:
            prompt = _build_pair_prompt(root_a, reqs_a, root_b, reqs_b)
            raw = _call_ai(prompt, system=_SYSTEM)
            env = _parse_envelope(raw)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"conflict scan {root_a} vs {root_b} failed: {e}")
            continue
        if not env:
            continue

        # Validate cited requirement ids
        valid_ids_a = {r.id for r in reqs_a}
        valid_ids_b = {r.id for r in reqs_b}

        for c in (env.get("conflicts") or []):
            severity = c.get("severity") or "medium"
            if severity not in ("high", "medium", "low"):
                severity = "medium"
            summary = (c.get("summary") or "").strip()[:280]
            detail = (c.get("detail") or "").strip()[:1500]
            if not summary:
                continue
            ids_a = [int(x) for x in (c.get("requirement_ids_a") or [])
                     if isinstance(x, (int, str)) and str(x).strip().lstrip('-').isdigit()
                     and int(x) in valid_ids_a]
            ids_b = [int(x) for x in (c.get("requirement_ids_b") or [])
                     if isinstance(x, (int, str)) and str(x).strip().lstrip('-').isdigit()
                     and int(x) in valid_ids_b]

            row = CrossSectionConflict(
                proposal_id=proposal_id,
                section_root_a=root_a,
                section_root_b=root_b,
                severity=severity,
                summary=summary,
                detail=detail or None,
                requirement_ids_a=json.dumps(ids_a) if ids_a else None,
                requirement_ids_b=json.dumps(ids_b) if ids_b else None,
                status="open",
                scan_id=scan_id,
                created_at=datetime.utcnow(),
            )
            db.add(row)
            saved.append(row)

    db.commit()

    return {
        "proposal_id": proposal_id,
        "scan_id": scan_id,
        "pairs_scanned": len(pair_list),
        "sections_eligible": len(eligible_roots),
        "prior_open_cleared": deleted,
        "conflicts_found": len(saved),
        "conflicts": [
            {
                "id": c.id,
                "section_root_a": c.section_root_a,
                "section_root_b": c.section_root_b,
                "section_label_a": _section_label(c.section_root_a),
                "section_label_b": _section_label(c.section_root_b),
                "severity": c.severity,
                "summary": c.summary,
                "detail": c.detail,
                "requirement_ids_a": json.loads(c.requirement_ids_a) if c.requirement_ids_a else [],
                "requirement_ids_b": json.loads(c.requirement_ids_b) if c.requirement_ids_b else [],
                "status": c.status,
                "scan_id": c.scan_id,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in saved
        ],
    }
