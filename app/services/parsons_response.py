"""Parsons response writing service.

Implements the user's "respond to compliance items first, then assemble
section narratives from those" workflow.

Three concerns live here:

1. **Per-requirement response drafting**
   ``draft_response_for_requirement`` runs an LLM grounded in the
   relevant Parsons-knowledge chunks (filtered by claim_class affinity
   to the requirement category). Returns a paragraph response + a
   compliance disposition (Comply / Comply-with-exception / etc.) the
   user can then edit.

2. **Submission readiness telemetry**
   ``compute_submission_readiness`` rolls up requirement counts +
   response coverage + Parsons-evidence coverage into RFP-native section
   buckets. The section taxonomy is *derived from the RFP itself*
   (RfpRequirement.section_id); we do NOT manufacture sections.

3. **Section narrative assembly**
   ``assemble_section_narrative`` takes every approved per-requirement
   response in a section and lets an LLM editor stitch them into a
   single-voice narrative ready for ResponseWorkbench → export.

4. **Agent-readiness preflight**
   ``check_agent_readiness`` audits the Parsons corpus + chunk
   embeddings + coverage status for the proposal. Used both by the
   dashboard widget and as a gate before bulk drafting actions.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func as sa_func, or_
from sqlalchemy.orm import Session

from ..models import (
    CrossSectionConflict, DocumentChunk, IngestedDocument,
    ProposalSectionNarrative, ProposalSubmissionSection,
    RequirementComment, RfpRequirement,
)
from .competitor_analyst import _call_ai
from .embedding_service import cosine_similarity, embed_text
from .parsons_coverage import _load_parsons_pool

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Helpers — section grouping (RFP-native, NOT manufactured)
# ─────────────────────────────────────────────────────────────────────

def _section_root(section_id: Optional[str]) -> str:
    """Roll a deeply-nested RFP section id up to its top-level bucket so
    the dashboard doesn't drown in 2,492 micro-sections.

    Examples:
      ``"3.2.1H"`` → ``"3"``
      ``"4.9.4.2"`` → ``"4"``
      ``"Appendix 3 - Inspection (BeginTestSelection)"`` → ``"Appendix 3"``
      ``"Bid Solicitation Checklist"`` → ``"Bid Solicitation Checklist"`` (unchanged)
      ``None`` → ``"(unspecified)"``
    """
    if not section_id:
        return "(unspecified)"
    s = section_id.strip()
    # "Appendix N …" → "Appendix N"
    m = re.match(r"^(Appendix\s+\w+)", s, re.IGNORECASE)
    if m:
        return m.group(1).title()
    # Numeric-dotted: keep the first segment ("3", "4")
    m = re.match(r"^(\d+)(?:\.|$)", s)
    if m:
        return f"Section {m.group(1)}"
    return s[:60]


def _section_label(section_root: str) -> str:
    """Human-friendly label for a roll-up bucket. The naming mirrors the
    way the RFP itself talks about these so we never invent terminology."""
    return section_root


# ─────────────────────────────────────────────────────────────────────
# 1) Per-requirement response drafting (LLM)
# ─────────────────────────────────────────────────────────────────────

_DISPOSITIONS = {"Comply", "Comply-with-exception", "Take-exception",
                 "Not-Applicable", "Needs-Clarification"}

_SYSTEM_DRAFTER = (
    "You are a Parsons capture-team senior writer drafting the response to "
    "ONE specific RFP requirement. You write in a confident, plain-language "
    "voice that a state procurement evaluator would score well on a "
    "compliance matrix. You cite Parsons evidence only when it is real — "
    "never fabricate dates, dollars, customers, or capabilities not present "
    "in the supplied excerpts.\n\n"
    "Output VALID JSON ONLY with this exact shape:\n"
    "{\n"
    '  "compliance_disposition": "Comply" | "Comply-with-exception" |\n'
    '                            "Take-exception" | "Not-Applicable" |\n'
    '                            "Needs-Clarification",\n'
    '  "response": "<one or two short paragraphs answering the requirement>",\n'
    '  "evidence_doc_ids": [int, ...]   # ids cited from the Parsons pool\n'
    "}\n\n"
    "Rules:\n"
    " * If the supplied Parsons excerpts substantiate the requirement, "
    "   disposition is Comply and the response cites the evidence concretely.\n"
    " * If the requirement asks for something Parsons does not yet have "
    "   evidence for, disposition is Comply-with-exception (we'll do it but "
    "   need to bring in supporting language) — DO NOT make up specifics.\n"
    " * If Parsons cannot or should not comply, Take-exception with rationale.\n"
    " * If the requirement is procedural (forms, signatures), Not-Applicable.\n"
    " * If the requirement is ambiguous and a question was needed, Needs-Clarification.\n"
)


def _load_bundle(db: Session, requirement_id: int) -> Optional[Dict[str, Any]]:
    """Pull the precomputed context bundle if one exists.

    The bundle is built by ``scripts/context/run_all.py`` and contains
    everything the drafter needs to produce a context-rich response:
    breadcrumb path, resolved cross-references with target excerpts,
    glossary terms with definitions, the verbatim source paragraph plus
    surrounding chunks, and nearby tables.
    """
    from sqlalchemy import text as _sql_text
    row = db.execute(
        _sql_text("SELECT bundle_json FROM requirement_context_bundle WHERE requirement_id=:rid"),
        {"rid": requirement_id},
    ).first()
    if not row or not row[0]:
        return None
    try:
        return json.loads(row[0])
    except (json.JSONDecodeError, TypeError):
        return None


def _build_drafter_user_prompt(req: RfpRequirement,
                                pool_snippets: List[Dict[str, Any]],
                                bundle: Optional[Dict[str, Any]] = None) -> str:
    bits: List[str] = []
    bits.append("REQUIREMENT")
    bits.append(f"  id: {req.requirement_id or req.id}")
    bits.append(f"  section: {req.section_id or '(none)'}")
    bits.append(f"  title: {(req.title or '').strip()[:240]}")
    bits.append(f"  category: {req.category or '(unspecified)'}")
    if req.description:
        bits.append(f"  description: {req.description.strip()[:1500]}")
    if req.source_text:
        bits.append(f"  source text: {req.source_text.strip()[:1500]}")

    # Inline context bundle: breadcrumb + source paragraph + resolved
    # references + applicable defined terms. Each block is bounded so
    # the prompt stays manageable.
    if bundle:
        breadcrumb = bundle.get("breadcrumb") or []
        if breadcrumb:
            path = " > ".join(
                f"{b['section_code']}{(' ' + b['title']) if b.get('title') else ''}"
                for b in breadcrumb
            )
            bits.append(f"  position: {path}")

        src = bundle.get("source") or {}
        chunk = src.get("chunk") or {}
        if chunk.get("content"):
            bits.append("")
            bits.append(f"  surrounding paragraph (verbatim from {src.get('document_name','source')}, "
                        f"p.{chunk.get('page_number','?')}):")
            ctx = (chunk["content"] or "").strip()
            bits.append(f"    {ctx[:1800]}")

        refs = bundle.get("references") or []
        if refs:
            bits.append("")
            bits.append(f"  cross-references the requirement points at ({len(refs)}):")
            for ref in refs[:8]:
                tgt = ref.get("target") or {}
                target_str = ""
                if tgt.get("section_code"):
                    target_str = f"§{tgt['section_code']}"
                    if tgt.get("section_title"):
                        target_str += f" {tgt['section_title']}"
                elif tgt.get("document_name"):
                    target_str = tgt["document_name"]
                excerpt = (tgt.get("excerpt") or "")[:300]
                bits.append(f"    - {ref.get('label')!r} → {target_str or ref.get('target_kind','?')}")
                if excerpt:
                    bits.append(f"        excerpt: {excerpt}")

        gloss = bundle.get("glossary_terms") or []
        if gloss:
            bits.append("")
            bits.append(f"  defined terms used in this requirement ({len(gloss)}):")
            for g in gloss[:6]:
                term = g.get("term") or "?"
                defn = (g.get("definition") or "")[:240]
                bits.append(f"    - {term}: {defn}")

        tables = bundle.get("tables_in_section") or []
        if tables:
            bits.append("")
            bits.append(f"  tables in this section ({len(tables)}; preview):")
            for t in tables[:2]:
                hdr = t.get("header") or []
                if hdr:
                    bits.append(f"    - {t.get('title') or 'Table'}: cols = " + " | ".join(hdr[:6]))
                else:
                    bits.append(f"    - {t.get('title') or 'Table'} ({t.get('n_rows',0)} rows × {t.get('n_cols',0)} cols)")

    bits.append("")
    if not pool_snippets:
        bits.append("PARSONS EVIDENCE EXCERPTS")
        bits.append("(none — Parsons knowledge has no relevant matches)")
    else:
        bits.append(f"PARSONS EVIDENCE EXCERPTS ({len(pool_snippets)})")
        used = 0
        for i, h in enumerate(pool_snippets, 1):
            snippet = (h["content"] or "").strip()[:900]
            block = (
                f"-- Excerpt {i} (document_id={h['document_id']}, "
                f"sim={h['similarity']:.2f}) --\n"
                f"Source: {h['document_name']}"
                f"{(' p.' + str(h['page'])) if h.get('page') else ''}\n"
                f"{snippet}\n"
            )
            if used + len(block) > 9000:
                bits.append(f"…and {len(pool_snippets) - i + 1} more excerpts truncated.")
                break
            bits.append(block)
            used += len(block)
    bits.append("")
    bits.append("Draft the response now. Output JSON only.")
    return "\n".join(bits)


def _parse_drafter_envelope(text: str) -> Optional[Dict[str, Any]]:
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


def draft_response_for_requirement(
    db: Session,
    requirement_id: int,
    actor_user_id: Optional[int] = None,
    force: bool = False,
    review_feedback: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the LLM drafter on a single requirement, persist the result.

    Idempotent on `parsons_response_status='not_started' | 'ai_drafted' | 'rejected'`
    — `user_edited`, `approved`, `exported` responses are NOT overwritten
    unless ``force=True`` is passed.

    When ``review_feedback`` is provided (typically because a reviewer
    rejected the prior draft), it's appended to the drafter prompt so the
    next draft can address the feedback explicitly.
    """
    req = db.query(RfpRequirement).filter(
        RfpRequirement.id == requirement_id).first()
    if not req:
        return {"error": "Requirement not found"}
    if (not force) and req.parsons_response_status in ("user_edited", "approved", "exported"):
        return {"requirement_id": requirement_id,
                "skipped": True,
                "reason": f"status={req.parsons_response_status} — preserving user content (pass force=true to override)"}

    # Pull the precomputed bundle (verbatim paragraph + resolved refs +
    # glossary + tables). Its richer text becomes both the embedding query
    # for Parsons-content matching AND the LLM's surrounding context.
    bundle = _load_bundle(db, requirement_id)

    # Pull Parsons pool (global + scoped to this proposal) and rank by similarity.
    pool = _load_parsons_pool(db, req.proposal_id)
    pool_snippets: List[Dict[str, Any]] = []

    # Fit-aware filtering: when the requirement carries a form_factor tag
    # (set by scripts/context/s12_form_factor_tags.py), drop Parsons chunks
    # whose form_factor is incompatible. e.g. a "tablet" requirement should
    # not see "workstation" or "facilities" content. Untagged chunks
    # (form_factor IS NULL — general content) always survive the filter.
    req_ff = getattr(req, "form_factor", None)
    if req_ff and pool:
        # Compatibility groups: items in the same group are inter-changeable.
        compat_groups = {
            "inspection-device": {"tablet", "workstation", "mobile-lane"},
            "facility":          {"cif", "pif", "facilities"},
            "data":              {"reporting", "performance"},
            "compliance":        {"forms", "legal"},
            "ops":               {"staffing", "pricing"},
        }
        compatible = {req_ff}
        for group in compat_groups.values():
            if req_ff in group:
                compatible |= group
        before = len(pool)
        pool = [
            p for p in pool
            if (p.get("form_factor") in (None, "") or p.get("form_factor") in compatible)
        ]
        if before and before != len(pool):
            logger.info(
                "fit-aware filter: req %d (form_factor=%s) reduced Parsons pool %d → %d",
                requirement_id, req_ff, before, len(pool),
            )

    if pool:
        # Compose richer query text from the bundle when available so
        # similarity ranks pull in the right Parsons sections instead of
        # generic keyword matches.
        query_parts = [req.title, req.description, req.source_text]
        if bundle:
            chunk = (bundle.get("source") or {}).get("chunk") or {}
            if chunk.get("content"):
                query_parts.append(chunk["content"][:2000])
            for ref in (bundle.get("references") or [])[:5]:
                tgt = ref.get("target") or {}
                if tgt.get("excerpt"):
                    query_parts.append(tgt["excerpt"][:600])
            for g in (bundle.get("glossary_terms") or [])[:5]:
                if g.get("term") and g.get("definition"):
                    query_parts.append(f"{g['term']}: {g['definition'][:200]}")
        query_text = " ".join(filter(None, query_parts))[:6000]
        if query_text.strip():
            try:
                q_vec = embed_text(query_text)
                scored = []
                for item in pool:
                    sim = cosine_similarity(q_vec, item["embedding"])
                    if sim > 0.20:  # cheap floor — below this it's not relevant
                        scored.append({**item, "similarity": sim})
                scored.sort(key=lambda x: x["similarity"], reverse=True)
                pool_snippets = scored[:6]
            except Exception as e:  # noqa: BLE001
                logger.warning(f"embed_text failed in drafter: {e}")

    user_prompt = _build_drafter_user_prompt(req, pool_snippets, bundle=bundle)
    # If a reviewer kicked back the prior draft, hand the feedback to the LLM.
    feedback = (review_feedback or req.parsons_response_review_feedback or "").strip()
    if feedback:
        user_prompt += (
            "\n\nREVIEWER FEEDBACK ON PRIOR DRAFT (please address):\n"
            f"{feedback[:1500]}\n"
        )
    raw = _call_ai(user_prompt, system=_SYSTEM_DRAFTER)
    env = _parse_drafter_envelope(raw)
    if not env:
        return {"error": "LLM did not return parseable JSON",
                "raw_preview": (raw or "")[:240]}

    disposition = env.get("compliance_disposition") or "Needs-Clarification"
    if disposition not in _DISPOSITIONS:
        disposition = "Needs-Clarification"
    response = (env.get("response") or "").strip()

    # Validate cited evidence ids against the actual pool we showed the LLM
    valid_ids = {p["document_id"] for p in pool_snippets}
    cited = []
    for d in (env.get("evidence_doc_ids") or []):
        try:
            d_int = int(d)
            if d_int in valid_ids:
                cited.append(d_int)
        except (TypeError, ValueError):
            continue

    # Snapshot the actual chunks we showed the LLM so the UI can render
    # "Evidence used (6)" without re-running retrieval. We snapshot ALL
    # snippets, not just the ones the model name-checked, because grounding
    # is what the model actually saw — the user audits all of it.
    cited_snapshot = []
    for snip in pool_snippets:
        content = (snip.get("content") or "").strip()
        if len(content) > 600:
            content = content[:600].rsplit(" ", 1)[0] + "…"
        cited_snapshot.append({
            "chunk_id": snip.get("chunk_id"),
            "document_id": snip.get("document_id"),
            "document_name": snip.get("document_name"),
            "page": snip.get("page"),
            "similarity": round(float(snip.get("similarity", 0.0)), 4),
            "snippet": content,
            "model_named": snip.get("document_id") in set(cited),
        })

    now = datetime.utcnow()
    # Snapshot the FIRST AI draft so the user-edit diff has a baseline.
    if not req.parsons_response_ai_original and response:
        req.parsons_response_ai_original = response
    req.parsons_response = response or None
    req.compliance_disposition = disposition
    req.parsons_response_status = "ai_drafted"
    req.parsons_response_authored_by_user_id = None  # AI authored
    req.parsons_response_updated_at = now
    req.parsons_response_cited_evidence = (
        json.dumps(cited_snapshot) if cited_snapshot else None
    )
    # Clear consumed feedback so a re-draft doesn't keep applying it.
    req.parsons_response_review_feedback = None
    # Mark any persisted narrative for this section as stale.
    _mark_section_narrative_stale(db, req.proposal_id, _section_root(req.section_id))
    # Also fold the cited ids into parsons_evidence_doc_ids if not already
    try:
        existing = json.loads(req.parsons_evidence_doc_ids) if req.parsons_evidence_doc_ids else []
    except (json.JSONDecodeError, TypeError):
        existing = []
    merged = list(set(existing) | set(cited))
    req.parsons_evidence_doc_ids = json.dumps(merged) if merged else None
    db.commit()

    return {
        "requirement_id": req.id,
        "compliance_disposition": disposition,
        "parsons_response": response,
        "evidence_doc_ids": cited,
        "evidence_chunks": cited_snapshot,
        "status": req.parsons_response_status,
        "updated_at": now.isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────
# 2) Submission readiness — RFP-native section rollup
# ─────────────────────────────────────────────────────────────────────

def compute_submission_readiness(
    db: Session,
    proposal_id: int,
) -> Dict[str, Any]:
    """Roll up every requirement into its RFP-native section bucket and
    compute (a) response coverage and (b) Parsons-evidence coverage.

    Bucketing strategy:
      * If the proposal has an extracted ``ProposalSubmissionSection``
        structure AND requirements have been mapped (``submission_section_slug``),
        use the RFP's own labels — exactly what the RFP says about how
        the vendor must submit (e.g. "Forms", "Technical Quote",
        "State-Supplied Price Sheet").
      * Otherwise fall back to the regex-derived ``_section_root``.

    A "structure_source" field is included in the response so the UI
    can show "RFP-defined" vs "auto-derived" badges.
    """
    rows = (db.query(RfpRequirement)
            .filter(RfpRequirement.proposal_id == proposal_id)
            .all())
    if not rows:
        return {"proposal_id": proposal_id, "total_requirements": 0,
                "sections": [], "overall": {},
                "structure_source": "none"}

    # Determine bucketing source: RFP-defined or fallback regex.
    structure_rows = (db.query(ProposalSubmissionSection)
                      .filter(ProposalSubmissionSection.proposal_id == proposal_id)
                      .filter(ProposalSubmissionSection.is_top_level.is_(True))
                      .order_by(ProposalSubmissionSection.order_index).all())
    have_structure = len(structure_rows) > 0
    mapped_count = sum(1 for r in rows if r.submission_section_slug)
    use_structure = have_structure and mapped_count > 0

    structure_source = ("rfp_defined" if use_structure
                        else "auto_derived" if rows
                        else "none")

    # Build slug→meta lookup for structure-driven path
    slug_to_meta: Dict[str, Dict[str, Any]] = {}
    for s in structure_rows:
        slug_to_meta[s.slug] = {
            "section_root": s.slug,
            "label": s.label,
            "rfp_section_ref": s.rfp_section_ref,
            "description": s.description,
            "order_index": s.order_index,
        }
    # Synthetic bucket for unmapped rows when structure exists
    UNMAPPED_SLUG = "(unmapped)"

    by_root: Dict[str, Dict[str, Any]] = {}

    def _bucket_init(root_key: str, meta: Optional[Dict[str, Any]] = None):
        return {
            "section_root": root_key,
            "label": (meta and meta.get("label")) or _section_label(root_key),
            "rfp_section_ref": (meta and meta.get("rfp_section_ref")),
            "description": (meta and meta.get("description")),
            "order_index": (meta and meta.get("order_index", 999)) or 999,
            "total": 0,
            "by_response_status": {"not_started": 0, "ai_drafted": 0,
                                    "user_edited": 0, "approved": 0,
                                    "exported": 0},
            "by_disposition": {},
            "by_parsons_coverage": {"covered": 0, "partial": 0, "gap": 0,
                                     "uncertain": 0, "not_assessed": 0},
            "by_priority": {},
        }

    # Pre-create all top-level structure buckets so they appear even with 0 requirements
    if use_structure:
        for s in structure_rows:
            by_root[s.slug] = _bucket_init(s.slug, slug_to_meta[s.slug])
        by_root[UNMAPPED_SLUG] = _bucket_init(UNMAPPED_SLUG, {
            "label": "Unmapped (informational / out-of-scope)",
            "order_index": 9999,
        })

    for r in rows:
        if use_structure:
            root = r.submission_section_slug or UNMAPPED_SLUG
            meta = slug_to_meta.get(root)
            b = by_root.setdefault(root, _bucket_init(root, meta))
        else:
            root = _section_root(r.section_id)
            b = by_root.setdefault(root, _bucket_init(root))
        b["total"] += 1
        rs = r.parsons_response_status or "not_started"
        b["by_response_status"][rs] = b["by_response_status"].get(rs, 0) + 1
        if r.compliance_disposition:
            b["by_disposition"][r.compliance_disposition] = (
                b["by_disposition"].get(r.compliance_disposition, 0) + 1)
        cov = r.parsons_coverage_status or "not_assessed"
        b["by_parsons_coverage"][cov] = b["by_parsons_coverage"].get(cov, 0) + 1
        if r.priority:
            b["by_priority"][r.priority] = b["by_priority"].get(r.priority, 0) + 1

    # Per-section computed fields
    sections = []
    overall = {"total": 0, "drafted": 0, "approved": 0, "covered": 0,
               "gap": 0}
    if use_structure:
        sort_key = lambda x: (by_root[x].get("order_index", 999), x)
    else:
        sort_key = lambda x: (x == "(unspecified)", x.lower())
    for root in sorted(by_root.keys(), key=sort_key):
        b = by_root[root]
        drafted = (b["by_response_status"].get("ai_drafted", 0)
                   + b["by_response_status"].get("user_edited", 0)
                   + b["by_response_status"].get("approved", 0)
                   + b["by_response_status"].get("exported", 0))
        approved = (b["by_response_status"].get("approved", 0)
                    + b["by_response_status"].get("exported", 0))
        covered = (b["by_parsons_coverage"].get("covered", 0)
                   + b["by_parsons_coverage"].get("partial", 0))
        gap = b["by_parsons_coverage"].get("gap", 0)
        total = b["total"]
        b["drafted_count"] = drafted
        b["approved_count"] = approved
        b["evidence_covered_count"] = covered
        b["gap_count"] = gap
        b["pct_drafted"] = int(round((drafted / total) * 100)) if total else 0
        b["pct_approved"] = int(round((approved / total) * 100)) if total else 0
        b["pct_evidence_covered"] = int(round((covered / total) * 100)) if total else 0
        # Readiness signal — green/amber/red derived from the worst metric.
        # Green = ≥80% drafted AND ≥70% evidence covered
        # Red = <30% drafted OR ≥25% in `gap`
        # Amber = anything else
        if total == 0:
            b["readiness"] = "unknown"
        elif b["pct_drafted"] >= 80 and b["pct_evidence_covered"] >= 70:
            b["readiness"] = "green"
        elif b["pct_drafted"] < 30 or (gap / total) >= 0.25:
            b["readiness"] = "red"
        else:
            b["readiness"] = "amber"
        sections.append(b)
        overall["total"] += total
        overall["drafted"] += drafted
        overall["approved"] += approved
        overall["covered"] += covered
        overall["gap"] += gap

    if overall["total"]:
        overall["pct_drafted"] = int(round((overall["drafted"] / overall["total"]) * 100))
        overall["pct_approved"] = int(round((overall["approved"] / overall["total"]) * 100))
        overall["pct_evidence_covered"] = int(round((overall["covered"] / overall["total"]) * 100))
        overall["pct_gap"] = int(round((overall["gap"] / overall["total"]) * 100))

    return {
        "proposal_id": proposal_id,
        "total_requirements": len(rows),
        "section_count": len(sections),
        "sections": sections,
        "overall": overall,
        "structure_source": structure_source,
        "mapped_count": mapped_count,
        "structure_top_level_count": len(structure_rows),
    }


# ─────────────────────────────────────────────────────────────────────
# 3) Section narrative assembly
# ─────────────────────────────────────────────────────────────────────

_SYSTEM_ASSEMBLER = (
    "You are a senior Parsons proposal editor. You receive a list of "
    "individual per-requirement responses (each already addressing one RFP "
    "requirement) and weave them into a single coherent section narrative. "
    "Preserve every factual claim; do NOT add new claims; remove only "
    "redundancy. Use a confident, plain-language voice. Output markdown only "
    "(no JSON, no fence)."
)


def assemble_section_narrative(
    db: Session,
    proposal_id: int,
    section_root: str,
    persist: bool = False,
    actor_user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Pull every drafted/approved per-requirement response in the given
    RFP-native section and ask the editor LLM to weave them into one
    narrative.

    By default the result is RETURNED only — caller can preview before
    committing. When ``persist=True`` the narrative is upserted into
    ``proposal_section_narratives`` and ``is_stale`` is cleared."""
    rows = (db.query(RfpRequirement)
            .filter(RfpRequirement.proposal_id == proposal_id)
            .all())
    in_section = [r for r in rows if _section_root(r.section_id) == section_root]
    drafted = [r for r in in_section
               if r.parsons_response and r.parsons_response_status in
               ("ai_drafted", "user_edited", "approved", "exported")]
    if not drafted:
        return {"section_root": section_root,
                "drafted_count": 0,
                "narrative": "",
                "warning": "No drafted responses to assemble in this section."}

    # Cap so we don't blow the prompt window. 60 reqs * ~250 chars each = 15k.
    cap = 60
    payload_bits: List[str] = []
    payload_bits.append(f"Section: {section_root}")
    payload_bits.append(f"Per-requirement responses ({min(len(drafted), cap)} of {len(drafted)})")
    for r in drafted[:cap]:
        payload_bits.append(
            f"--- {r.section_id or '(no id)'} | {r.requirement_id or r.id} | "
            f"{r.compliance_disposition or '?'} ---"
        )
        payload_bits.append((r.title or "").strip()[:200])
        payload_bits.append((r.parsons_response or "").strip()[:900])
        payload_bits.append("")
    if len(drafted) > cap:
        payload_bits.append(f"…and {len(drafted) - cap} more responses truncated for prompt size.")
    payload_bits.append("")
    payload_bits.append(
        "Weave these into a single coherent section narrative. Markdown only."
    )

    user_prompt = "\n".join(payload_bits)
    narrative = _call_ai(user_prompt, system=_SYSTEM_ASSEMBLER) or ""
    narrative = narrative.strip()
    req_ids = [r.id for r in drafted]

    if persist and narrative:
        now = datetime.utcnow()
        existing = (db.query(ProposalSectionNarrative)
                    .filter(ProposalSectionNarrative.proposal_id == proposal_id,
                            ProposalSectionNarrative.section_root == section_root)
                    .first())
        if existing:
            existing.narrative_md = narrative
            existing.requirement_ids = json.dumps(req_ids)
            existing.last_assembled_at = now
            existing.is_stale = False
        else:
            existing = ProposalSectionNarrative(
                proposal_id=proposal_id,
                section_root=section_root,
                narrative_md=narrative,
                requirement_ids=json.dumps(req_ids),
                last_assembled_at=now,
                is_stale=False,
            )
            db.add(existing)
        db.commit()
        db.refresh(existing)
        return {
            "section_root": section_root,
            "drafted_count": len(drafted),
            "requirement_ids": req_ids,
            "narrative": narrative,
            "persisted": True,
            "narrative_id": existing.id,
            "last_assembled_at": now.isoformat(),
        }

    return {
        "section_root": section_root,
        "drafted_count": len(drafted),
        "requirement_ids": req_ids,
        "narrative": narrative,
        "persisted": False,
    }


_SYSTEM_REQ_CHAT = (
    "You are a Parsons capture-team senior writer. The user is iterating "
    "on the response to ONE specific RFP requirement and wants to "
    "strengthen it. You have access to: (a) the RFP requirement itself, "
    "(b) the current draft response, and (c) Parsons-knowledge excerpts "
    "retrieved for this requirement. Help the user critique, improve, "
    "and rewrite the draft. Cite Parsons evidence concretely (mention the "
    "document name and page when you do); never fabricate dates, dollars, "
    "customers, or capabilities not present in the supplied excerpts. "
    "When the user asks for a rewrite, output the rewritten paragraph "
    "in a fenced ```draft block so the UI can offer a one-click apply."
)


def chat_about_requirement(
    db: Session,
    requirement_id: int,
    user_message: str,
    history: Optional[List[Dict[str, str]]] = None,
    top_k: int = 6,
) -> Dict[str, Any]:
    """Conversational chat scoped to a single requirement. Mirrors the
    RFP Ask agent's contract: { answer, citations, history } so the UI
    can render the same way.

    The model sees the requirement text, the current draft response, and
    the top-K Parsons-pool snippets ranked by similarity to the user's
    question + the requirement together (so "How can I strengthen this?"
    still pulls the right evidence even though the user's words don't
    contain the requirement keywords).
    """
    req = db.query(RfpRequirement).filter(
        RfpRequirement.id == requirement_id).first()
    if not req:
        return {"error": "Requirement not found"}
    if not (user_message or "").strip():
        return {"error": "Empty message"}

    # Retrieval — combine the user's message with the requirement context
    # so the embedding lookup grounds in the requirement even when the
    # user's prose ("strengthen this", "shorten") doesn't carry signal.
    query_text = " ".join(filter(None, [
        user_message[:1000],
        req.title,
        req.description,
        req.source_text,
    ]))[:4000]

    pool = _load_parsons_pool(db, req.proposal_id)
    snippets: List[Dict[str, Any]] = []
    if pool and query_text.strip():
        try:
            q_vec = embed_text(query_text)
            scored = []
            for item in pool:
                sim = cosine_similarity(q_vec, item["embedding"])
                if sim > 0.18:
                    scored.append({**item, "similarity": sim})
            scored.sort(key=lambda x: x["similarity"], reverse=True)
            snippets = scored[:max(1, min(top_k, 12))]
        except Exception as e:  # noqa: BLE001
            logger.warning(f"chat_about_requirement embedding failed: {e}")

    # Build user prompt
    bits: List[str] = []
    bits.append("REQUIREMENT")
    bits.append(f"  id: {req.requirement_id or req.id}")
    bits.append(f"  section: {req.section_id or '(none)'}")
    bits.append(f"  title: {(req.title or '').strip()[:240]}")
    if req.description:
        bits.append(f"  description: {req.description.strip()[:1500]}")
    if req.source_text:
        bits.append(f"  source text: {req.source_text.strip()[:1500]}")
    bits.append("")
    bits.append("CURRENT DRAFT RESPONSE")
    bits.append((req.parsons_response or "(no draft yet — user has not run Generate Response)").strip()[:3000])
    bits.append("")
    if snippets:
        bits.append(f"PARSONS EVIDENCE EXCERPTS ({len(snippets)})")
        used = 0
        for i, h in enumerate(snippets, 1):
            content = (h.get("content") or "").strip()[:900]
            block = (
                f"-- Excerpt {i} (document_id={h['document_id']}, "
                f"sim={h['similarity']:.2f}) --\n"
                f"Source: {h['document_name']}"
                f"{(' p.' + str(h['page'])) if h.get('page') else ''}\n"
                f"{content}\n"
            )
            if used + len(block) > 9000:
                bits.append(f"…and {len(snippets) - i + 1} more excerpts truncated.")
                break
            bits.append(block)
            used += len(block)
    else:
        bits.append("PARSONS EVIDENCE EXCERPTS")
        bits.append("(none — Parsons knowledge has no relevant matches for this query)")
    bits.append("")
    if history:
        bits.append("PRIOR CONVERSATION (oldest first)")
        for h in history[-12:]:
            role = (h.get("role") or "").strip().lower()
            content = (h.get("content") or "").strip()
            if not role or not content:
                continue
            bits.append(f"  [{role}] {content[:1500]}")
        bits.append("")
    bits.append(f"USER MESSAGE: {user_message.strip()[:2000]}")
    bits.append("")
    bits.append(
        "Respond helpfully. If suggesting a rewrite, place the rewritten "
        "paragraph inside a ```draft fenced block. Otherwise plain markdown."
    )
    user_prompt = "\n".join(bits)

    raw = _call_ai(user_prompt, system=_SYSTEM_REQ_CHAT) or ""
    answer = raw.strip()

    # Pull out a suggested-rewrite block if present.
    rewrite = None
    m = re.search(r"```(?:draft|response)\s*\n(.*?)\n```", answer, re.DOTALL | re.IGNORECASE)
    if m:
        rewrite = m.group(1).strip()

    citations = [
        {
            "document_id": s.get("document_id"),
            "document_name": s.get("document_name"),
            "page": s.get("page"),
            "similarity": round(float(s.get("similarity", 0.0)), 4),
            "snippet": (s.get("content") or "").strip()[:400],
        }
        for s in snippets
    ]

    return {
        "requirement_id": req.id,
        "answer": answer,
        "suggested_rewrite": rewrite,
        "citations": citations,
        "evidence_count": len(citations),
    }


def _mark_section_narrative_stale(db: Session, proposal_id: int,
                                   section_root: str) -> None:
    """Mark any persisted narrative for the given section as stale.
    Caller is responsible for db.commit() — we only flip the flag.
    Tolerant of section_root being empty/(unspecified)."""
    if not proposal_id or not section_root:
        return
    try:
        n = (db.query(ProposalSectionNarrative)
             .filter(ProposalSectionNarrative.proposal_id == proposal_id,
                     ProposalSectionNarrative.section_root == section_root)
             .first())
        if n and not n.is_stale:
            n.is_stale = True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"_mark_section_narrative_stale failed: {e}")


# ─────────────────────────────────────────────────────────────────────
# 4) Agent-readiness preflight
# ─────────────────────────────────────────────────────────────────────

def check_agent_readiness(
    db: Session,
    proposal_id: int,
) -> Dict[str, Any]:
    """Audit the Parsons corpus + chunk embeddings + coverage status for
    the proposal. Returns a structured preflight report the dashboard
    can render and the drafter can gate on.

    Surfaces:
      * Parsons doc inventory (global + scoped); how many are 'completed' / 'failed'
      * Chunk-without-embedding count (silent enrichment failure)
      * Stuck-ingest docs (status='processing' for hours)
      * Coverage gap count
      * Overall traffic light + actionable issue list
    """
    issues: List[Dict[str, Any]] = []

    # Parsons docs visible to this proposal
    docs_q = (db.query(IngestedDocument)
              .filter(IngestedDocument.source_type == "parsons")
              .filter(or_(
                  IngestedDocument.parsons_scope_proposal_id.is_(None),
                  IngestedDocument.parsons_scope_proposal_id == proposal_id,
              )))
    parsons_docs = docs_q.all()
    parsons_total = len(parsons_docs)
    parsons_completed = sum(1 for d in parsons_docs if d.status == "completed")
    parsons_failed = sum(1 for d in parsons_docs if d.status == "failed")
    parsons_processing = sum(1 for d in parsons_docs if d.status == "processing")

    # Per-category breakdown so the dashboard can SHOW the user that
    # past_proposal / capability_statement / SOP / cert docs are
    # contributing. This was the user's "I uploaded past responses but
    # they don't appear to be considered" complaint — they ARE considered,
    # but the UI didn't surface it.
    category_breakdown: Dict[str, Dict[str, Any]] = {}
    for d in parsons_docs:
        cat = d.parsons_category or "uncategorized"
        e = category_breakdown.setdefault(cat, {
            "category": cat,
            "doc_count": 0,
            "chunk_count": 0,
            "chunks_with_embedding": 0,
            "scope_global": 0,
            "scope_proposal": 0,
        })
        e["doc_count"] += 1
        e["chunk_count"] += d.total_chunks or 0
        if d.parsons_scope_proposal_id is None:
            e["scope_global"] += 1
        else:
            e["scope_proposal"] += 1

    # Fill chunks_with_embedding per category — single SQL pass keyed by category
    if parsons_docs:
        ids = [d.id for d in parsons_docs]
        cat_by_doc = {d.id: (d.parsons_category or "uncategorized") for d in parsons_docs}
        for chunk_doc, count in (
            db.query(DocumentChunk.document_id, sa_func.count(DocumentChunk.id))
            .filter(DocumentChunk.document_id.in_(ids))
            .filter(DocumentChunk.embedding.isnot(None))
            .group_by(DocumentChunk.document_id).all()
        ):
            cat = cat_by_doc.get(chunk_doc)
            if cat and cat in category_breakdown:
                category_breakdown[cat]["chunks_with_embedding"] += int(count)

    # Standard categories the rubric expects — flag any that are missing.
    EXPECTED_CATEGORIES = ["past_proposal", "capability_statement", "sop",
                            "cert", "case_study"]
    missing_categories = [c for c in EXPECTED_CATEGORIES
                          if c not in category_breakdown]
    if missing_categories and parsons_total > 0:
        issues.append({"severity": "medium", "key": "missing_categories",
                       "label": f"No documents tagged as: {', '.join(missing_categories)}.",
                       "fix": "Tag the relevant documents in Parsons Knowledge "
                              "or upload missing categories. The drafter weighs "
                              "evidence types differently — a balanced corpus "
                              "produces stronger responses."})

    if parsons_total == 0:
        issues.append({"severity": "high", "key": "no_parsons_corpus",
                       "label": "No Parsons knowledge documents are available — "
                                "the AI has nothing to cite.",
                       "fix": "Upload past proposals, capability statements, "
                               "SOPs, certs to Parsons Knowledge."})
    elif "past_proposal" not in category_breakdown:
        issues.append({"severity": "high", "key": "no_past_proposals",
                       "label": "No past proposals available — the AI has no "
                                "prior winning bid language to learn from.",
                       "fix": "Upload at least one past proposal to "
                              "Parsons Knowledge, tagged as 'past_proposal'."})
    if parsons_failed:
        issues.append({"severity": "high", "key": "parsons_ingest_failed",
                       "label": f"{parsons_failed} Parsons doc(s) failed ingest.",
                       "fix": "Open Parsons Knowledge → delete + re-upload."})
    if parsons_processing:
        issues.append({"severity": "medium", "key": "parsons_processing_stuck",
                       "label": f"{parsons_processing} Parsons doc(s) still processing.",
                       "fix": "Wait or re-trigger ingestion."})

    # Embedding-health audit — count chunks WITHOUT embeddings on parsons docs
    if parsons_docs:
        ids = [d.id for d in parsons_docs]
        total_chunks = (db.query(sa_func.count(DocumentChunk.id))
                        .filter(DocumentChunk.document_id.in_(ids))
                        .scalar() or 0)
        chunks_without_embed = (db.query(sa_func.count(DocumentChunk.id))
                                 .filter(DocumentChunk.document_id.in_(ids))
                                 .filter(DocumentChunk.embedding.is_(None))
                                 .scalar() or 0)
    else:
        total_chunks = 0
        chunks_without_embed = 0

    if total_chunks > 0 and chunks_without_embed > 0:
        pct = int(round((chunks_without_embed / total_chunks) * 100))
        sev = "high" if pct >= 50 else "medium"
        issues.append({"severity": sev, "key": "missing_embeddings",
                       "label": f"{chunks_without_embed} of {total_chunks} "
                                f"Parsons chunks ({pct}%) are missing embeddings — "
                                f"semantic search and coverage assessment will miss them.",
                       "fix": "Run embedding-health repair (POST /api/parsons-knowledge/"
                              "repair-embeddings) or delete + re-upload affected docs."})

    # Coverage status on requirements
    reqs = (db.query(RfpRequirement)
            .filter(RfpRequirement.proposal_id == proposal_id).all())
    req_total = len(reqs)
    not_assessed = sum(1 for r in reqs
                       if (r.parsons_coverage_status or "not_assessed") == "not_assessed")
    gap = sum(1 for r in reqs if r.parsons_coverage_status == "gap")

    if req_total == 0:
        issues.append({"severity": "high", "key": "no_requirements",
                       "label": "No RFP requirements have been extracted yet.",
                       "fix": "Run requirement extraction on the RFP doc."})
    elif not_assessed and not_assessed >= req_total * 0.20:
        issues.append({"severity": "medium", "key": "coverage_unrun",
                       "label": f"{not_assessed} of {req_total} requirements "
                                f"have no Parsons-coverage assessment yet.",
                       "fix": "Run assess-coverage from the Parsons Knowledge "
                              "page."})
    if gap and req_total and (gap / req_total) >= 0.25:
        issues.append({"severity": "high", "key": "high_gap_density",
                       "label": f"{gap} requirements ({int((gap/req_total)*100)}%) have NO Parsons evidence.",
                       "fix": "Upload more Parsons knowledge OR mark these "
                              "requirements as Take-exception."})

    drafted = sum(1 for r in reqs if (r.parsons_response_status or "not_started") != "not_started")

    # Overall verdict
    high_count = sum(1 for i in issues if i["severity"] == "high")
    med_count = sum(1 for i in issues if i["severity"] == "medium")
    if high_count > 0:
        verdict = "red"
        verdict_label = "AI is NOT ready to draft."
    elif med_count > 0:
        verdict = "amber"
        verdict_label = "AI can draft with caveats — fix flagged issues for best results."
    else:
        verdict = "green"
        verdict_label = "AI is ready to draft."

    # Sort category breakdown by canonical order, then alpha for unknowns
    CATEGORY_ORDER = {
        "past_proposal": 0, "capability_statement": 1, "sop": 2,
        "cert": 3, "case_study": 4, "pricing_history": 5,
        "org_resume": 6, "uncategorized": 99,
    }
    category_list = sorted(
        category_breakdown.values(),
        key=lambda c: (CATEGORY_ORDER.get(c["category"], 50), c["category"]),
    )

    return {
        "proposal_id": proposal_id,
        "verdict": verdict,
        "verdict_label": verdict_label,
        "summary": {
            "parsons_docs_total": parsons_total,
            "parsons_docs_completed": parsons_completed,
            "parsons_docs_failed": parsons_failed,
            "parsons_docs_processing": parsons_processing,
            "parsons_chunks_total": total_chunks,
            "parsons_chunks_without_embedding": chunks_without_embed,
            "requirements_total": req_total,
            "requirements_drafted": drafted,
            "requirements_coverage_gap": gap,
            "requirements_coverage_unrun": not_assessed,
        },
        "parsons_categories": category_list,
        "issues": issues,
        "checked_at": datetime.utcnow().isoformat(),
    }
