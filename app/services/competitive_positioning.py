"""Competitive positioning per requirement.

For each RFP requirement, decide where Parsons stands competitively given:
  * Parsons evidence retrieval (the same vector search used by the drafter)
  * Competitor strengths/weaknesses dossier entries for the proposal's
    tracked competitors

Output positions:
  strong  — Parsons is well-positioned AND/OR competitors are weak here
  parity  — both can answer this similarly
  weak    — Parsons gap AND/OR competitors known to be strong here
  neutral — informational / no competitive angle (forms, signatures, etc.)

Idempotent: skips requirements that already have a competitive_position
unless ``rerun=True`` is passed.

Tactical guardrails (accuracy):
  * Validate the LLM's named-competitor lists against the actual tracked
    set — drop any IDs the LLM hallucinated.
  * Hard fallback to "neutral" for forms/signatures/procedural reqs even
    if the LLM tries to assign a competitive angle.
  * If no competitor dossiers exist, position falls back to a Parsons-only
    assessment (strong if covered, weak if gap).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..models import (
    Competitor, CompetitorDossier, ProposalCompetitor, RfpRequirement,
)
from .embedding_service import cosine_similarity, embed_text
from .orchestrator_agent import _call_ai
from .parsons_coverage import _load_parsons_pool

logger = logging.getLogger(__name__)


ALLOWED_POSITIONS = {"strong", "parity", "weak", "neutral"}

# These categories typically have no competitive angle.
_PROCEDURAL_HINTS = (
    "form", "signature", "ownership disclosure", "macbride",
    "offer and acceptance", "business registration", "set-aside",
)


# ─────────────────────────────────────────────────────────────────────
# Competitor context loader
# ─────────────────────────────────────────────────────────────────────

def _load_proposal_competitors(
    db: Session, proposal_id: int,
) -> List[Tuple[Competitor, Dict[str, str]]]:
    """Return (competitor, {category: dossier_content}) tuples for every
    competitor the capture team is tracking on this proposal. Includes
    ruled_out=False entries only."""
    pcs = (db.query(ProposalCompetitor)
           .filter(ProposalCompetitor.proposal_id == proposal_id)
           .filter(ProposalCompetitor.relevance != "ruled_out")
           .all())
    if not pcs:
        return []
    out: List[Tuple[Competitor, Dict[str, str]]] = []
    for pc in pcs:
        comp = db.query(Competitor).filter(Competitor.id == pc.competitor_id).first()
        if not comp:
            continue
        # Pull strengths + weaknesses (the two categories most useful for
        # per-requirement positioning). Other categories are noise here.
        rows = (db.query(CompetitorDossier)
                .filter(CompetitorDossier.competitor_id == comp.id)
                .filter(CompetitorDossier.category.in_(["strengths", "weaknesses"]))
                .all())
        cat_map = {r.category: (r.content or "")[:8000] for r in rows}
        out.append((comp, cat_map))
    return out


def _is_procedural(req: RfpRequirement) -> bool:
    text = " ".join(filter(None, [
        (req.title or "").lower(),
        (req.section_id or "").lower(),
        (req.category or "").lower(),
    ]))
    if (req.category or "").lower() in ("form", "certification", "signature"):
        return True
    return any(h in text for h in _PROCEDURAL_HINTS)


# ─────────────────────────────────────────────────────────────────────
# LLM positioning prompt
# ─────────────────────────────────────────────────────────────────────

_POSITIONING_SYSTEM = (
    "You are a Parsons capture-team strategist. For ONE specific RFP "
    "requirement, you assess where Parsons stands competitively given "
    "the Parsons evidence excerpts and the competitors' published "
    "strengths and weaknesses.\n\n"
    "Output VALID JSON ONLY. NO preamble. NO code fences."
)


def _build_positioning_prompt(
    req: RfpRequirement,
    parsons_snippets: List[Dict[str, Any]],
    competitor_blocks: List[Dict[str, Any]],
) -> str:
    bits: List[str] = []
    bits.append("REQUIREMENT")
    bits.append(f"  id: {req.requirement_id or req.id}")
    bits.append(f"  section: {req.section_id or '(none)'}")
    bits.append(f"  title: {(req.title or '').strip()[:240]}")
    bits.append(f"  category: {req.category or '(none)'}")
    if req.description:
        bits.append(f"  description: {req.description.strip()[:1200]}")
    if req.source_text:
        bits.append(f"  source text: {req.source_text.strip()[:1200]}")
    bits.append("")

    if parsons_snippets:
        bits.append(f"PARSONS EVIDENCE EXCERPTS ({len(parsons_snippets)})")
        for i, h in enumerate(parsons_snippets, 1):
            snippet = (h["content"] or "").strip()[:600]
            bits.append(
                f"-- Excerpt {i} (sim={h['similarity']:.2f}, "
                f"category={h.get('parsons_category') or '?'}) --\n"
                f"Source: {h['document_name']}\n"
                f"{snippet}\n"
            )
    else:
        bits.append("PARSONS EVIDENCE EXCERPTS")
        bits.append("(none — no Parsons knowledge matches this requirement)")
    bits.append("")

    if competitor_blocks:
        bits.append(f"COMPETITOR DOSSIERS ({len(competitor_blocks)} tracked)")
        for cb in competitor_blocks:
            bits.append(f"\n--- Competitor id={cb['id']}: {cb['name']} ---")
            if cb.get("strengths"):
                bits.append(f"STRENGTHS:\n{cb['strengths'][:2500]}")
            if cb.get("weaknesses"):
                bits.append(f"WEAKNESSES:\n{cb['weaknesses'][:2500]}")
    else:
        bits.append("COMPETITOR DOSSIERS")
        bits.append("(none — no competitors tracked on this proposal)")
    bits.append("")

    bits.append(
        "Decide ONE position for this requirement, weighing ALL the "
        "evidence above. Identify which competitors give us advantage "
        "(they are weak here OR we have stronger evidence) and which "
        "pose risk (they are documented strong here AND/OR we have a "
        "gap)."
    )
    bits.append("")
    bits.append(
        "Output a SINGLE JSON object EXACTLY this shape:\n"
        "{\n"
        '  "position": "strong" | "parity" | "weak" | "neutral",\n'
        '  "rationale": "<2-3 sentence explanation that names specific evidence>",\n'
        '  "advantage_competitor_ids": [<int competitor ids where we look strong vs them>],\n'
        '  "risk_competitor_ids": [<int competitor ids where they look strong vs us>],\n'
        '  "win_theme": "<short bid-message-friendly win theme, or empty>"\n'
        "}\n"
        "Use 'neutral' for procedural / form / signature requirements. "
        "Use 'strong' only when you can name specific Parsons evidence "
        "AND/OR a documented competitor weakness. Be HONEST about gaps — "
        "false 'strong' kills bids."
    )
    bits.append("JSON ONLY.")
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
        v = json.loads(cand)
        return v if isinstance(v, dict) else None
    except json.JSONDecodeError:
        return None


def _validate(env: Dict[str, Any], valid_competitor_ids: set) -> Optional[Dict[str, Any]]:
    if not isinstance(env, dict):
        return None
    pos = (env.get("position") or "").lower().strip()
    if pos not in ALLOWED_POSITIONS:
        return None

    def _filter_ids(raw):
        out = []
        for x in (raw or []):
            try:
                xi = int(x)
                if xi in valid_competitor_ids:
                    out.append(xi)
            except (TypeError, ValueError):
                continue
        return out

    return {
        "position": pos,
        "rationale": (env.get("rationale") or "").strip()[:1500] or None,
        "advantage_competitor_ids": _filter_ids(env.get("advantage_competitor_ids")),
        "risk_competitor_ids": _filter_ids(env.get("risk_competitor_ids")),
        "win_theme": (env.get("win_theme") or "").strip()[:500] or None,
    }


# ─────────────────────────────────────────────────────────────────────
# Public entry — assess one requirement
# ─────────────────────────────────────────────────────────────────────

def assess_requirement(
    db: Session,
    requirement_id: int,
    *,
    competitor_blocks: Optional[List[Dict[str, Any]]] = None,
    parsons_pool: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Assess competitive position for ONE requirement. Returns the
    persisted dict shape. Caller can hand in pre-loaded competitor blocks
    + parsons pool to amortize across many requirements."""
    req = db.query(RfpRequirement).filter(
        RfpRequirement.id == requirement_id).first()
    if not req:
        return {"error": "Requirement not found"}

    # Procedural shortcut — these never have a competitive angle
    if _is_procedural(req):
        req.competitive_position = "neutral"
        req.competitive_rationale = "Procedural / form requirement — no competitive angle."
        req.competitive_advantage_competitors = None
        req.competitive_risk_competitors = None
        req.competitive_assessed_at = datetime.utcnow()
        db.commit()
        return {
            "requirement_id": req.id,
            "position": "neutral",
            "rationale": req.competitive_rationale,
            "shortcut": "procedural",
        }

    if competitor_blocks is None:
        comps = _load_proposal_competitors(db, req.proposal_id)
        competitor_blocks = [
            {"id": c.id, "name": c.name,
             "strengths": cats.get("strengths", ""),
             "weaknesses": cats.get("weaknesses", "")}
            for c, cats in comps
        ]
    valid_ids = {b["id"] for b in competitor_blocks}

    if parsons_pool is None:
        parsons_pool = _load_parsons_pool(db, req.proposal_id)

    # Vector-rank Parsons evidence
    parsons_snippets: List[Dict[str, Any]] = []
    query = " ".join(filter(None, [req.title, req.description, req.source_text]))[:4000]
    if query.strip() and parsons_pool:
        try:
            q_vec = embed_text(query)
            scored = []
            for item in parsons_pool:
                sim = cosine_similarity(q_vec, item["embedding"])
                if sim > 0.20:
                    scored.append({**item, "similarity": sim})
            scored.sort(key=lambda x: x["similarity"], reverse=True)
            parsons_snippets = scored[:5]
        except Exception as e:  # noqa: BLE001
            logger.warning(f"embed failed for req {req.id}: {e}")

    # No competitors tracked → fall back to Parsons-only
    if not competitor_blocks:
        # Heuristic: covered → strong, gap → weak, partial/uncertain → parity
        cov = req.parsons_coverage_status or "not_assessed"
        fallback_pos = (
            "strong" if cov == "covered"
            else "weak" if cov == "gap"
            else "parity" if cov in ("partial", "uncertain")
            else "parity"
        )
        req.competitive_position = fallback_pos
        req.competitive_rationale = (
            f"No competitors tracked on this proposal. Position derived from "
            f"Parsons-coverage status: {cov}.")
        req.competitive_advantage_competitors = None
        req.competitive_risk_competitors = None
        req.competitive_assessed_at = datetime.utcnow()
        db.commit()
        return {
            "requirement_id": req.id,
            "position": fallback_pos,
            "rationale": req.competitive_rationale,
            "shortcut": "no_competitors",
        }

    # LLM positioning
    prompt = _build_positioning_prompt(req, parsons_snippets, competitor_blocks)
    raw = _call_ai(prompt, _POSITIONING_SYSTEM, max_tokens=900)
    env = _parse_envelope(raw)
    decision = _validate(env or {}, valid_ids)
    if not decision:
        return {"error": "LLM returned malformed positioning envelope",
                "raw_preview": (raw or "")[:240]}

    # Persist
    req.competitive_position = decision["position"]
    req.competitive_rationale = decision["rationale"]
    req.competitive_advantage_competitors = (
        json.dumps(decision["advantage_competitor_ids"])
        if decision["advantage_competitor_ids"] else None
    )
    req.competitive_risk_competitors = (
        json.dumps(decision["risk_competitor_ids"])
        if decision["risk_competitor_ids"] else None
    )
    req.competitive_assessed_at = datetime.utcnow()
    db.commit()
    return {
        "requirement_id": req.id,
        **decision,
    }


# ─────────────────────────────────────────────────────────────────────
# Bulk
# ─────────────────────────────────────────────────────────────────────

def _positioning_workers() -> int:
    import os
    try:
        return max(1, min(32, int(os.environ.get("POSITIONING_PARALLEL", "8"))))
    except ValueError:
        return 8


def assess_all_requirements(
    db: Session,
    proposal_id: int,
    *,
    rerun: bool = False,
    max_requirements: Optional[int] = None,
    progress_cb: Optional[callable] = None,
) -> Dict[str, Any]:
    """Run competitive positioning across all (or unassessed) requirements
    on a proposal. Loads competitor + Parsons context once and reuses across
    the parallel workers. LLM calls run in a ThreadPoolExecutor (I/O bound);
    the main thread owns the SQLAlchemy session."""
    comps = _load_proposal_competitors(db, proposal_id)
    competitor_blocks = [
        {"id": c.id, "name": c.name,
         "strengths": cats.get("strengths", ""),
         "weaknesses": cats.get("weaknesses", "")}
        for c, cats in comps
    ]
    valid_competitor_ids = {b["id"] for b in competitor_blocks}
    parsons_pool = _load_parsons_pool(db, proposal_id)

    q = (db.query(RfpRequirement)
         .filter(RfpRequirement.proposal_id == proposal_id))
    if not rerun:
        q = q.filter(RfpRequirement.competitive_position.is_(None))
    reqs = q.all()
    if max_requirements:
        reqs = reqs[:max_requirements]

    counts = {"strong": 0, "parity": 0, "weak": 0, "neutral": 0}
    errors = 0
    advantage_competitor_hits: Dict[int, int] = {}
    risk_competitor_hits: Dict[int, int] = {}
    workers = _positioning_workers()
    logger.info(
        f"competitive positioning: proposal={proposal_id} reqs={len(reqs)} "
        f"competitors={len(competitor_blocks)} workers={workers}"
    )

    if not reqs:
        return {
            "proposal_id": proposal_id, "total_assessed": 0,
            "counts": counts, "errors": 0,
            "competitors_tracked": len(competitor_blocks),
            "advantage_hits_by_competitor": {},
            "risk_hits_by_competitor": {},
        }

    # Snapshot every requirement to plain dicts BEFORE workers start, so
    # workers never touch a SQLAlchemy session. Without this, a main-thread
    # commit() mid-run puts the session in 'prepared' state and any
    # concurrent attribute load (req.title, etc.) crashes with
    # "session is in 'prepared' state". This was the root cause of the
    # earlier coverage-job stall.
    snapshots: List[Dict[str, Any]] = [
        {
            "id": r.id,
            "requirement_id": r.requirement_id,
            "title": r.title,
            "description": r.description,
            "source_text": r.source_text,
            "section_id": r.section_id,
            "category": r.category,
            "parsons_coverage_status": r.parsons_coverage_status,
        }
        for r in reqs
    ]

    def _is_procedural_snap(snap: Dict[str, Any]) -> bool:
        text = " ".join(filter(None, [
            (snap.get("title") or "").lower(),
            (snap.get("section_id") or "").lower(),
            (snap.get("category") or "").lower(),
        ]))
        if (snap.get("category") or "").lower() in ("form", "certification", "signature"):
            return True
        return any(h in text for h in _PROCEDURAL_HINTS)

    def _build_prompt_from_snap(
        snap: Dict[str, Any],
        snippets: List[Dict[str, Any]],
        comp_blocks: List[Dict[str, Any]],
    ) -> str:
        bits: List[str] = []
        bits.append("REQUIREMENT")
        bits.append(f"  id: {snap.get('requirement_id') or snap['id']}")
        bits.append(f"  section: {snap.get('section_id') or '(none)'}")
        bits.append(f"  title: {(snap.get('title') or '').strip()[:240]}")
        bits.append(f"  category: {snap.get('category') or '(none)'}")
        if snap.get("description"):
            bits.append(f"  description: {snap['description'].strip()[:1200]}")
        if snap.get("source_text"):
            bits.append(f"  source text: {snap['source_text'].strip()[:1200]}")
        bits.append("")
        if snippets:
            bits.append(f"PARSONS EVIDENCE EXCERPTS ({len(snippets)})")
            for i, h in enumerate(snippets, 1):
                txt = (h["content"] or "").strip()[:600]
                bits.append(
                    f"-- Excerpt {i} (sim={h['similarity']:.2f}, "
                    f"category={h.get('parsons_category') or '?'}) --\n"
                    f"Source: {h['document_name']}\n{txt}\n")
        else:
            bits.append("PARSONS EVIDENCE EXCERPTS\n(none)")
        bits.append("")
        if comp_blocks:
            bits.append(f"COMPETITOR DOSSIERS ({len(comp_blocks)} tracked)")
            for cb in comp_blocks:
                bits.append(f"\n--- Competitor id={cb['id']}: {cb['name']} ---")
                if cb.get("strengths"):
                    bits.append(f"STRENGTHS:\n{cb['strengths'][:2500]}")
                if cb.get("weaknesses"):
                    bits.append(f"WEAKNESSES:\n{cb['weaknesses'][:2500]}")
        bits.append("")
        bits.append(
            "Decide ONE position. Output JSON exactly:\n"
            "{\n"
            '  "position": "strong" | "parity" | "weak" | "neutral",\n'
            '  "rationale": "<2-3 sentences>",\n'
            '  "advantage_competitor_ids": [<int ids>],\n'
            '  "risk_competitor_ids": [<int ids>],\n'
            '  "win_theme": "<short or empty>"\n'
            "}\nJSON ONLY.")
        return "\n".join(bits)

    # ── Worker: pure dict in, pure dict out. NO ORM access.
    def _worker(snap: Dict[str, Any]) -> Dict[str, Any]:
        try:
            if _is_procedural_snap(snap):
                return {
                    "req_id": snap["id"], "position": "neutral",
                    "rationale": "Procedural / form requirement — no competitive angle.",
                    "advantage_ids": [], "risk_ids": [],
                    "error": None, "shortcut": "procedural",
                }
            parsons_snippets: List[Dict[str, Any]] = []
            query = " ".join(filter(None, [
                snap.get("title"), snap.get("description"), snap.get("source_text"),
            ]))[:4000]
            if query.strip() and parsons_pool:
                try:
                    q_vec = embed_text(query)
                    scored = []
                    for item in parsons_pool:
                        sim = cosine_similarity(q_vec, item["embedding"])
                        if sim > 0.20:
                            scored.append({**item, "similarity": sim})
                    scored.sort(key=lambda x: x["similarity"], reverse=True)
                    parsons_snippets = scored[:5]
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"embed failed for req {snap['id']}: {e}")
            if not competitor_blocks:
                cov = snap.get("parsons_coverage_status") or "not_assessed"
                fb = ("strong" if cov == "covered"
                      else "weak" if cov == "gap"
                      else "parity")
                return {
                    "req_id": snap["id"], "position": fb,
                    "rationale": f"No competitors tracked; using Parsons-coverage status: {cov}.",
                    "advantage_ids": [], "risk_ids": [],
                    "error": None, "shortcut": "no_competitors",
                }
            prompt = _build_prompt_from_snap(snap, parsons_snippets, competitor_blocks)
            raw = _call_ai(prompt, _POSITIONING_SYSTEM, max_tokens=900)
            env = _parse_envelope(raw)
            decision = _validate(env or {}, valid_competitor_ids)
            if not decision:
                return {"req_id": snap["id"], "error": "malformed envelope"}
            return {
                "req_id": snap["id"],
                "position": decision["position"],
                "rationale": decision["rationale"],
                "advantage_ids": decision["advantage_competitor_ids"],
                "risk_ids": decision["risk_competitor_ids"],
                "error": None,
            }
        except Exception as e:  # noqa: BLE001
            logger.warning(f"positioning worker failed for req {snap['id']}: {e}")
            return {"req_id": snap["id"], "error": str(e)}

    req_by_id = {r.id: r for r in reqs}
    completed = 0
    commit_every = 50
    now = datetime.utcnow()

    if workers <= 1:
        # Sequential fallback
        results_iter = (_worker(s) for s in snapshots)
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        ex = ThreadPoolExecutor(max_workers=workers)
        futures = [ex.submit(_worker, s) for s in snapshots]
        results_iter = (fut.result() for fut in as_completed(futures))

    for res in results_iter:
        if res.get("error"):
            errors += 1
            completed += 1
            if progress_cb and completed % 5 == 0:
                try:
                    progress_cb(completed, len(reqs))
                except Exception:
                    pass
            continue
        req = req_by_id.get(res["req_id"])
        if not req:
            continue
        req.competitive_position = res["position"]
        req.competitive_rationale = res["rationale"]
        req.competitive_advantage_competitors = (
            json.dumps(res["advantage_ids"]) if res["advantage_ids"] else None)
        req.competitive_risk_competitors = (
            json.dumps(res["risk_ids"]) if res["risk_ids"] else None)
        req.competitive_assessed_at = now

        pos = res["position"]
        if pos in counts:
            counts[pos] += 1
        for cid in res["advantage_ids"]:
            advantage_competitor_hits[cid] = advantage_competitor_hits.get(cid, 0) + 1
        for cid in res["risk_ids"]:
            risk_competitor_hits[cid] = risk_competitor_hits.get(cid, 0) + 1

        completed += 1
        if completed % commit_every == 0:
            db.commit()
        if progress_cb and completed % 5 == 0:
            try:
                progress_cb(completed, len(reqs))
            except Exception:
                pass

    db.commit()

    return {
        "proposal_id": proposal_id,
        "total_assessed": sum(counts.values()),
        "counts": counts,
        "errors": errors,
        "competitors_tracked": len(competitor_blocks),
        "workers": workers,
        "advantage_hits_by_competitor": advantage_competitor_hits,
        "risk_hits_by_competitor": risk_competitor_hits,
    }


def summarize_competitive_position(
    db: Session,
    proposal_id: int,
) -> Dict[str, Any]:
    """Roll up positioning across requirements + per submission section
    so the readiness dashboard can render 'we win here / we lose there'
    summaries."""
    rows = (db.query(RfpRequirement)
            .filter(RfpRequirement.proposal_id == proposal_id).all())
    if not rows:
        return {"proposal_id": proposal_id, "total": 0,
                "counts": {}, "by_section": [], "competitors": []}

    counts = {"strong": 0, "parity": 0, "weak": 0, "neutral": 0,
              "not_assessed": 0}
    by_section: Dict[str, Dict[str, Any]] = {}
    advantage_hits: Dict[int, int] = {}
    risk_hits: Dict[int, int] = {}

    for r in rows:
        pos = r.competitive_position or "not_assessed"
        counts[pos] = counts.get(pos, 0) + 1

        sec = r.submission_section_slug or "(unmapped)"
        b = by_section.setdefault(sec, {
            "section_slug": sec,
            "total": 0,
            "strong": 0, "parity": 0, "weak": 0, "neutral": 0,
            "not_assessed": 0,
        })
        b["total"] += 1
        b[pos] = b.get(pos, 0) + 1

        try:
            adv = json.loads(r.competitive_advantage_competitors) if r.competitive_advantage_competitors else []
        except (json.JSONDecodeError, TypeError):
            adv = []
        try:
            risk = json.loads(r.competitive_risk_competitors) if r.competitive_risk_competitors else []
        except (json.JSONDecodeError, TypeError):
            risk = []
        for cid in adv:
            advantage_hits[cid] = advantage_hits.get(cid, 0) + 1
        for cid in risk:
            risk_hits[cid] = risk_hits.get(cid, 0) + 1

    # Resolve competitor names
    comp_ids = list(set(advantage_hits.keys()) | set(risk_hits.keys()))
    comp_names: Dict[int, str] = {}
    if comp_ids:
        for c in db.query(Competitor).filter(Competitor.id.in_(comp_ids)).all():
            comp_names[c.id] = c.name

    competitors_summary = []
    for cid in sorted(set(advantage_hits.keys()) | set(risk_hits.keys()),
                       key=lambda x: (-(advantage_hits.get(x, 0) + risk_hits.get(x, 0)),
                                       comp_names.get(x, str(x)))):
        competitors_summary.append({
            "competitor_id": cid,
            "name": comp_names.get(cid, f"competitor #{cid}"),
            "advantage_hits": advantage_hits.get(cid, 0),
            "risk_hits": risk_hits.get(cid, 0),
        })

    return {
        "proposal_id": proposal_id,
        "total": len(rows),
        "counts": counts,
        "by_section": list(by_section.values()),
        "competitors": competitors_summary,
    }
