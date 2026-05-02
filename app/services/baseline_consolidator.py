"""Baseline consolidator — apply each amendment to the master RFP so the
downstream diff is apples-to-apples.

The problem
-----------
A naive diff with ``baseline_document_ids = [master + every amendment]``
produces misleading findings. Master language replaced by amendment X looks
like a "removed" requirement to the diff, and the amendment language looks
"added" — when in reality the amendment simply modified the master.

The fix
-------
Build a synthetic **as-amended baseline**:

  1. Snapshot every requirement extracted from the master RFP into
     ``consolidated_requirements`` (provenance: original master req).
  2. For each amendment doc, in chronological order:
       a. For every amendment requirement, vector-rank the top-K most
          similar live consolidated requirements.
       b. Ask an LLM to decide:
            ``modify`` — this amendment replaces / updates one specific
                         consolidated requirement (it returns which one
                         and the new text).
            ``add``    — this amendment introduces brand-new scope.
            ``remove`` — this amendment removes a master requirement.
            ``skip``   — Q&A clarification, no requirement change.
       c. Apply the decision to the consolidated state, recording
          ``amendment_history`` audit entries on every touched row.
  3. The final ``consolidated_requirements`` table for that run IS the
     as-amended baseline — pass its requirement IDs into the existing
     ``run_requirement_diff`` to compare against the new RFP.

Accuracy guardrails
-------------------
  * Strict JSON envelope validation; malformed envelopes default to
    ``skip`` (the safest no-op).
  * The LLM can only point to one of the K candidates we showed it —
    if it names an ID outside the set, the action becomes ``add``.
  * Pure-dict snapshots so workers never touch the SQLAlchemy session
   — same pattern used in coverage / positioning to avoid the
     "session in 'prepared' state" race.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..models import (
    ConsolidatedBaselineRun, ConsolidatedRequirement, IngestedDocument,
    RfpRequirement,
)
from .embedding_service import cosine_similarity, embed_text
from .orchestrator_agent import _call_ai

logger = logging.getLogger(__name__)


# Top-K most-similar consolidated reqs to show the LLM per amendment req.
# 6 is enough to give the LLM real choice without bloating the prompt.
TOP_K = 6
# Below this similarity, the LLM almost certainly says "add" — skip it
# to a deterministic add and save tokens.
MIN_SIM_FOR_LLM = 0.40
ALLOWED_ACTIONS = {"modify", "add", "remove", "skip"}


def _consolidator_workers() -> int:
    try:
        return max(1, min(32, int(os.environ.get("CONSOLIDATE_PARALLEL", "8"))))
    except ValueError:
        return 8


# ─────────────────────────────────────────────────────────────────────
# JSON parser (tolerant of fence + preamble noise)
# ─────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────
# LLM amendment-action prompt
# ─────────────────────────────────────────────────────────────────────

_SYSTEM = (
    "You are a senior RFP capture analyst applying a procurement amendment "
    "to the master solicitation's requirement list. For ONE specific "
    "amendment item you decide whether it MODIFIES one of the existing "
    "(consolidated) requirements, ADDS a brand-new requirement, REMOVES "
    "an existing requirement, or is a Q&A SKIP that doesn't change scope.\n\n"
    "Rules:\n"
    "  * If you say 'modify', identify EXACTLY one consolidated_id from the "
    "    candidate list and provide the post-modification text.\n"
    "  * If you say 'remove', identify the consolidated_id being removed.\n"
    "  * If you say 'add', the amendment introduces brand-new scope not in "
    "    any candidate.\n"
    "  * If you say 'skip', the amendment is a clarification, definition, "
    "    or Q&A item that doesn't change a requirement.\n"
    "Be conservative — when uncertain whether something is a modify vs add, "
    "prefer modify if any candidate is plausibly the same obligation, else "
    "add. False adds inflate the requirement count; false modifies overwrite "
    "real master language.\n\n"
    "Output VALID JSON ONLY. NO preamble. NO code fences."
)


def _build_action_prompt(
    amendment_label: str,
    amendment_req: Dict[str, Any],
    candidates: List[Dict[str, Any]],
) -> str:
    bits: List[str] = []
    bits.append(f"AMENDMENT DOCUMENT: {amendment_label}")
    bits.append("")
    bits.append("AMENDMENT REQUIREMENT")
    bits.append(f"  amendment_req_id: {amendment_req['id']}")
    if amendment_req.get("section_id"):
        bits.append(f"  section_id: {amendment_req['section_id']}")
    if amendment_req.get("title"):
        bits.append(f"  title: {amendment_req['title'][:240]}")
    if amendment_req.get("category"):
        bits.append(f"  category: {amendment_req['category']}")
    if amendment_req.get("description"):
        bits.append(f"  description: {amendment_req['description'][:1200]}")
    if amendment_req.get("source_text"):
        bits.append(f"  source text: {amendment_req['source_text'][:1500]}")
    bits.append("")

    if candidates:
        bits.append(f"TOP-{len(candidates)} MOST-SIMILAR CONSOLIDATED REQUIREMENTS")
        for i, c in enumerate(candidates, 1):
            bits.append(
                f"  -- Candidate {i} (consolidated_id={c['id']}, "
                f"sim={c['similarity']:.2f}) --"
            )
            bits.append(f"     section: {c.get('section_id') or '(none)'}")
            bits.append(f"     title: {(c.get('title') or '')[:200]}")
            if c.get("description"):
                bits.append(f"     description: {c['description'][:600]}")
            if c.get("source_text"):
                bits.append(f"     source_text: {c['source_text'][:600]}")
    else:
        bits.append("CANDIDATES: (none)")
    bits.append("")
    bits.append(
        "Decide the action and output a SINGLE JSON object EXACTLY this shape:\n"
        "{\n"
        '  "action": "modify" | "add" | "remove" | "skip",\n'
        '  "consolidated_id": <int candidate id, or null>,\n'
        '  "rationale": "<one sentence>",\n'
        '  "new_section_id": "<section id of the amendment requirement, or empty>",\n'
        '  "new_title": "<post-modification title, or empty>",\n'
        '  "new_description": "<post-modification description, or empty>",\n'
        '  "new_source_text": "<post-modification source text, or empty>"\n'
        "}\n"
        "If action=modify, populate consolidated_id AND new_* fields with the "
        "FULL post-modification text (not just the delta). If action=add, "
        "leave consolidated_id null but populate the new_* fields. If "
        "action=remove, populate consolidated_id and leave new_* empty. If "
        "action=skip, all id/text fields can be empty.\n"
        "JSON ONLY."
    )
    return "\n".join(bits)


def _validate_action(
    env: Dict[str, Any],
    valid_candidate_ids: set,
    amendment_req: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Return a normalized decision dict, or None if the envelope is
    malformed in a way that can't be recovered."""
    if not isinstance(env, dict):
        return None
    action = (env.get("action") or "").lower().strip()
    if action not in ALLOWED_ACTIONS:
        return None

    cid = env.get("consolidated_id")
    cid_int: Optional[int] = None
    if cid is not None:
        try:
            cid_int = int(cid)
        except (TypeError, ValueError):
            cid_int = None
    if cid_int is not None and cid_int not in valid_candidate_ids:
        # The model invented an id — that's an "add" disguised as "modify".
        if action in ("modify", "remove"):
            action = "add"
            cid_int = None

    if action == "modify" and cid_int is None:
        # Modify without a target → degrade to add
        action = "add"
    if action == "remove" and cid_int is None:
        # Remove without a target → safest no-op
        action = "skip"

    decision = {
        "action": action,
        "consolidated_id": cid_int,
        "rationale": (env.get("rationale") or "").strip()[:1000] or None,
        "new_section_id": (env.get("new_section_id") or "").strip()[:240] or None,
        "new_title": (env.get("new_title") or "").strip()[:500] or None,
        "new_description": (env.get("new_description") or "").strip()[:8000] or None,
        "new_source_text": (env.get("new_source_text") or "").strip()[:8000] or None,
    }

    if action in ("modify", "add"):
        # If the LLM didn't supply replacement text, fall back to the
        # amendment's own text — it's the next best thing.
        if not decision["new_title"] and amendment_req.get("title"):
            decision["new_title"] = amendment_req["title"][:500]
        if not decision["new_description"] and amendment_req.get("description"):
            decision["new_description"] = amendment_req["description"][:8000]
        if not decision["new_source_text"] and amendment_req.get("source_text"):
            decision["new_source_text"] = amendment_req["source_text"][:8000]
        if not decision["new_section_id"] and amendment_req.get("section_id"):
            decision["new_section_id"] = amendment_req["section_id"][:240]

    return decision


# ─────────────────────────────────────────────────────────────────────
# Apply a single decision to the consolidated state
# ─────────────────────────────────────────────────────────────────────

def _append_history(
    row: ConsolidatedRequirement,
    entry: Dict[str, Any],
) -> None:
    try:
        hist = json.loads(row.amendment_history) if row.amendment_history else []
    except (json.JSONDecodeError, TypeError):
        hist = []
    hist.append(entry)
    row.amendment_history = json.dumps(hist)


# ─────────────────────────────────────────────────────────────────────
# Public entry — execute the consolidator run
# ─────────────────────────────────────────────────────────────────────

def start_consolidation_run(
    db: Session,
    *,
    master_document_id: int,
    amendment_document_ids: List[int],
    label: Optional[str] = None,
    created_by_user_id: Optional[int] = None,
) -> ConsolidatedBaselineRun:
    """Create the run row in 'pending' state. The orchestrator
    (background runner) is responsible for flipping to 'running' and
    eventually 'complete'/'failed'."""
    run = ConsolidatedBaselineRun(
        label=(label or
               f"Consolidate master={master_document_id} "
               f"+ {len(amendment_document_ids)} amendment(s)"),
        master_document_id=master_document_id,
        amendment_document_ids=json.dumps(amendment_document_ids),
        status="pending",
        progress_note="queued",
        created_by_user_id=created_by_user_id,
        created_at=datetime.utcnow(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def execute_consolidation(
    db: Session,
    run_id: int,
    *,
    progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Run the consolidation end-to-end. Updates the run row in place."""
    run = db.query(ConsolidatedBaselineRun).filter(
        ConsolidatedBaselineRun.id == run_id).first()
    if not run:
        return {"error": "run not found"}

    try:
        amendment_ids: List[int] = json.loads(run.amendment_document_ids) or []
    except (json.JSONDecodeError, TypeError):
        amendment_ids = []

    run.status = "running"
    run.started_at = datetime.utcnow()
    run.progress_note = "Snapshotting master requirements…"
    db.commit()

    try:
        # ── Step 1: snapshot master requirements
        master_reqs = (db.query(RfpRequirement)
                       .filter(RfpRequirement.document_id == run.master_document_id)
                       .order_by(RfpRequirement.section_id, RfpRequirement.id)
                       .all())
        if not master_reqs:
            run.status = "failed"
            run.error = (f"No requirements found on master document "
                         f"{run.master_document_id}.")
            run.completed_at = datetime.utcnow()
            db.commit()
            return {"error": run.error}

        now = datetime.utcnow()
        for r in master_reqs:
            db.add(ConsolidatedRequirement(
                consolidation_run_id=run.id,
                requirement_id=r.requirement_id,
                section_id=r.section_id,
                category=r.category,
                priority=r.priority,
                title=r.title,
                description=r.description,
                source_text=r.source_text,
                source_page=r.source_page,
                original_requirement_id=r.id,
                source_document_id=run.master_document_id,
                is_removed=False,
                is_added_by_amendment=False,
                amendment_history=None,
                created_at=now,
                updated_at=now,
            ))
        db.commit()
        run.total_master_requirements = len(master_reqs)
        run.progress_note = (f"Master snapshot complete ({len(master_reqs)} "
                              f"requirements). Loading amendments…")
        db.commit()

        # ── Step 2: process each amendment in chronological (list) order.
        total_amendment_actions = 0
        action_counts = {"modify": 0, "add": 0, "remove": 0, "skip": 0,
                         "unchanged": 0}
        workers = _consolidator_workers()

        for amend_idx, amend_id in enumerate(amendment_ids, start=1):
            amend_doc = (db.query(IngestedDocument)
                         .filter(IngestedDocument.id == amend_id).first())
            amend_label = (amend_doc.original_filename if amend_doc
                           else f"doc {amend_id}")
            amend_reqs = (db.query(RfpRequirement)
                          .filter(RfpRequirement.document_id == amend_id)
                          .order_by(RfpRequirement.section_id,
                                     RfpRequirement.id)
                          .all())
            if not amend_reqs:
                run.progress_note = (f"[{amend_idx}/{len(amendment_ids)}] "
                                     f"{amend_label}: 0 reqs (skipping)")
                db.commit()
                continue

            run.progress_note = (
                f"[{amend_idx}/{len(amendment_ids)}] {amend_label}: "
                f"applying {len(amend_reqs)} item(s)…"
            )
            db.commit()

            # Build the live consolidated set for this amendment pass —
            # we re-query because earlier amendments may have added rows.
            live = (db.query(ConsolidatedRequirement)
                    .filter(ConsolidatedRequirement.consolidation_run_id == run.id)
                    .filter(ConsolidatedRequirement.is_removed.is_(False))
                    .all())
            # Pre-embed the live set ONCE per amendment (LLM-free, cheap)
            live_vecs: List[Tuple[ConsolidatedRequirement, List[float]]] = []
            for cr in live:
                qtxt = " ".join(filter(None, [
                    cr.title, cr.description, cr.source_text,
                ]))[:4000]
                if not qtxt.strip():
                    continue
                try:
                    v = embed_text(qtxt)
                    live_vecs.append((cr, v))
                except Exception:  # noqa: BLE001
                    continue

            # Snapshot amendment reqs to plain dicts so workers don't
            # touch the SQLAlchemy session.
            amend_snaps = [
                {
                    "id": r.id, "requirement_id": r.requirement_id,
                    "section_id": r.section_id, "category": r.category,
                    "priority": r.priority, "title": r.title,
                    "description": r.description, "source_text": r.source_text,
                    "source_page": r.source_page,
                }
                for r in amend_reqs
            ]

            def _worker(snap: Dict[str, Any]) -> Dict[str, Any]:
                """Pure-Python worker. Returns a decision keyed back to
                snap['id']."""
                try:
                    qtxt = " ".join(filter(None, [
                        snap.get("title"), snap.get("description"),
                        snap.get("source_text"),
                    ]))[:4000]
                    if not qtxt.strip() or not live_vecs:
                        # No text or empty live set → decide deterministically
                        if not qtxt.strip():
                            return {"snap_id": snap["id"], "decision": {
                                "action": "skip",
                                "rationale": "Amendment item has no text.",
                                "consolidated_id": None,
                                "new_section_id": None, "new_title": None,
                                "new_description": None, "new_source_text": None,
                            }}
                        return {"snap_id": snap["id"], "decision": {
                            "action": "add",
                            "rationale": "No live consolidated reqs to compare against.",
                            "consolidated_id": None,
                            "new_section_id": snap.get("section_id"),
                            "new_title": snap.get("title"),
                            "new_description": snap.get("description"),
                            "new_source_text": snap.get("source_text"),
                        }}

                    try:
                        q_vec = embed_text(qtxt)
                    except Exception:  # noqa: BLE001
                        # Embedding failed — safest default is add
                        return {"snap_id": snap["id"], "decision": {
                            "action": "add",
                            "rationale": "Embedding failed; defaulted to add.",
                            "consolidated_id": None,
                            "new_section_id": snap.get("section_id"),
                            "new_title": snap.get("title"),
                            "new_description": snap.get("description"),
                            "new_source_text": snap.get("source_text"),
                        }}

                    scored: List[Tuple[float, ConsolidatedRequirement]] = []
                    for cr, vec in live_vecs:
                        sim = cosine_similarity(q_vec, vec)
                        scored.append((sim, cr))
                    scored.sort(key=lambda x: x[0], reverse=True)
                    top = scored[:TOP_K]

                    if not top or top[0][0] < MIN_SIM_FOR_LLM:
                        # Not similar to anything → deterministic add
                        return {"snap_id": snap["id"], "decision": {
                            "action": "add",
                            "rationale": (f"Top similarity "
                                          f"{(top[0][0] if top else 0):.2f} below LLM threshold."),
                            "consolidated_id": None,
                            "new_section_id": snap.get("section_id"),
                            "new_title": snap.get("title"),
                            "new_description": snap.get("description"),
                            "new_source_text": snap.get("source_text"),
                        }}

                    # Build candidate dicts for the LLM prompt
                    candidates = [
                        {"id": cr.id, "similarity": sim,
                         "section_id": cr.section_id,
                         "title": cr.title, "description": cr.description,
                         "source_text": cr.source_text}
                        for sim, cr in top
                    ]
                    valid_ids = {c["id"] for c in candidates}

                    prompt = _build_action_prompt(amend_label, snap, candidates)
                    raw = _call_ai(prompt, _SYSTEM, max_tokens=1200)
                    env = _parse_json_obj(raw)
                    decision = _validate_action(env or {}, valid_ids, snap)
                    if decision is None:
                        # Unrecoverable — safest no-op
                        decision = {
                            "action": "skip",
                            "rationale": ("Malformed LLM envelope; skipped to "
                                          "avoid corrupting baseline."),
                            "consolidated_id": None,
                            "new_section_id": None, "new_title": None,
                            "new_description": None, "new_source_text": None,
                        }
                    return {"snap_id": snap["id"], "decision": decision}
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"consolidator worker failed for amend req "
                        f"{snap['id']}: {e}"
                    )
                    return {"snap_id": snap["id"], "decision": {
                        "action": "skip",
                        "rationale": f"Worker error: {type(e).__name__}",
                        "consolidated_id": None,
                        "new_section_id": None, "new_title": None,
                        "new_description": None, "new_source_text": None,
                    }}

            # Run workers
            decisions: List[Dict[str, Any]] = []
            if workers <= 1:
                for s in amend_snaps:
                    decisions.append(_worker(s))
            else:
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    futures = [ex.submit(_worker, s) for s in amend_snaps]
                    for fut in as_completed(futures):
                        decisions.append(fut.result())

            # Apply decisions on the main thread (SQLAlchemy session safe)
            cr_by_id = {cr.id: cr for cr, _ in live_vecs}
            applied_in_amendment = 0
            for d in decisions:
                snap_id = d["snap_id"]
                dec = d["decision"]
                action = dec["action"]
                history_entry = {
                    "amendment_doc_id": amend_id,
                    "amendment_label": amend_label,
                    "amendment_req_id": snap_id,
                    "action": action,
                    "rationale": dec.get("rationale"),
                    "applied_at": datetime.utcnow().isoformat(),
                }

                if action == "skip":
                    action_counts["skip"] += 1
                    continue
                if action == "remove" and dec["consolidated_id"]:
                    target = cr_by_id.get(dec["consolidated_id"])
                    if not target:
                        action_counts["skip"] += 1
                        continue
                    target.is_removed = True
                    _append_history(target, history_entry)
                    target.updated_at = datetime.utcnow()
                    action_counts["remove"] += 1
                    applied_in_amendment += 1
                    continue
                if action == "modify" and dec["consolidated_id"]:
                    target = cr_by_id.get(dec["consolidated_id"])
                    if not target:
                        # Lost reference — degrade to add
                        action = "add"
                    else:
                        if dec["new_section_id"]:
                            target.section_id = dec["new_section_id"]
                        if dec["new_title"]:
                            target.title = dec["new_title"]
                        if dec["new_description"]:
                            target.description = dec["new_description"]
                        if dec["new_source_text"]:
                            target.source_text = dec["new_source_text"]
                        target.source_document_id = amend_id
                        _append_history(target, history_entry)
                        target.updated_at = datetime.utcnow()
                        action_counts["modify"] += 1
                        applied_in_amendment += 1
                        continue

                if action == "add":
                    new_row = ConsolidatedRequirement(
                        consolidation_run_id=run.id,
                        requirement_id=None,  # synthesized
                        section_id=dec["new_section_id"],
                        category=None,
                        priority=None,
                        title=dec["new_title"],
                        description=dec["new_description"],
                        source_text=dec["new_source_text"],
                        source_page=None,
                        original_requirement_id=snap_id,
                        source_document_id=amend_id,
                        is_removed=False,
                        is_added_by_amendment=True,
                        amendment_history=json.dumps([history_entry]),
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow(),
                    )
                    db.add(new_row)
                    action_counts["add"] += 1
                    applied_in_amendment += 1

            db.commit()
            total_amendment_actions += len(decisions)
            run.total_amendment_actions = total_amendment_actions
            run.actions_modified = action_counts["modify"]
            run.actions_added = action_counts["add"]
            run.actions_removed = action_counts["remove"]
            run.actions_skipped = action_counts["skip"]
            run.progress_note = (
                f"[{amend_idx}/{len(amendment_ids)}] {amend_label}: "
                f"applied {applied_in_amendment} action(s)."
            )
            db.commit()

            if progress_cb:
                try:
                    progress_cb({
                        "amendment_index": amend_idx,
                        "total_amendments": len(amendment_ids),
                        "amendment_label": amend_label,
                        "applied": applied_in_amendment,
                    })
                except Exception:  # noqa: BLE001
                    pass

        # ── Done
        run.actions_unchanged = run.total_master_requirements - (
            action_counts["modify"] + action_counts["remove"]
        )
        run.status = "complete"
        run.completed_at = datetime.utcnow()
        run.progress_note = (
            f"Complete. Master={run.total_master_requirements}; "
            f"actions: modify={action_counts['modify']} "
            f"add={action_counts['add']} remove={action_counts['remove']} "
            f"skip={action_counts['skip']} "
            f"unchanged={run.actions_unchanged}."
        )
        db.commit()

        return {
            "run_id": run.id,
            "master_requirements": run.total_master_requirements,
            "amendments_processed": len(amendment_ids),
            "total_amendment_actions": total_amendment_actions,
            **action_counts,
            "unchanged": run.actions_unchanged,
        }
    except Exception as e:  # noqa: BLE001
        logger.exception(f"consolidation run {run_id} failed: {e}")
        run.status = "failed"
        run.error = str(e)[:2000]
        run.completed_at = datetime.utcnow()
        db.commit()
        return {"error": str(e)}


def consolidated_requirement_count(
    db: Session, run_id: int, *, include_removed: bool = False,
) -> int:
    q = (db.query(ConsolidatedRequirement)
         .filter(ConsolidatedRequirement.consolidation_run_id == run_id))
    if not include_removed:
        q = q.filter(ConsolidatedRequirement.is_removed.is_(False))
    return q.count()


# ─────────────────────────────────────────────────────────────────────
# Materialization — surface consolidated requirements as RfpRequirement
# rows so the existing diff service can use them without a schema
# change. The diff service stores baseline_requirement_id as an FK to
# rfp_requirements; rather than make that column polymorphic we attach
# the materialized rows to a synthetic IngestedDocument that represents
# the consolidation run.
# ─────────────────────────────────────────────────────────────────────

CONSOLIDATION_DOC_PREFIX = "__consolidation_run__"


def materialize_baseline_as_documents(
    db: Session,
    run_id: int,
) -> Dict[str, Any]:
    """Create (or refresh) a synthetic IngestedDocument representing the
    consolidated baseline, populate it with RfpRequirement rows mirroring
    every NON-removed ConsolidatedRequirement, and return its id.

    Idempotent: re-running clears prior synthetic rows and recreates them
    so the materialized view always reflects the latest consolidation.
    """
    run = db.query(ConsolidatedBaselineRun).filter(
        ConsolidatedBaselineRun.id == run_id).first()
    if not run:
        return {"error": "consolidation run not found"}
    if run.status != "complete":
        return {"error": f"consolidation run is not complete (status={run.status})"}

    synthetic_filename = f"{CONSOLIDATION_DOC_PREFIX}{run_id}"
    syn = (db.query(IngestedDocument)
           .filter(IngestedDocument.filename == synthetic_filename)
           .first())
    if syn:
        # Wipe prior materialized rows so we don't accumulate duplicates
        deleted = (db.query(RfpRequirement)
                   .filter(RfpRequirement.document_id == syn.id)
                   .delete(synchronize_session=False))
        db.commit()
        logger.info(f"materialize_baseline: wiped {deleted} prior rows on doc {syn.id}")
    else:
        syn = IngestedDocument(
            filename=synthetic_filename,
            original_filename=(run.label or f"Consolidated baseline run #{run.id}"),
            file_type="virtual",
            file_size=0,
            source_type="rfp",
            document_type="consolidated_baseline",
            description=(f"Synthetic as-amended baseline produced by "
                          f"consolidation run #{run.id}"),
            total_pages=0,
            total_chunks=0,
            status="completed",
            created_at=datetime.utcnow(),
        )
        db.add(syn)
        db.commit()
        db.refresh(syn)

    # Mirror every live consolidated row into rfp_requirements
    live = (db.query(ConsolidatedRequirement)
            .filter(ConsolidatedRequirement.consolidation_run_id == run.id)
            .filter(ConsolidatedRequirement.is_removed.is_(False))
            .all())
    now = datetime.utcnow()
    created = 0
    for cr in live:
        # Build a stable requirement_id like "CR-<run>-<cr.id>" so it's clear
        # in the UI that this came from consolidation
        req_id = cr.requirement_id or f"CR-{run.id}-{cr.id}"
        db.add(RfpRequirement(
            document_id=syn.id,
            proposal_id=None,  # consolidated baseline isn't tied to a proposal
            requirement_id=req_id,
            section_id=cr.section_id,
            category=cr.category,
            priority=cr.priority,
            title=cr.title,
            description=cr.description,
            source_page=cr.source_page,
            source_text=cr.source_text,
            compliance_status="not_assessed",
            verified=False,
            extraction_pass="consolidation_materialized",
            notes=(f"Materialized from ConsolidatedRequirement {cr.id} "
                    f"(consolidation run #{run.id})"),
            created_at=now,
            updated_at=now,
        ))
        created += 1
    db.commit()
    syn.total_chunks = created  # repurpose for "row count"
    db.commit()

    return {
        "consolidation_run_id": run.id,
        "synthetic_document_id": syn.id,
        "materialized_requirement_count": created,
    }
