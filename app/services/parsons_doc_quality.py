"""Parsons knowledge-document quality assessment + improvement suggestions.

For a given Parsons doc:
  1. Pull the doc text (joined chunks, capped to fit the prompt)
  2. Read the category metadata so the LLM knows what "good" looks like for
     a past_proposal vs a capability_statement vs a cert vs an SOP, etc.
  3. Ask the LLM to produce a JSON envelope:
        {
          "quality_score": 0-100,
          "headline": "<one-line summary>",
          "rationale": "<markdown — strengths, gaps, why this score>",
          "suggestions": [
            {"severity": "high",
             "title": "Add jurisdictions covered",
             "rationale": "...",
             "suggested_text": "...",
             "edit_kind": "append" | "replace" | "prepend" | "metadata",
             "target_chunk_id": null | int}
          ]
        }
  4. Persist the score + replace existing 'pending' suggestions in one shot.
     Already-disposed (accepted/rejected/ignored) suggestions are kept.
  5. Write an audit-log row for the assessment.

The LLM is the same `_call_ai` the rest of the codebase uses. If no AI
provider is configured the function returns an `error` and writes nothing —
caller can show "evidence-pending" UX.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ..models import (
    DocumentChunk, IngestedDocument, ParsonsDocApproval,
    ParsonsDocAuditLog, ParsonsDocCategory, ParsonsDocSuggestion,
)
from .competitor_analyst import _call_ai

logger = logging.getLogger(__name__)

# Cap how much of the doc we feed into the prompt. With multi-MB past
# proposals we'd otherwise blow the input window. Bumped from 30K → 120K
# (~24-30 pages) which fits comfortably inside Claude's 200K context with
# room for the system prompt and answer. For docs LARGER than this we use
# a structural sample (beginning + middle + end + section headers) and
# tell the LLM EXPLICITLY in the prompt that the full text is loaded into
# the system — so it never suggests "link to the full proposal" again.
MAX_DOC_CHARS = 120_000


# ─────────────────────────────────────────────────────────────────────
# Audit log helper — every state-changing operation should call this.
# ─────────────────────────────────────────────────────────────────────

def write_audit(
    db: Session,
    document_id: int,
    action: str,
    *,
    suggestion_id: Optional[int] = None,
    actor_user_id: Optional[int] = None,
    actor_label: Optional[str] = None,
    payload: Optional[dict] = None,
    note: Optional[str] = None,
) -> ParsonsDocAuditLog:
    row = ParsonsDocAuditLog(
        document_id=document_id,
        suggestion_id=suggestion_id,
        action=action,
        actor_user_id=actor_user_id,
        actor_label=actor_label,
        payload_json=json.dumps(payload) if payload else None,
        note=note,
        created_at=datetime.utcnow(),
    )
    db.add(row)
    return row


def ensure_auto_approval(
    db: Session,
    document_id: int,
    actor_user_id: Optional[int] = None,
    actor_label: Optional[str] = None,
) -> ParsonsDocApproval:
    """Create the initial auto-approval row when a Parsons doc is uploaded
    or after a content change. Idempotent — only writes a row if no
    'auto_approved' / 'approved' / 'pending_review' row exists yet."""
    existing = (db.query(ParsonsDocApproval)
                .filter(ParsonsDocApproval.document_id == document_id)
                .order_by(ParsonsDocApproval.created_at.desc())
                .first())
    if existing and existing.status in ("auto_approved", "approved", "pending_review"):
        return existing
    row = ParsonsDocApproval(
        document_id=document_id,
        status="auto_approved",
        approver_user_id=None,
        notes="Auto-approved on upload (manual approval workflow not yet enabled).",
    )
    db.add(row)
    db.flush()
    write_audit(db, document_id, "approval_auto",
                actor_user_id=actor_user_id, actor_label=actor_label,
                payload={"approval_id": row.id, "status": row.status})
    return row


# ─────────────────────────────────────────────────────────────────────
# Doc-text extraction (joined chunks, capped)
# ─────────────────────────────────────────────────────────────────────

def _load_doc_text(db: Session, document_id: int, max_chars: int = MAX_DOC_CHARS) -> str:
    """Load document text for the quality assessor, with smart sampling
    for documents larger than ``max_chars``.

    For docs that fit: concatenate all chunks in order.
    For docs that DON'T fit: take a representative structural sample so
    the LLM sees the beginning (executive summary, scope), middle pages
    (technical body), end (pricing, references, appendices), AND any
    chunks that look like section headers anywhere in between. The
    function PREFIXES the text with a notice so the LLM knows this is a
    sample of a complete document, not all the user uploaded.
    """
    chunks = (db.query(DocumentChunk)
              .filter(DocumentChunk.document_id == document_id)
              .order_by(DocumentChunk.chunk_index)
              .all())
    if not chunks:
        return ""

    def _block(c: DocumentChunk) -> str:
        text = (c.content or "").strip()
        if not text:
            return ""
        return f"[chunk {c.id}, page {c.page_number or '?'}]\n{text}"

    # Total size with framing
    total = sum(len(_block(c)) + 2 for c in chunks if (c.content or "").strip())
    if total <= max_chars:
        # Fits whole — concatenate all chunks
        parts = [_block(c) for c in chunks if (c.content or "").strip()]
        return "\n\n".join(parts)

    # Identify and skip low-information chunks: ToC dot-leaders, page-number
    # only, table/form layouts dominated by pipe characters or numbers.
    # These chunks burn budget without helping the assessor see real content.
    def _is_low_information(c: DocumentChunk) -> bool:
        text = (c.content or "").strip()
        if not text:
            return True
        if len(text) < 50:
            return True
        # Dot-leader ToC pages (e.g. "Section 1.2 ........ 14")
        dots = text.count(".")
        if dots > 0 and dots / len(text) > 0.20:
            return True
        # Pipe-heavy form/table dumps
        pipes = text.count("|")
        if pipes > 0 and pipes / len(text) > 0.05:
            return True
        # Repeated whitespace or non-letter ratio (e.g. data dumps)
        letters = sum(1 for ch in text if ch.isalpha())
        if letters / len(text) < 0.40:
            return True
        return False

    # Pre-filter chunks once
    rich_chunks = [c for c in chunks if (c.content or "").strip() and not _is_low_information(c)]
    if not rich_chunks:
        # Fallback to original behavior if our filter is too aggressive
        rich_chunks = [c for c in chunks if (c.content or "").strip()]
    chunks = rich_chunks  # rebind to filtered list for sampling
    n = len(chunks)
    if n == 0:
        return ""

    # ── Doc is too large — structural sampling ────────────────────────
    # Strategy (after filtering out ToC/forms/data tables):
    #   ~40% of budget → start of doc (exec summary, scope)
    #   ~25% of budget → end of doc (pricing, references, sign-offs)
    #   ~25% of budget → middle of doc (sampled middle chunk + neighbors)
    #   ~10% of budget → chunks that contain heading-like content
    budget_start = int(max_chars * 0.40)
    budget_end = int(max_chars * 0.25)
    budget_mid = int(max_chars * 0.25)
    budget_headings = max_chars - (budget_start + budget_end + budget_mid)

    def _take(chunks_iter, budget) -> tuple[List[str], List[int]]:
        """Greedy take chunks until budget exhausted. Returns (parts, ids_taken)."""
        out: List[str] = []
        ids: List[int] = []
        used = 0
        for c in chunks_iter:
            blk = _block(c)
            if not blk:
                continue
            if used + len(blk) > budget:
                break
            out.append(blk)
            ids.append(c.id)
            used += len(blk) + 2
        return out, ids

    chosen_ids: set[int] = set()
    parts: List[str] = []

    # Beginning
    parts.append("=== Beginning of document ===")
    p_start, ids_start = _take(chunks, budget_start)
    parts.extend(p_start)
    chosen_ids.update(ids_start)

    # End
    parts.append("\n=== End of document ===")
    p_end, ids_end = _take(reversed(chunks), budget_end)
    p_end.reverse()
    parts.extend(p_end)
    chosen_ids.update(ids_end)

    # Middle (sampled around the center)
    middle_start = max(0, n // 2 - 5)
    middle_chunks = chunks[middle_start:middle_start + 25]
    parts.append("\n=== Middle of document (sampled) ===")
    p_mid, ids_mid = _take(
        [c for c in middle_chunks if c.id not in chosen_ids],
        budget_mid,
    )
    parts.extend(p_mid)
    chosen_ids.update(ids_mid)

    # Heading-like chunks (lots of caps, numbered sections, bold-style)
    heading_re = re.compile(
        r"^\s*(?:\d+(?:\.\d+)*\s+|SECTION\s+\d+|Volume\s+[IVX]+|Appendix\s+[A-Z]|"
        r"[A-Z][A-Z\s\-:]{6,}$)",
        re.MULTILINE,
    )
    heading_chunks = [
        c for c in chunks
        if c.id not in chosen_ids
        and c.content
        and heading_re.search(c.content[:200])
    ]
    parts.append("\n=== Section headings (sampled across the doc) ===")
    p_h, _ = _take(heading_chunks[:30], budget_headings)
    parts.extend(p_h)

    n_total = len([c for c in chunks if (c.content or "").strip()])
    n_shown = len(chosen_ids) + len(p_h)
    sampling_note = (
        f"\n\n=== SAMPLING NOTE FOR THE ASSESSOR ===\n"
        f"This document has {n_total} chunks of content totaling ~{total:,} chars "
        f"and exceeds the per-prompt limit of {max_chars:,} chars. The text shown "
        f"above is a STRUCTURAL SAMPLE: beginning, end, middle, plus likely "
        f"section headings. {n_shown} chunks of {n_total} are visible in this "
        f"prompt, but the COMPLETE document is fully loaded into the application's "
        f"vector store and is searchable for downstream tasks.\n"
        f"DO NOT suggest 'upload the full proposal', 'link to the full PDF', "
        f"'attach Volume 1', or similar — the document IS uploaded and indexed in "
        f"full. The truncation here is purely for the assessment prompt size.\n"
    )
    return sampling_note + "\n\n".join(parts)


# ─────────────────────────────────────────────────────────────────────
# Quality rubric prompt
# ─────────────────────────────────────────────────────────────────────

# What "good" looks like per category — the rubric used by the LLM. New
# user-defined categories fall through to a generic rubric.
_CATEGORY_GUIDANCE = {
    "past_proposal": (
        "A past proposal should clearly identify: the agency / customer, "
        "the contract scope and value, the dates of performance, Parsons' role "
        "(prime vs sub), specific technical capabilities demonstrated, key "
        "personnel involved, performance outcomes (metrics, awards, references), "
        "and lessons learned. Strong past proposals enable an AI to substantiate "
        "Parsons' experience on a similar future RFP."
    ),
    "capability_statement": (
        "A capability statement should crisply describe Parsons' service offering "
        "in this practice area: solution components, differentiators, NAICS/SIC "
        "codes, certifications, sample customers, and quantified outcomes. It "
        "should answer 'why Parsons over a competitor?' without marketing fluff."
    ),
    "sop": (
        "An SOP should describe a repeatable process: trigger, owner, steps, "
        "tooling, quality checks, escalation paths, and acceptance criteria. "
        "Useful SOPs let the AI describe HOW Parsons would execute a requirement."
    ),
    "cert": (
        "A certification or audit should identify the issuing body, the standard "
        "(e.g. ISO 27001, SOC 2 Type II, StateRAMP), the in-scope systems and "
        "operations, the validity period, and any conditions or carve-outs."
    ),
    "case_study": (
        "A case study should tell a complete story: customer challenge, Parsons' "
        "approach, technologies used, measurable outcomes, and references. The AI "
        "should be able to lift a 'we did X, achieved Y' narrative directly."
    ),
    "pricing_history": (
        "A pricing history should detail the contract structure (T&M / FFP / IDIQ), "
        "labor rates by category, escalation factors, ODC / pass-through assumptions, "
        "win/loss outcome, and rationale for the pricing strategy."
    ),
    "org_resume": (
        "An org chart or resume should map roles to people, identify clearances / "
        "credentials, summarize relevant experience by year and customer, and "
        "name the key personnel proposed for similar future work."
    ),
}

_GENERIC_GUIDANCE = (
    "A high-quality reference document for the AI should be specific, current, "
    "self-contained, and contain quantitative anchors (dates, dollar values, "
    "performance metrics, named customers, named personnel) so the AI can cite it."
)


_SYSTEM = (
    "You are a senior Parsons capture-team SME assessing a single internal "
    "knowledge document. Your goal: judge how well the document substantiates "
    "Parsons' capabilities for the AI that will cite it during proposal "
    "drafting, and surface concrete improvements the document owner could make.\n\n"
    "CRITICAL CONTEXT — what 'uploaded' means here:\n"
    "  * The user has ALREADY uploaded the FULL document into this system.\n"
    "  * Every chunk of the document is parsed, chunked, embedded, and "
    "    available to the response-writing pipeline via vector search.\n"
    "  * Large documents may be SAMPLED in YOUR view (you may see a "
    "    'SAMPLING NOTE' section saying so) — the system has the rest.\n"
    "  * NEVER suggest 'upload the full proposal', 'link to the complete "
    "    PDF', 'attach Volume 1', 'add the Volume II appendix' or similar. "
    "    The document is uploaded in full. Suggestions about completeness "
    "    must be about CONTENT QUALITY (named customers, dates, metrics), "
    "    not about file delivery.\n\n"
    "RULES:\n"
    "1. Score honestly. A polished marketing PDF that lacks specifics scores LOWER "
    "   than a rough draft full of named customers, dates, and metrics. We need "
    "   the AI to be able to write defensible claims.\n"
    "2. Suggestions must be actionable. Each one should propose a concrete edit "
    "   the user could apply in <5 minutes. Generic 'add more detail' is useless.\n"
    "3. Cap at 6 suggestions per document; pick the highest-leverage ones.\n"
    "4. Output VALID JSON ONLY — schema below. No markdown fence, no prose outside "
    "   the JSON object.\n\n"
    "Schema:\n"
    "{\n"
    '  "quality_score": <int 0-100>,\n'
    '  "headline":     "<one-line summary <=120 chars>",\n'
    '  "rationale":    "<markdown explaining the score>",\n'
    '  "suggestions": [\n'
    '     {"severity": "critical|high|medium|low",\n'
    '      "title":     "<short title>",\n'
    '      "rationale": "<why this matters>",\n'
    '      "suggested_text": "<concrete replacement/insertion text or empty>",\n'
    '      "edit_kind": "append|replace|prepend|metadata",\n'
    '      "target_chunk_id": null | <int from supplied chunk ids>}\n'
    "  ]\n"
    "}\n"
)


def _build_user_prompt(doc: IngestedDocument, doc_text: str, category_label: str,
                       category_guidance: str) -> str:
    return (
        f"Document filename: {doc.original_filename or doc.filename or '(unnamed)'}\n"
        f"Category: {category_label}\n"
        f"Practice area: {doc.practice_area or '(none)'}\n"
        f"Description: {doc.description or '(none)'}\n\n"
        f"## Category guidance\n{category_guidance}\n\n"
        f"## Document text\n{doc_text or '(no text available)'}\n\n"
        f"Now assess. Return JSON only."
    )


def _parse_envelope(text: str) -> Optional[Dict[str, Any]]:
    """Robust JSON extractor: tolerates ```json fences and stray text."""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.+?\})\s*```", text, re.DOTALL)
    candidate = (fenced.group(1) if fenced else text).strip()
    if not candidate.startswith("{"):
        m = re.search(r"\{.+\}", candidate, re.DOTALL)
        if m:
            candidate = m.group(0)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


# ─────────────────────────────────────────────────────────────────────
# Public entry points
# ─────────────────────────────────────────────────────────────────────

def assess_quality_for_document(
    db: Session,
    document_id: int,
    actor_user_id: Optional[int] = None,
    actor_label: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the LLM rubric over a Parsons knowledge doc, persist the score
    and any pending suggestions, write an audit row.

    Returns the same shape the API surfaces back to the caller.
    """
    doc = (db.query(IngestedDocument)
           .filter(IngestedDocument.id == document_id,
                   IngestedDocument.source_type == "parsons").first())
    if not doc:
        return {"error": "Parsons document not found"}

    cat = (db.query(ParsonsDocCategory)
           .filter(ParsonsDocCategory.slug == doc.parsons_category).first())
    cat_label = cat.label if cat else (doc.parsons_category or "uncategorized")
    cat_guidance = _CATEGORY_GUIDANCE.get(doc.parsons_category, _GENERIC_GUIDANCE)

    doc_text = _load_doc_text(db, document_id)
    if not doc_text:
        return {"error": "Document has no extractable text yet — wait for ingestion to finish."}

    user_prompt = _build_user_prompt(doc, doc_text, cat_label, cat_guidance)
    raw = _call_ai(user_prompt, system=_SYSTEM)
    envelope = _parse_envelope(raw)
    if not envelope:
        return {"error": "LLM did not return a parseable assessment."}

    # Validate / clamp score
    try:
        score = int(envelope.get("quality_score") or 0)
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(100, score))

    headline = (envelope.get("headline") or "").strip()[:240] or None
    rationale = (envelope.get("rationale") or "").strip() or None

    now = datetime.utcnow()
    doc.quality_score = score
    doc.quality_headline = headline
    doc.quality_rationale = rationale
    doc.quality_assessed_at = now

    # Replace existing PENDING suggestions; preserve disposed ones.
    db.query(ParsonsDocSuggestion).filter(
        ParsonsDocSuggestion.document_id == document_id,
        ParsonsDocSuggestion.status == "pending",
    ).delete()

    suggestions_in = envelope.get("suggestions") or []
    persisted: List[Dict[str, Any]] = []
    valid_chunk_ids = {c.id for c in
                      db.query(DocumentChunk.id)
                      .filter(DocumentChunk.document_id == document_id).all()}
    for s in suggestions_in[:6]:
        if not isinstance(s, dict):
            continue
        title = (s.get("title") or "").strip()[:240]
        if not title:
            continue
        sev = (s.get("severity") or "medium").lower()
        if sev not in {"critical", "high", "medium", "low"}:
            sev = "medium"
        edit_kind = (s.get("edit_kind") or "append").lower()
        if edit_kind not in {"append", "replace", "prepend", "metadata"}:
            edit_kind = "append"
        target = s.get("target_chunk_id")
        try:
            target = int(target) if target not in (None, "") else None
        except (TypeError, ValueError):
            target = None
        if target is not None and target not in valid_chunk_ids:
            target = None
        row = ParsonsDocSuggestion(
            document_id=document_id,
            severity=sev,
            title=title,
            rationale=(s.get("rationale") or "").strip() or None,
            suggested_text=(s.get("suggested_text") or "").strip() or None,
            edit_kind=edit_kind,
            target_chunk_id=target,
            status="pending",
        )
        db.add(row)
        db.flush()
        persisted.append({
            "id": row.id, "title": title, "severity": sev,
            "edit_kind": edit_kind, "target_chunk_id": target,
        })

    write_audit(db, document_id, "assessed_quality",
                actor_user_id=actor_user_id, actor_label=actor_label,
                payload={"score": score, "headline": headline,
                         "suggestion_count": len(persisted)})

    db.commit()
    return {
        "document_id": document_id,
        "quality_score": score,
        "quality_headline": headline,
        "quality_rationale": rationale,
        "suggestions": persisted,
        "assessed_at": now.isoformat(),
    }
