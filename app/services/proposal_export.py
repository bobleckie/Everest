"""Proposal export — compose the full proposal draft from per-requirement
responses + assembled section narratives.

Two output formats:
  * markdown  — single .md string, also the building block for downstream
  * docx      — proper Word doc using python-docx (heading styles, page
                breaks between sections, compliance disposition tables)

Section ordering is RFP-native: the same `_section_root` rollup used by
the rest of the response writer.

The assembled narrative is preferred when persisted + non-stale; if it's
stale or missing we either re-assemble on the fly (default) or fall back
to a deterministic concatenation of approved per-requirement responses
(if the user disabled re-assembly).
"""
from __future__ import annotations

import io
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ..models import (
    Proposal, ProposalSectionNarrative, RfpRequirement,
)
from .parsons_response import (
    _section_root, _section_label, assemble_section_narrative,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Compose proposal payload (data shape used by both md + docx renderers)
# ─────────────────────────────────────────────────────────────────────

def _ordered_section_roots(reqs: List[RfpRequirement]) -> List[str]:
    """Return section_roots in a sensible RFP order:
       numbered sections ascending, Appendices A→Z, "(unspecified)" last."""
    roots = {_section_root(r.section_id) for r in reqs}

    def sort_key(root: str):
        if root == "(unspecified)":
            return (3, 0, root)
        if root.lower().startswith("section "):
            try:
                n = int(root.split()[1])
                return (0, n, "")
            except (ValueError, IndexError):
                return (0, 999, root)
        if root.lower().startswith("appendix "):
            return (1, 0, root.lower())
        return (2, 0, root.lower())

    return sorted(roots, key=sort_key)


def compose_full_proposal(
    db: Session,
    proposal_id: int,
    *,
    reassemble_stale: bool = True,
    only_approved: bool = False,
) -> Dict[str, Any]:
    """Build a structured payload representing the full proposal draft.

    Each section dict contains:
      * section_root, label
      * narrative_md (from persisted narrative OR freshly assembled)
      * requirements: list of {section_id, title, disposition, response,
        status, requirement_id}
      * stats (drafted/total/approved counts)
      * source: 'persisted' | 'persisted_stale' | 'assembled_now' |
                'concatenated' | 'empty'
    """
    proposal = db.query(Proposal).filter(Proposal.id == proposal_id).first()
    if not proposal:
        return {"error": "Proposal not found"}

    proposal_name = (proposal.title or proposal.rfp_reference
                     or f"Proposal {proposal_id}")

    reqs = (db.query(RfpRequirement)
            .filter(RfpRequirement.proposal_id == proposal_id)
            .all())
    if not reqs:
        return {
            "proposal_id": proposal_id,
            "proposal_name": proposal_name,
            "sections": [],
            "warning": "No requirements found.",
        }

    by_root: Dict[str, List[RfpRequirement]] = {}
    for r in reqs:
        by_root.setdefault(_section_root(r.section_id), []).append(r)

    persisted = {n.section_root: n for n in
                 db.query(ProposalSectionNarrative)
                 .filter(ProposalSectionNarrative.proposal_id == proposal_id)
                 .all()}

    sections_payload: List[Dict[str, Any]] = []
    for root in _ordered_section_roots(reqs):
        in_section = sorted(
            by_root.get(root, []),
            key=lambda r: (r.section_id or "", r.id),
        )
        approved_states = {"approved", "exported"}
        drafted_states = approved_states | {"ai_drafted", "user_edited"}
        eligible_states = approved_states if only_approved else drafted_states
        included = [r for r in in_section
                    if r.parsons_response and (r.parsons_response_status or "")
                    in eligible_states]

        narrative_md = ""
        source = "empty"
        narr = persisted.get(root)
        if narr and narr.narrative_md and not narr.is_stale:
            narrative_md = narr.narrative_md
            source = "persisted"
        elif narr and narr.narrative_md and narr.is_stale and not reassemble_stale:
            narrative_md = narr.narrative_md
            source = "persisted_stale"
        elif included and reassemble_stale:
            try:
                res = assemble_section_narrative(
                    db, proposal_id, root, persist=True
                )
                narrative_md = res.get("narrative") or ""
                source = "assembled_now" if narrative_md else "concatenated"
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Re-assemble failed for {root}: {e}")
                source = "concatenated"

        if not narrative_md and included:
            # Deterministic fallback: just concatenate the per-requirement
            # responses with their requirement IDs as headers.
            narrative_md = "\n\n".join(
                f"**{r.section_id or r.requirement_id or r.id}** — "
                f"{r.compliance_disposition or 'Comply'}\n\n"
                f"{(r.parsons_response or '').strip()}"
                for r in included
            )
            source = "concatenated"

        approved_count = sum(1 for r in in_section
                             if (r.parsons_response_status or "") in approved_states)
        drafted_count = sum(1 for r in in_section
                            if (r.parsons_response_status or "") in drafted_states)

        sections_payload.append({
            "section_root": root,
            "label": _section_label(root),
            "narrative_md": narrative_md,
            "requirements": [
                {
                    "id": r.id,
                    "section_id": r.section_id,
                    "requirement_id": r.requirement_id,
                    "title": r.title,
                    "disposition": r.compliance_disposition,
                    "response": r.parsons_response,
                    "status": r.parsons_response_status,
                }
                for r in in_section
            ],
            "stats": {
                "total": len(in_section),
                "drafted": drafted_count,
                "approved": approved_count,
                "included_in_export": len(included),
            },
            "source": source,
        })

    return {
        "proposal_id": proposal_id,
        "proposal_name": proposal_name,
        "agency": getattr(proposal, "issuing_agency", None),
        "rfp_reference": getattr(proposal, "rfp_reference", None),
        "solicitation_number": getattr(proposal, "solicitation_number", None),
        "due_date": (proposal.due_date.isoformat()
                     if getattr(proposal, "due_date", None) else None),
        "sections": sections_payload,
        "generated_at": datetime.utcnow().isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────
# Markdown renderer
# ─────────────────────────────────────────────────────────────────────

def render_markdown(payload: Dict[str, Any]) -> str:
    """Render the payload as a single .md string."""
    bits: List[str] = []
    bits.append(f"# {payload.get('proposal_name', 'Proposal Draft')}")
    if payload.get("agency"):
        bits.append(f"*Agency:* {payload['agency']}")
    if payload.get("due_date"):
        bits.append(f"*Due:* {payload['due_date']}")
    bits.append(f"*Generated:* {payload.get('generated_at', '')}")
    bits.append("")
    bits.append("---")
    bits.append("")

    for sec in payload.get("sections", []):
        bits.append(f"## {sec['label']}")
        st = sec.get("stats", {})
        bits.append(
            f"_{st.get('included_in_export', 0)} of {st.get('total', 0)} "
            f"requirements included • source: {sec.get('source', 'unknown')}_"
        )
        bits.append("")
        narrative = (sec.get("narrative_md") or "").strip()
        if narrative:
            bits.append(narrative)
        else:
            bits.append("*No drafted responses for this section yet.*")
        bits.append("")

        # Compliance matrix table for the section
        reqs = sec.get("requirements") or []
        if reqs:
            bits.append("### Compliance Matrix")
            bits.append("| Section ID | Disposition | Status | Requirement |")
            bits.append("|---|---|---|---|")
            for r in reqs:
                rid = (r.get("section_id") or r.get("requirement_id")
                       or str(r.get("id") or ""))
                disp = r.get("disposition") or ""
                status = r.get("status") or "not_started"
                title = (r.get("title") or "").replace("|", "\\|")[:120]
                bits.append(f"| {rid} | {disp} | {status} | {title} |")
            bits.append("")
        bits.append("")

    return "\n".join(bits)


# ─────────────────────────────────────────────────────────────────────
# DOCX renderer (python-docx)
# ─────────────────────────────────────────────────────────────────────

def render_docx(payload: Dict[str, Any]) -> bytes:
    """Render the payload as a .docx and return raw bytes."""
    from docx import Document
    from docx.enum.text import WD_BREAK
    from docx.shared import Pt

    doc = Document()
    # Title
    title = doc.add_heading(payload.get("proposal_name", "Proposal Draft"),
                             level=0)
    title_run = title.runs[0]
    title_run.font.size = Pt(20)

    meta = doc.add_paragraph()
    if payload.get("agency"):
        meta.add_run(f"Agency: {payload['agency']}\n").italic = True
    if payload.get("due_date"):
        meta.add_run(f"Due: {payload['due_date']}\n").italic = True
    meta.add_run(f"Generated: {payload.get('generated_at', '')}").italic = True

    for i, sec in enumerate(payload.get("sections", [])):
        if i > 0:
            doc.add_page_break()
        doc.add_heading(sec["label"], level=1)

        st = sec.get("stats", {})
        sub = doc.add_paragraph()
        sub.add_run(
            f"{st.get('included_in_export', 0)} of {st.get('total', 0)} "
            f"requirements • source: {sec.get('source', 'unknown')}"
        ).italic = True

        narrative = (sec.get("narrative_md") or "").strip()
        if narrative:
            # Render markdown paragraphs as docx paragraphs.
            # Headings (## / ###) become Heading 2 / Heading 3.
            for line in narrative.split("\n\n"):
                line = line.strip()
                if not line:
                    continue
                if line.startswith("### "):
                    doc.add_heading(line[4:].strip(), level=3)
                elif line.startswith("## "):
                    doc.add_heading(line[3:].strip(), level=2)
                elif line.startswith("# "):
                    doc.add_heading(line[2:].strip(), level=2)
                else:
                    p = doc.add_paragraph()
                    # Inline bold via **...** is good enough for now.
                    parts = line.split("**")
                    for j, part in enumerate(parts):
                        run = p.add_run(part)
                        if j % 2 == 1:
                            run.bold = True
        else:
            doc.add_paragraph("(No drafted responses for this section yet.)")

        # Compliance matrix table
        reqs = sec.get("requirements") or []
        if reqs:
            doc.add_heading("Compliance Matrix", level=2)
            tbl = doc.add_table(rows=1, cols=4)
            tbl.style = "Light Grid"
            hdr = tbl.rows[0].cells
            hdr[0].text = "Section ID"
            hdr[1].text = "Disposition"
            hdr[2].text = "Status"
            hdr[3].text = "Requirement"
            for r in reqs:
                row = tbl.add_row().cells
                row[0].text = (r.get("section_id") or r.get("requirement_id")
                               or str(r.get("id") or ""))
                row[1].text = r.get("disposition") or ""
                row[2].text = r.get("status") or "not_started"
                row[3].text = (r.get("title") or "")[:200]

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
