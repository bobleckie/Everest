"""Parsons-knowledge coverage assessment.

Given an RFP document's extracted requirements, this service:
  1. Pulls all Parsons-tagged document chunks (global + scoped to this proposal)
  2. For each requirement, vector-searches the Parsons pool for relevant chunks
  3. Asks an LLM to grade the match using a strict rubric:
        covered     — direct match in Parsons knowledge with high confidence
        partial     — some matches but real gaps remain
        gap         — no relevant Parsons evidence found
        uncertain   — match exists but LLM can't tell if it actually answers
  4. Persists the verdict + cited evidence on each RfpRequirement so the
     compliance matrix can render coverage chips and the dashboard can
     compute the gap KPI.

Vector search uses the existing ChromaDB-free pipeline already wired into
DocumentChunk.embedding (JSON-encoded float arrays from embed_texts()).
We do an in-memory cosine-similarity scan because the Parsons pool is
small (single-digit-MB of doc chunks for the foreseeable future) — a
proper ANN index isn't worth the operational cost yet.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from ..models import (
    DocumentChunk, IngestedDocument, RfpRequirement,
)
from .embedding_service import embed_text, cosine_similarity
from .competitor_analyst import _call_ai

logger = logging.getLogger(__name__)


# How many top-similarity chunks to feed the grading LLM per requirement.
# Higher = better grounding but more tokens. 5 is a good cost/quality balance.
TOP_K_CHUNKS = 5
# Minimum cosine sim for a chunk to count as "relevant at all". Below this
# threshold we skip the LLM call and grade as `gap` directly. This keeps cost
# low on requirements where the Parsons library has nothing remotely related.
MIN_SIMILARITY_FOR_LLM = 0.25

# Bound the snippet feed to the LLM so we don't blow the context window.
MAX_CHARS_PER_CHUNK = 1200
MAX_TOTAL_PROMPT_CHARS = 12_000


# ─────────────────────────────────────────────────────────────────────
# Pool loaders
# ─────────────────────────────────────────────────────────────────────

def _load_parsons_pool(db: Session, proposal_id: Optional[int]) -> List[Dict[str, Any]]:
    """Build the in-memory pool of Parsons knowledge chunks visible to the
    given proposal. Includes:
      * global Parsons docs (parsons_scope_proposal_id IS NULL)
      * proposal-scoped Parsons docs (parsons_scope_proposal_id == proposal_id)
    EXCLUDES:
      * superseded docs (superseded_by_document_id IS NOT NULL) — the
        successor doc represents the current truth; old versions stay
        in the DB for audit but are filtered out of retrieval.
    Each entry: {chunk_id, document_id, document_name, content, embedding,
                 page, parsons_category}.
    """
    docs_q = (db.query(IngestedDocument)
              .filter(IngestedDocument.source_type == "parsons")
              .filter(IngestedDocument.superseded_by_document_id.is_(None)))
    if proposal_id is not None:
        from sqlalchemy import or_
        docs_q = docs_q.filter(or_(
            IngestedDocument.parsons_scope_proposal_id.is_(None),
            IngestedDocument.parsons_scope_proposal_id == proposal_id,
        ))
    else:
        docs_q = docs_q.filter(IngestedDocument.parsons_scope_proposal_id.is_(None))
    docs = {d.id: d for d in docs_q.all()}
    if not docs:
        return []

    chunks = (db.query(DocumentChunk)
              .filter(DocumentChunk.document_id.in_(docs.keys()))
              .all())
    pool: List[Dict[str, Any]] = []
    for c in chunks:
        if not c.embedding:
            # Document was uploaded but never embedded — coverage assessment
            # should not silently include it. Caller can re-run enrich_chunks
            # to populate embeddings if this becomes an issue.
            continue
        try:
            vec = json.loads(c.embedding)
        except (json.JSONDecodeError, TypeError):
            continue
        d = docs[c.document_id]
        pool.append({
            "chunk_id": c.id,
            "document_id": c.document_id,
            "document_name": d.original_filename or d.filename or f"doc {c.document_id}",
            "content": c.content or "",
            "embedding": vec,
            "page": c.page_number,
            "parsons_category": getattr(d, "parsons_category", None),
            "scope": "proposal" if d.parsons_scope_proposal_id else "global",
            "form_factor": getattr(c, "form_factor", None),
        })
    return pool


# ─────────────────────────────────────────────────────────────────────
# LLM rubric
# ─────────────────────────────────────────────────────────────────────

_RUBRIC_SYSTEM = (
    "You are a Parsons capture-team SME assessing whether the company has "
    "evidence to respond to a single RFP requirement. You read the requirement "
    "and a small bundle of EXCERPTS from existing Parsons knowledge documents "
    "(past proposals, capability statements, SOPs, certifications, case "
    "studies, etc.), and you grade coverage strictly per the rubric below. "
    "Be honest about gaps — fabricating coverage is far worse than reporting "
    "an honest gap, because the capture team will deliver bad responses if "
    "you say 'covered' on something we cannot actually substantiate.\n\n"
    "Rubric:\n"
    "  covered    — at least one excerpt directly substantiates the requirement; "
    "                a competent SME could draft a response from the excerpts alone.\n"
    "  partial    — some excerpts touch on the requirement but key specifics "
    "                are missing (e.g. requirement asks for Class 8 vehicles but "
    "                Parsons evidence only shows Class 4-6 work).\n"
    "  gap        — no excerpt addresses the requirement; SME must write from "
    "                scratch or upload more knowledge.\n"
    "  uncertain  — excerpts are tangentially related but it is genuinely "
    "                unclear whether they answer the requirement; needs SME review.\n\n"
    "Output a single valid JSON object on its own line, no markdown fence:\n"
    "{\n"
    '  "status": "covered" | "partial" | "gap" | "uncertain",\n'
    '  "rationale": "<one or two sentences explaining the verdict>",\n'
    '  "cited_doc_ids": [int, ...]   // subset of the supplied document_id values\n'
    "}\n"
)


def _build_rubric_user_prompt(req: RfpRequirement, hits: List[Dict[str, Any]]) -> str:
    """Compose the per-requirement user message: the requirement text + top-k
    similar Parsons excerpts. Each excerpt carries its document_id so the
    LLM can cite specific docs back."""
    lines = []
    lines.append("REQUIREMENT")
    lines.append(f"  id: {req.requirement_id or req.id}")
    lines.append(f"  title: {(req.title or '').strip()[:240]}")
    lines.append(f"  category: {req.category or 'unspecified'}")
    if req.description:
        lines.append(f"  description: {req.description.strip()[:1500]}")
    if req.source_text:
        lines.append(f"  source text: {req.source_text.strip()[:1500]}")
    lines.append("")
    lines.append("PARSONS EVIDENCE EXCERPTS (top similar chunks, " +
                 f"{len(hits)} shown)")
    used = 0
    for i, h in enumerate(hits, 1):
        snippet = (h["content"] or "").strip()[:MAX_CHARS_PER_CHUNK]
        block = (
            f"--- Excerpt {i} (document_id={h['document_id']}, "
            f"category={h.get('parsons_category') or 'uncategorized'}, "
            f"scope={h.get('scope', 'global')}, "
            f"sim={h['similarity']:.2f}) ---\n"
            f"Source: {h['document_name']}"
            + (f" p.{h['page']}" if h.get('page') else "") + "\n"
            f"{snippet}\n"
        )
        if used + len(block) > MAX_TOTAL_PROMPT_CHARS:
            lines.append(f"…and {len(hits) - i + 1} more excerpts truncated for prompt size.")
            break
        lines.append(block)
        used += len(block)
    lines.append("")
    lines.append("Grade this requirement now. Output JSON only.")
    return "\n".join(lines)


def _parse_rubric_json(text: str) -> Optional[Dict[str, Any]]:
    """Extract the verdict JSON from the LLM response — robust to a stray
    markdown fence or trailing chatter."""
    if not text:
        return None
    import re
    fenced = re.search(r"```(?:json)?\s*(\{.+?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    candidate = candidate.strip()
    # Find the first balanced top-level object if the model padded text.
    if not candidate.startswith("{"):
        m = re.search(r"\{.+\}", candidate, re.DOTALL)
        if m:
            candidate = m.group(0)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def _grade_one(
    req: RfpRequirement,
    pool: List[Dict[str, Any]],
) -> Tuple[str, Optional[str], List[int]]:
    """Vector-search + LLM-grade one requirement. Returns
    (status, rationale, cited_doc_ids).
    """
    if not pool:
        return ("gap",
                "No Parsons knowledge documents are available for this proposal.",
                [])

    query_text = " ".join(filter(None, [
        req.title, req.description, req.source_text,
    ]))[:4000]
    if not query_text.strip():
        return ("uncertain",
                "Requirement text is empty — cannot vector-search.",
                [])

    try:
        q_vec = embed_text(query_text)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"embed_text failed for req {req.id}: {e}")
        return ("uncertain",
                f"Could not embed requirement text: {type(e).__name__}",
                [])

    scored: List[Dict[str, Any]] = []
    for item in pool:
        sim = cosine_similarity(q_vec, item["embedding"])
        scored.append({**item, "similarity": sim})
    scored.sort(key=lambda x: x["similarity"], reverse=True)
    top = scored[:TOP_K_CHUNKS]

    if not top or top[0]["similarity"] < MIN_SIMILARITY_FOR_LLM:
        return ("gap",
                f"No Parsons excerpt scored above the relevance threshold "
                f"(top similarity {top[0]['similarity']:.2f} if any).",
                [])

    # Single LLM call to grade. Keep cheap by using whatever model is
    # configured — the rubric is small enough that the regular Sonnet/4o
    # tier is fine.
    user_prompt = _build_rubric_user_prompt(req, top)
    raw = _call_ai(user_prompt, system=_RUBRIC_SYSTEM)
    verdict = _parse_rubric_json(raw)
    if not verdict:
        return ("uncertain",
                "LLM did not return parseable JSON — defaulting to uncertain.",
                [t["document_id"] for t in top])

    status = (verdict.get("status") or "uncertain").lower()
    if status not in {"covered", "partial", "gap", "uncertain"}:
        status = "uncertain"
    rationale = (verdict.get("rationale") or "").strip()[:1000] or None
    cited_raw = verdict.get("cited_doc_ids") or []
    valid_ids = {item["document_id"] for item in top}
    cited = [int(d) for d in cited_raw
             if isinstance(d, (int, str)) and str(d).isdigit()
             and int(d) in valid_ids]
    return (status, rationale, cited)


# ─────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────

# Parallelism for coverage grading. LLM calls are I/O-bound, so threads
# are appropriate. Eight concurrent workers comfortably stays under
# Anthropic/OpenAI rate limits for our tier and cuts a 5,000-req sweep
# from hours to ~10–15 minutes. Configurable via env var.
def _coverage_workers() -> int:
    try:
        return max(1, min(32, int(os.environ.get("COVERAGE_PARALLEL", "8"))))
    except ValueError:
        return 8


def assess_coverage_for_document(
    db: Session,
    document_id: int,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    resume: bool = True,
) -> Dict[str, Any]:
    """Run the coverage rubric over every requirement extracted from the
    given RFP document. Persists status + rationale + cited doc ids on
    each RfpRequirement.

    Resume mode (default): only grades requirements that have NOT yet been
    assessed (parsons_coverage_status IS NULL or 'not_assessed'). Pass
    ``resume=False`` to force a full re-grade.

    Workers grade in parallel (LLM calls are I/O-bound). The main thread
    owns the SQLAlchemy session — workers only read from a snapshot and
    return result tuples that the main thread then writes back.

    Returns a summary dict the API surfaces back to the caller.
    """
    doc = (db.query(IngestedDocument)
           .filter(IngestedDocument.id == document_id)
           .first())
    if not doc:
        return {"error": "Document not found"}

    all_reqs = (db.query(RfpRequirement)
                .filter(RfpRequirement.document_id == document_id)
                .all())
    if not all_reqs:
        return {"error": "No requirements extracted yet for this document",
                "assessed": 0}

    if resume:
        reqs = [r for r in all_reqs
                if not r.parsons_coverage_status
                or r.parsons_coverage_status == "not_assessed"]
        already_assessed = len(all_reqs) - len(reqs)
    else:
        reqs = all_reqs
        already_assessed = 0
    if not reqs:
        return {"document_id": document_id, "assessed": 0,
                "already_assessed": already_assessed,
                "counts": {"covered": 0, "partial": 0, "gap": 0, "uncertain": 0},
                "skipped_reason": "all requirements already assessed"}

    pool = _load_parsons_pool(db, doc.proposal_id)
    pool_size = len(pool)
    pool_doc_count = len({p["document_id"] for p in pool})
    workers = _coverage_workers()
    logger.info(
        f"Parsons coverage: doc={document_id} reqs={len(reqs)} "
        f"pool={pool_size} chunks across {pool_doc_count} parsons docs "
        f"(workers={workers})"
    )

    counts = {"covered": 0, "partial": 0, "gap": 0, "uncertain": 0}
    now = datetime.utcnow()

    if workers <= 1 or len(reqs) <= 1:
        # Sequential fallback (preserved for env COVERAGE_PARALLEL=1 / debugging)
        for i, req in enumerate(reqs):
            status, rationale, cited = _grade_one(req, pool)
            req.parsons_coverage_status = status
            req.parsons_coverage_notes = rationale
            req.parsons_evidence_doc_ids = json.dumps(cited)
            req.parsons_coverage_assessed_at = now
            counts[status] = counts.get(status, 0) + 1
            if progress_cb:
                try:
                    progress_cb(i + 1, len(reqs))
                except Exception:  # noqa: BLE001
                    pass
            # Commit incrementally so a long run's progress survives a crash
            if (i + 1) % 50 == 0:
                db.commit()
        db.commit()
    else:
        # Parallel path: workers do a PURE LLM call on a plain-dict snapshot.
        # NO worker touches the SQLAlchemy session — that prevents the
        # "session is in 'prepared' state" error that surfaced when workers
        # tried to reload req attributes mid-commit on the main thread.
        from concurrent.futures import ThreadPoolExecutor, as_completed

        # Snapshot all requirements into plain dicts BEFORE the workers run.
        # Each snapshot has every field _grade_one needs.
        snapshots = [
            {
                "id": r.id,
                "title": r.title,
                "description": r.description,
                "source_text": r.source_text,
                "category": r.category,
                "section_id": r.section_id,
                "requirement_id": r.requirement_id,
            }
            for r in reqs
        ]
        req_by_id: Dict[int, RfpRequirement] = {r.id: r for r in reqs}

        def _grade_snapshot(snap: Dict[str, Any]) -> tuple:
            """Pure-Python grading on a plain-dict snapshot. Mirrors
            _grade_one but never touches a SQLAlchemy attribute."""
            try:
                if not pool:
                    return (snap["id"], "gap",
                            "No Parsons knowledge documents are available.",
                            [], None)
                query_text = " ".join(filter(None, [
                    snap.get("title"), snap.get("description"),
                    snap.get("source_text"),
                ]))[:4000]
                if not query_text.strip():
                    return (snap["id"], "uncertain",
                            "Requirement text is empty — cannot vector-search.",
                            [], None)
                try:
                    q_vec = embed_text(query_text)
                except Exception as e:  # noqa: BLE001
                    return (snap["id"], "uncertain",
                            f"Could not embed: {type(e).__name__}", [], None)

                scored: List[Dict[str, Any]] = []
                for item in pool:
                    sim = cosine_similarity(q_vec, item["embedding"])
                    scored.append({**item, "similarity": sim})
                scored.sort(key=lambda x: x["similarity"], reverse=True)
                top = scored[:TOP_K_CHUNKS]
                if not top or top[0]["similarity"] < MIN_SIMILARITY_FOR_LLM:
                    return (snap["id"], "gap",
                            f"No Parsons excerpt above relevance threshold "
                            f"(top sim "
                            f"{(top[0]['similarity'] if top else 0):.2f}).",
                            [], None)

                # Build prompt manually so we don't pass a SQLAlchemy req.
                lines = [
                    "REQUIREMENT",
                    f"  id: {snap.get('requirement_id') or snap['id']}",
                    f"  title: {(snap.get('title') or '').strip()[:240]}",
                    f"  category: {snap.get('category') or 'unspecified'}",
                ]
                if snap.get("description"):
                    lines.append(f"  description: {snap['description'].strip()[:1500]}")
                if snap.get("source_text"):
                    lines.append(f"  source text: {snap['source_text'].strip()[:1500]}")
                lines.append("")
                lines.append(f"PARSONS EVIDENCE EXCERPTS (top similar chunks, "
                             f"{len(top)} shown)")
                used = 0
                for i, h in enumerate(top, 1):
                    snippet = (h["content"] or "").strip()[:MAX_CHARS_PER_CHUNK]
                    block = (
                        f"--- Excerpt {i} (document_id={h['document_id']}, "
                        f"category={h.get('parsons_category') or 'uncategorized'}, "
                        f"scope={h.get('scope', 'global')}, "
                        f"sim={h['similarity']:.2f}) ---\n"
                        f"Source: {h['document_name']}"
                        + (f" p.{h['page']}" if h.get('page') else "") + "\n"
                        f"{snippet}\n"
                    )
                    if used + len(block) > MAX_TOTAL_PROMPT_CHARS:
                        lines.append(f"…and {len(top) - i + 1} more truncated.")
                        break
                    lines.append(block)
                    used += len(block)
                lines.append("")
                lines.append("Grade this requirement now. Output JSON only.")
                user_prompt = "\n".join(lines)

                raw = _call_ai(user_prompt, system=_RUBRIC_SYSTEM)
                verdict = _parse_rubric_json(raw)
                if not verdict:
                    return (snap["id"], "uncertain",
                            "LLM did not return parseable JSON.",
                            [t["document_id"] for t in top], None)
                status = (verdict.get("status") or "uncertain").lower()
                if status not in {"covered", "partial", "gap", "uncertain"}:
                    status = "uncertain"
                rationale = (verdict.get("rationale") or "").strip()[:1000] or None
                cited_raw = verdict.get("cited_doc_ids") or []
                valid_ids = {item["document_id"] for item in top}
                cited = [int(d) for d in cited_raw
                         if isinstance(d, (int, str)) and str(d).isdigit()
                         and int(d) in valid_ids]
                return (snap["id"], status, rationale, cited, None)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"coverage worker failed for req {snap['id']}: {e}")
                return (snap["id"], "uncertain",
                        f"Worker error: {type(e).__name__}", [], str(e))

        completed = 0
        commit_every = 50
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(_grade_snapshot, s) for s in snapshots]
            for fut in as_completed(futures):
                req_id, status, rationale, cited, err = fut.result()
                req = req_by_id.get(req_id)
                if not req:
                    continue
                req.parsons_coverage_status = status
                req.parsons_coverage_notes = rationale
                req.parsons_evidence_doc_ids = json.dumps(cited)
                req.parsons_coverage_assessed_at = now
                counts[status] = counts.get(status, 0) + 1
                completed += 1
                if progress_cb:
                    try:
                        progress_cb(completed, len(reqs))
                    except Exception:  # noqa: BLE001
                        pass
                if completed % commit_every == 0:
                    db.commit()
        db.commit()

    return {
        "document_id": document_id,
        "assessed": len(reqs),
        "counts": counts,
        "parsons_pool_chunks": pool_size,
        "parsons_pool_docs": pool_doc_count,
        "workers": workers,
        "assessed_at": now.isoformat(),
    }


def assess_coverage_for_proposal(
    db: Session,
    proposal_id: int,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    resume: bool = True,
) -> Dict[str, Any]:
    """Run coverage assessment across every RFP document in a proposal.

    Resume mode (default): skips requirements already assessed. Pass
    ``resume=False`` to force a full re-grade across all docs."""
    docs = (db.query(IngestedDocument)
            .filter(IngestedDocument.proposal_id == proposal_id,
                    IngestedDocument.source_type == "rfp")
            .all())
    if not docs:
        return {"error": "No RFP documents found for proposal", "assessed": 0}
    aggregate = {"covered": 0, "partial": 0, "gap": 0, "uncertain": 0}
    total_assessed = 0
    total_already = 0
    for d in docs:
        result = assess_coverage_for_document(
            db, d.id, progress_cb=progress_cb, resume=resume)
        if result.get("error"):
            logger.warning(f"Coverage assessment for doc {d.id}: {result['error']}")
            continue
        for k, v in (result.get("counts") or {}).items():
            aggregate[k] = aggregate.get(k, 0) + v
        total_assessed += result.get("assessed", 0)
        total_already += result.get("already_assessed", 0)
    return {
        "proposal_id": proposal_id,
        "assessed": total_assessed,
        "already_assessed": total_already,
        "counts": aggregate,
        "documents": len(docs),
        "resumed": resume,
    }

def reassess_open_gaps_for_proposal(
    db: Session,
    proposal_id: int,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> Dict[str, Any]:
    """Re-grade ONLY requirements with parsons_coverage_status in
    ('gap', 'partial', 'uncertain') for a proposal. Used after a new
    Parsons knowledge document is uploaded — covered requirements don't
    need re-evaluation because their evidence already passed the bar.

    Returns:
        {"reassessed": int, "promoted": int, "before": {...}, "after": {...}}
        where 'promoted' = number that moved from gap → covered or partial.
    """
    # Snapshot before-state for the wizard summary
    before_rows = (db.query(RfpRequirement.parsons_coverage_status,
                            sa_func.count(RfpRequirement.id))
                   .filter(RfpRequirement.proposal_id == proposal_id)
                   .group_by(RfpRequirement.parsons_coverage_status)
                   .all())
    before = {(s or "(null)"): int(n) for s, n in before_rows}

    # Find the open-gap requirements
    open_reqs = (db.query(RfpRequirement)
                 .filter(RfpRequirement.proposal_id == proposal_id)
                 .filter(RfpRequirement.parsons_coverage_status.in_(
                     ["gap", "partial", "uncertain"]))
                 .all())
    if not open_reqs:
        return {"reassessed": 0, "promoted": 0,
                "before": before, "after": before,
                "skipped_reason": "no open-gap requirements"}

    # Track each req's old status so we can compute promotions
    old_status = {r.id: r.parsons_coverage_status for r in open_reqs}

    # Reuse the same per-doc grader by setting force-re-grade.
    # Group by document and grade each open req fresh.
    pool = _load_parsons_pool(db, proposal_id)
    workers = _coverage_workers()
    logger.info(
        f"reassess_open_gaps: {len(open_reqs)} reqs, pool={len(pool)} chunks, "
        f"{workers} workers"
    )

    counts = {"covered": 0, "partial": 0, "gap": 0, "uncertain": 0}
    now = datetime.utcnow()
    reassessed = 0
    promoted = 0

    if workers <= 1:
        for i, req in enumerate(open_reqs):
            status, rationale, cited = _grade_one(req, pool)
            req.parsons_coverage_status = status
            req.parsons_coverage_notes = rationale
            req.parsons_evidence_doc_ids = json.dumps(cited)
            req.parsons_coverage_assessed_at = now
            counts[status] = counts.get(status, 0) + 1
            reassessed += 1
            if status in ("covered", "partial") and old_status.get(req.id) == "gap":
                promoted += 1
            if progress_cb:
                try:
                    progress_cb(i + 1, len(open_reqs))
                except Exception:
                    pass
            if (i + 1) % 50 == 0:
                db.commit()
        db.commit()
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        # Snapshot req contents (workers must NOT touch the ORM session)
        snapshots = []
        for req in open_reqs:
            snapshots.append({
                "id": req.id,
                "title": req.title or "",
                "description": req.description or "",
                "source_text": req.source_text or "",
                "section_id": req.section_id or "",
                "category": req.category or "",
                "priority": req.priority or "",
            })

        # Build a fake-ORM-like object the existing _grade_one can use
        class _ReqLike:
            __slots__ = ("id", "title", "description", "source_text",
                         "section_id", "category", "priority")
            def __init__(self, d):
                for k, v in d.items():
                    setattr(self, k, v)

        def _worker(snap):
            try:
                req_like = _ReqLike(snap)
                status, rationale, cited = _grade_one(req_like, pool)
                return (snap["id"], status, rationale, cited, None)
            except Exception as e:
                return (snap["id"], None, None, [], str(e)[:200])

        ex = ThreadPoolExecutor(max_workers=workers)
        futures = [ex.submit(_worker, s) for s in snapshots]
        commit_every = 50
        committed_since = 0
        for fut in as_completed(futures):
            req_id, status, rationale, cited, err = fut.result()
            if err or not status:
                continue
            req = next((r for r in open_reqs if r.id == req_id), None)
            if not req:
                continue
            req.parsons_coverage_status = status
            req.parsons_coverage_notes = rationale
            req.parsons_evidence_doc_ids = json.dumps(cited)
            req.parsons_coverage_assessed_at = now
            counts[status] = counts.get(status, 0) + 1
            reassessed += 1
            if status in ("covered", "partial") and old_status.get(req_id) == "gap":
                promoted += 1
            committed_since += 1
            if committed_since >= commit_every:
                db.commit()
                committed_since = 0
            if progress_cb:
                try:
                    progress_cb(reassessed, len(open_reqs))
                except Exception:
                    pass
        db.commit()

    after_rows = (db.query(RfpRequirement.parsons_coverage_status,
                            sa_func.count(RfpRequirement.id))
                   .filter(RfpRequirement.proposal_id == proposal_id)
                   .group_by(RfpRequirement.parsons_coverage_status)
                   .all())
    after = {(s or "(null)"): int(n) for s, n in after_rows}

    return {"reassessed": reassessed, "promoted": promoted,
            "before": before, "after": after,
            "counts": counts}