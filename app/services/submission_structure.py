"""Extract the RFP's own definition of how the vendor must submit, and
map every extracted requirement into one of those buckets.

Why this exists
---------------
Without this, the readiness dashboard rolls requirements up by
``RfpRequirement.section_id`` (e.g. ``"3.2H"`` → ``"Section 3"``) which
produces 1,000+ tiny buckets that look like opaque IDs. The user wants
the dashboard to mirror the RFP's own submission terminology — for the
NJ MVC Bid Solicitation that's the three-bucket structure defined in
``3.12 QUOTE CONTENT``: **Forms**, **Technical Quote**,
**State-Supplied Price Sheet**. Other RFPs may use **Technical Volume /
Cost Volume / Past Performance**, **Tab 1 / Tab 2 / Tab 3**, etc. —
whatever the RFP itself says.

Two stages
----------
1. ``extract_submission_structure(db, proposal_id, ...)`` — finds the
   chunks that define the submission structure (heuristic search over
   keywords like "QUOTE CONTENT", "PROPOSAL FORMAT", "BIDDER MUST
   SUBMIT", etc.), feeds them to an LLM that returns a structured tree,
   and persists it as ``ProposalSubmissionSection`` rows.

2. ``map_requirements_to_submission(db, proposal_id, ...)`` — for each
   ``RfpRequirement`` with no ``submission_section_slug`` yet, asks the
   LLM which of the extracted top-level buckets it belongs in, with a
   confidence rating and a one-line rationale. Heuristic shortcuts
   handle obvious cases (signed forms → Forms; price/cost → Pricing).

Both stages are idempotent. The mapper batches requirements 8-at-a-time
to amortize prompt overhead.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..models import (
    DocumentChunk, IngestedDocument, ProposalSubmissionSection,
    RfpRequirement,
)
from .orchestrator_agent import _call_ai

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────

# Keywords that anchor a "vendor submission structure" section. Tuned to
# the kind of language NJ-style RFPs use; broad enough to catch federal/
# state/local variants too.
_ANCHOR_PHRASES = [
    "QUOTE CONTENT",
    "QUOTE SUBMISSION",
    "PROPOSAL FORMAT",
    "PROPOSAL SUBMISSION REQUIREMENTS",
    "PROPOSAL CONTENT",
    "PROPOSAL ORGANIZATION",
    "BIDDER MUST SUBMIT",
    "VENDOR MUST SUBMIT",
    "SUBMISSION REQUIREMENTS",
    "SUBMITTAL REQUIREMENTS",
    "PROPOSAL SHALL BE ORGANIZED",
    "PROPOSAL VOLUMES",
    "REQUIRED SUBMITTALS",
    "FORMAT OF PROPOSAL",
    "FORMAT OF QUOTE",
]


def _slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9_]+", "_", (s or "").lower()).strip("_")
    return s[:64] or f"sec_{uuid.uuid4().hex[:6]}"


# ─────────────────────────────────────────────────────────────────────
# Stage 1 — find the structure
# ─────────────────────────────────────────────────────────────────────

def _find_anchor_chunks(
    db: Session,
    proposal_id: int,
    *,
    max_chunks: int = 25,
) -> List[DocumentChunk]:
    """Locate the RFP chunks most likely to define the submission format.

    Strategy: pull all chunks from RFP source-type docs on the proposal,
    score each by anchor-phrase hit count, and keep the highest-scoring
    contiguous neighborhood (anchor chunk ± 4).
    """
    docs = (db.query(IngestedDocument)
            .filter(IngestedDocument.proposal_id == proposal_id)
            .filter(IngestedDocument.source_type == "rfp")
            .all())
    if not docs:
        return []
    doc_ids = [d.id for d in docs]

    chunks = (db.query(DocumentChunk)
              .filter(DocumentChunk.document_id.in_(doc_ids))
              .order_by(DocumentChunk.document_id,
                         DocumentChunk.chunk_index)
              .all())
    if not chunks:
        return []

    # Score each chunk by anchor hits
    scored: List[Tuple[int, DocumentChunk]] = []
    for c in chunks:
        text_upper = (c.content or "").upper()
        hits = sum(1 for ph in _ANCHOR_PHRASES if ph in text_upper)
        if hits:
            scored.append((hits, c))
    if not scored:
        # No exact anchor hit; fall back to the first chunks of the master
        # bid solicitation (largest doc by chunk count) — the LLM will
        # have to detect the structure itself.
        master = max(docs, key=lambda d: d.total_chunks or 0)
        return (db.query(DocumentChunk)
                .filter(DocumentChunk.document_id == master.id)
                .order_by(DocumentChunk.chunk_index)
                .limit(max_chunks).all())

    # Keep neighborhoods around top anchors
    scored.sort(key=lambda x: x[0], reverse=True)
    chosen_ids: List[int] = []
    seen_chunk_ids: set = set()
    for hits, anchor in scored[:5]:
        # Pull the anchor + neighborhood (anchor and the next 6 chunks
        # in the same document, since submission-format sections are
        # usually a few paragraphs long)
        nbhd = (db.query(DocumentChunk)
                .filter(DocumentChunk.document_id == anchor.document_id)
                .filter(DocumentChunk.chunk_index >= anchor.chunk_index)
                .filter(DocumentChunk.chunk_index <= anchor.chunk_index + 8)
                .order_by(DocumentChunk.chunk_index).all())
        for c in nbhd:
            if c.id not in seen_chunk_ids:
                seen_chunk_ids.add(c.id)
                chosen_ids.append(c.id)
        if len(chosen_ids) >= max_chunks:
            break

    return (db.query(DocumentChunk)
            .filter(DocumentChunk.id.in_(chosen_ids))
            .order_by(DocumentChunk.document_id,
                       DocumentChunk.chunk_index).all())


_STRUCTURE_SYSTEM = (
    "You read RFP / bid solicitation excerpts and extract the EXACT "
    "submission structure the RFP requires the vendor to follow when "
    "preparing their proposal/quote. You preserve the RFP's own terminology "
    "verbatim — do NOT invent labels or rename things. If the RFP says "
    "'Forms / Technical Quote / State-Supplied Price Sheet,' you return "
    "those exact three labels. If it says 'Volume I — Technical / Volume II "
    "— Cost,' you return those.\n\n"
    "Output a SINGLE JSON object only. No preamble. No code fences."
)


def _structure_user_prompt(snippets: List[Dict[str, Any]]) -> str:
    blocks = []
    for s in snippets:
        block = (
            f"-- doc_id={s['document_id']} page={s['page']} idx={s['chunk_index']} --\n"
            f"{s['content'][:1500]}"
        )
        blocks.append(block)
    body = "\n\n".join(blocks)
    return f"""RFP EXCERPTS (in order — these are the section(s) of the RFP that define how the vendor must submit a proposal):

{body}

Extract the vendor submission structure EXACTLY as the RFP defines it.

Return a JSON object with this shape:
{{
  "found": true | false,
  "rfp_section_ref": "<e.g. 3.12, 4.4 — the section number/heading that defines this>",
  "intro": "<one paragraph from the RFP that introduces the structure, verbatim>",
  "top_level": [
    {{
      "label": "<the RFP's exact label, e.g. 'Forms'>",
      "rfp_section_ref": "<e.g. 3.12, 3.13>",
      "description": "<one short sentence summarizing what goes here>",
      "source_page": <int or null>,
      "source_quote": "<verbatim quote that introduces this bucket>",
      "items": [
        {{
          "label": "<exact sub-label, e.g. 'Offer and Acceptance Page'>",
          "rfp_section_ref": "<e.g. 3.13.1>",
          "description": "<short>",
          "source_page": <int or null>
        }}
      ]
    }}
  ]
}}

Rules:
* If the excerpts do NOT clearly define a submission structure, return
  {{"found": false, "rfp_section_ref": null, "intro": null, "top_level": []}}.
* Use the RFP's terminology EXACTLY — do not paraphrase labels.
* Only include actual proposal-content sections (Forms, Technical Quote,
  Price Sheet, Volumes, Tabs, etc.). Do NOT include procedural sections
  like "Quote Withdrawal" or "Bidder Responsibility".
* Items inside Forms (form-by-form list) are valuable — include them.
* JSON ONLY. No prose. No markdown fence.
"""


def _parse_json_obj(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.+?\})\s*```", text, re.DOTALL)
    cand = (fenced.group(1) if fenced else text).strip()
    if not cand.startswith("{"):
        m = re.search(r"\{.+\}", cand, re.DOTALL)
        if m:
            cand = m.group(0)
    try:
        v = json.loads(cand)
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        return None


def extract_submission_structure(
    db: Session,
    proposal_id: int,
    *,
    replace_existing: bool = False,
) -> Dict[str, Any]:
    """Extract the RFP's submission structure for one proposal.

    Idempotent: by default refuses to overwrite an existing structure
    unless ``replace_existing=True``. Returns a payload describing what
    was extracted, or ``{'error': ...}`` if the RFP doesn't define one.
    """
    existing = (db.query(ProposalSubmissionSection)
                .filter(ProposalSubmissionSection.proposal_id == proposal_id)
                .all())
    if existing and not replace_existing:
        return {
            "proposal_id": proposal_id,
            "already_extracted": True,
            "section_count": len(existing),
            "top_level_count": sum(1 for s in existing if s.is_top_level),
        }

    if existing and replace_existing:
        # Wipe slate. Cascading delete of children first via ordering.
        # We just delete all rows for this proposal.
        for s in existing:
            db.delete(s)
        db.commit()

    chunks = _find_anchor_chunks(db, proposal_id)
    if not chunks:
        return {"error": "No RFP documents found for this proposal."}

    snippets = [{
        "document_id": c.document_id,
        "page": c.page_number,
        "chunk_index": c.chunk_index,
        "content": c.content or "",
    } for c in chunks]

    prompt = _structure_user_prompt(snippets)
    # Need a generous output budget — the structure can include nested form
    # items (e.g. ~10 forms inside "Forms" alone in the NJ MVC RFP).
    raw = _call_ai(prompt, _STRUCTURE_SYSTEM, max_tokens=6000)
    env = _parse_json_obj(raw)
    if not env or not env.get("found") or not isinstance(env.get("top_level"), list):
        return {
            "error": "RFP submission structure could not be detected.",
            "raw_preview": (raw or "")[:500],
        }

    run_id = f"struct-{uuid.uuid4().hex[:8]}"
    now = datetime.utcnow()
    saved_top = 0
    saved_items = 0
    # Track all slugs in this batch (including uncommitted ones) to avoid
    # collisions when the LLM emits items with identical labels (e.g.
    # multiple "Reserved" sub-items in a forms list).
    used_slugs: set = set()

    def _unique_slug(base: str) -> str:
        if base not in used_slugs:
            used_slugs.add(base)
            return base
        suffix = 1
        while f"{base}_{suffix}" in used_slugs:
            suffix += 1
        slug = f"{base}_{suffix}"
        used_slugs.add(slug)
        return slug

    for i, top in enumerate(env["top_level"]):
        label = (top.get("label") or "").strip()
        if not label:
            continue
        slug = _unique_slug(_slugify(label))

        # Resolve which RFP doc the source_page belongs to (use first anchor doc)
        src_doc_id = chunks[0].document_id if chunks else None

        parent_row = ProposalSubmissionSection(
            proposal_id=proposal_id,
            parent_id=None,
            slug=slug,
            label=label[:200],
            rfp_section_ref=(top.get("rfp_section_ref") or "").strip()[:50] or None,
            description=(top.get("description") or "").strip()[:1000] or None,
            source_document_id=src_doc_id,
            source_page=top.get("source_page"),
            source_quote=(top.get("source_quote") or "").strip()[:2000] or None,
            order_index=i,
            is_top_level=True,
            extracted_at=now,
            extraction_run_id=run_id,
        )
        db.add(parent_row)
        db.flush()
        saved_top += 1

        for j, item in enumerate(top.get("items") or []):
            item_label = (item.get("label") or "").strip()
            if not item_label:
                continue
            item_slug = _unique_slug(_slugify(f"{slug}__{item_label}"))
            db.add(ProposalSubmissionSection(
                proposal_id=proposal_id,
                parent_id=parent_row.id,
                slug=item_slug,
                label=item_label[:200],
                rfp_section_ref=(item.get("rfp_section_ref") or "").strip()[:50] or None,
                description=(item.get("description") or "").strip()[:1000] or None,
                source_document_id=src_doc_id,
                source_page=item.get("source_page"),
                source_quote=None,
                order_index=j,
                is_top_level=False,
                extracted_at=now,
                extraction_run_id=run_id,
            ))
            saved_items += 1

    db.commit()
    return {
        "proposal_id": proposal_id,
        "extraction_run_id": run_id,
        "rfp_section_ref": env.get("rfp_section_ref"),
        "intro": env.get("intro"),
        "top_level_count": saved_top,
        "item_count": saved_items,
    }


# ─────────────────────────────────────────────────────────────────────
# Stage 2 — map requirements to submission buckets
# ─────────────────────────────────────────────────────────────────────

# Heuristic shortcuts for obvious mappings. Slug substrings are matched
# against the actual extracted slugs, so we work for any taxonomy the
# RFP defines (a "Cost Volume" doc → matches anything starting with cost_).
_HEURISTIC_RULES: List[Tuple[List[str], List[str]]] = [
    # (requirement-text-keywords, submission-section-slug-substrings)
    (["price sheet", "pricing", "cost", "rate", "labor rate", "hourly rate"],
     ["price", "cost", "pric"]),
    (["form", "ownership disclosure", "offer and acceptance",
      "macbride principles", "dpcc", "subcontractor utilization"],
     ["form", "form_packet", "submittal_form"]),
    (["technical approach", "scope of work", "implementation",
      "experience", "past performance", "key personnel"],
     ["technical", "tech_quote", "technical_quote"]),
]


def _heuristic_bucket(
    req: RfpRequirement,
    sections: List[ProposalSubmissionSection],
) -> Optional[ProposalSubmissionSection]:
    """Try to match a requirement to a top-level bucket using simple
    keyword heuristics. Returns None if no confident match."""
    text = " ".join(filter(None, [
        (req.title or "").lower(),
        (req.description or "").lower()[:800],
        (req.section_id or "").lower(),
        (req.category or "").lower(),
    ]))
    for keywords, slug_hints in _HEURISTIC_RULES:
        if any(k in text for k in keywords):
            for s in sections:
                slug = s.slug.lower()
                if any(h in slug for h in slug_hints):
                    return s
    return None


_MAPPER_SYSTEM = (
    "You map RFP requirements into the RFP's own vendor-submission "
    "structure (the buckets the vendor must use to organize their "
    "proposal). Each requirement goes into exactly ONE top-level "
    "submission bucket. You use ONLY the buckets supplied — never "
    "invent new ones. When a requirement is informational background "
    "(definitions, glossary, recitals, contractor obligations during "
    "performance — NOT during proposal preparation), you return "
    "'(none)' for the bucket.\n\n"
    "Output a SINGLE JSON object only. No preamble. No code fences."
)


def _mapper_user_prompt(
    sections: List[ProposalSubmissionSection],
    intro: Optional[str],
    batch: List[RfpRequirement],
) -> str:
    bucket_lines = []
    for s in sections:
        bucket_lines.append(
            f"  - slug: {s.slug}\n"
            f"    label: {s.label}\n"
            f"    rfp_ref: {s.rfp_section_ref or '(none)'}\n"
            f"    description: {(s.description or '').strip()[:200]}"
        )
    bucket_block = "\n".join(bucket_lines)

    req_lines = []
    for r in batch:
        req_lines.append(
            f"  - id: {r.id}\n"
            f"    section_id: {r.section_id or '(none)'}\n"
            f"    title: {(r.title or '').strip()[:240]}\n"
            f"    category: {r.category or '(none)'}\n"
            f"    description: {(r.description or '').strip()[:600]}"
        )
    req_block = "\n".join(req_lines)

    intro_block = (
        f"INTRO (RFP's own description of how the vendor must submit):\n{intro}\n\n"
        if intro else "")

    return f"""{intro_block}AVAILABLE TOP-LEVEL SUBMISSION BUCKETS (use slugs verbatim):
{bucket_block}

REQUIREMENTS TO MAP ({len(batch)}):
{req_block}

For each requirement, decide which bucket it belongs in.

Output a JSON object EXACTLY this shape:
{{
  "mappings": [
    {{
      "requirement_id": <int>,
      "submission_section_slug": "<slug from list above, or '(none)'>",
      "confidence": "high" | "medium" | "low",
      "rationale": "<one short sentence>"
    }}
  ]
}}

Rules:
* slug MUST be one of the slugs listed (or '(none)' if the requirement
  is not vendor-submitted content).
* requirement_id MUST be one of the IDs listed.
* JSON ONLY. No prose."""


def _validate_mapping(
    env: Dict[str, Any],
    valid_slugs: set,
    valid_ids: set,
) -> List[Dict[str, Any]]:
    if not isinstance(env, dict):
        return []
    out = []
    for m in (env.get("mappings") or []):
        if not isinstance(m, dict):
            continue
        try:
            rid = int(m.get("requirement_id"))
        except (TypeError, ValueError):
            continue
        if rid not in valid_ids:
            continue
        slug = (m.get("submission_section_slug") or "").strip()
        if slug != "(none)" and slug not in valid_slugs:
            continue
        conf = (m.get("confidence") or "medium").lower().strip()
        if conf not in ("high", "medium", "low"):
            conf = "medium"
        out.append({
            "requirement_id": rid,
            "slug": slug if slug != "(none)" else None,
            "confidence": conf,
            "rationale": (m.get("rationale") or "").strip()[:600] or None,
        })
    return out


def map_requirements_to_submission(
    db: Session,
    proposal_id: int,
    *,
    batch_size: int = 8,
    only_unmapped: bool = True,
    max_requirements: Optional[int] = None,
) -> Dict[str, Any]:
    """Map every requirement on the proposal to one of the extracted
    top-level submission buckets. Heuristic shortcut when keywords are
    obvious; LLM batch otherwise.

    Idempotent: by default only operates on requirements with no
    submission_section_slug yet (``only_unmapped=True``). Pass
    ``only_unmapped=False`` to remap everything.
    """
    sections = (db.query(ProposalSubmissionSection)
                .filter(ProposalSubmissionSection.proposal_id == proposal_id)
                .filter(ProposalSubmissionSection.is_top_level.is_(True))
                .order_by(ProposalSubmissionSection.order_index).all())
    if not sections:
        return {"error": "No submission structure extracted yet. "
                          "Call extract_submission_structure first."}

    valid_slugs = {s.slug for s in sections}
    intro = None  # We no longer carry intro across function calls; can be enhanced later

    q = (db.query(RfpRequirement)
         .filter(RfpRequirement.proposal_id == proposal_id))
    if only_unmapped:
        q = q.filter(RfpRequirement.submission_section_slug.is_(None))
    reqs = q.all()
    if max_requirements:
        reqs = reqs[:max_requirements]

    if not reqs:
        return {"proposal_id": proposal_id, "mapped": 0,
                "skipped_already_mapped": True}

    counts = {"heuristic": 0, "llm": 0, "none": 0, "errors": 0}
    by_bucket: Dict[str, int] = {}

    # First pass: heuristic
    llm_batch: List[RfpRequirement] = []
    for r in reqs:
        match = _heuristic_bucket(r, sections)
        if match:
            r.submission_section_slug = match.slug
            r.submission_mapping_confidence = "high"
            r.submission_mapping_rationale = "heuristic keyword match"
            counts["heuristic"] += 1
            by_bucket[match.slug] = by_bucket.get(match.slug, 0) + 1
        else:
            llm_batch.append(r)

    db.commit()

    # Second pass: LLM in batches
    for start in range(0, len(llm_batch), batch_size):
        batch = llm_batch[start:start + batch_size]
        valid_ids = {r.id for r in batch}
        try:
            prompt = _mapper_user_prompt(sections, intro, batch)
            raw = _call_ai(prompt, _MAPPER_SYSTEM, max_tokens=1200)
            env = _parse_json_obj(raw)
            mappings = _validate_mapping(env or {}, valid_slugs, valid_ids)
        except Exception as e:  # noqa: BLE001
            logger.exception(f"mapper batch failed: {e}")
            counts["errors"] += len(batch)
            continue

        # Apply
        by_id = {r.id: r for r in batch}
        for m in mappings:
            r = by_id.get(m["requirement_id"])
            if not r:
                continue
            r.submission_section_slug = m["slug"]
            r.submission_mapping_confidence = m["confidence"]
            r.submission_mapping_rationale = m["rationale"]
            if m["slug"] is None:
                counts["none"] += 1
            else:
                counts["llm"] += 1
                by_bucket[m["slug"]] = by_bucket.get(m["slug"], 0) + 1
        db.commit()

    return {
        "proposal_id": proposal_id,
        "total_requirements": len(reqs),
        "mapped_via_heuristic": counts["heuristic"],
        "mapped_via_llm": counts["llm"],
        "mapped_to_none": counts["none"],
        "errors": counts["errors"],
        "by_bucket": by_bucket,
    }
