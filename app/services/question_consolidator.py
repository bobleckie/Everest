"""
Question Consolidator — reduce a drafted-question set down to a
client-deliverable size.

Strategy
────────
1. Bucket draft questions by `source_section` (with a "by document" fallback).
   Questions about the same part of the RFP are the only ones we compare —
   two unrelated questions shouldn't merge just because they share phrasing.
2. Within each bucket, embed each question's text (+ source_quote) and cluster
   by cosine similarity using single-linkage.
3. For every cluster of size ≥ 2, ask the LLM to write ONE tight question that
   covers all the facets the members were asking about, preserving the most
   specific section cite and the union of categories/priorities.
4. Return a plan: {keep: [...], merge: [{new_question, member_ids: [...]}], ...}
5. Apply-plan re-uses the existing RfpQuestion model, marks merged-away rows
   as `status="superseded"` (instead of deleting) so history is auditable and
   the original drafts can be restored.

Public entry points
  - build_plan(db, proposal_id=None, similarity_threshold=0.80,
               only_priorities=None, only_statuses=("draft",)) -> dict
  - apply_plan(db, plan, actor_user_id=None) -> dict
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import datetime
from typing import Iterable, List, Optional, Sequence

from sqlalchemy.orm import Session

from ..models import RfpQuestion
from .embedding_service import embed_texts, cosine_similarity
from .orchestrator_agent import _call_ai

logger = logging.getLogger(__name__)


# ── Configuration defaults ──────────────────────────────────────────────

DEFAULT_SIMILARITY_THRESHOLD = 0.80
# A cluster larger than this is split — asking the LLM to merge 20 questions at
# once usually produces mush. 6 is a comfortable ceiling.
MAX_CLUSTER_SIZE = 6
# Below this length a cluster is too short to embed reliably; fall back to
# exact section-only bucketing.
MIN_EMBED_TEXT_CHARS = 20


_PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, None: 4}


# ── Section normalization ───────────────────────────────────────────────

_SECTION_RE = re.compile(r"(\d+(?:\.\d+)*)")


def _normalize_section(raw: Optional[str], document_id: Optional[int]) -> str:
    """Return a stable bucket key derived from source_section.

    We truncate the section number to its first two dotted segments so that
    "4.11.3.A" and "4.11.5.B.2" land in the same bucket — both concern
    Section 4.11. Questions without any section fall back to a per-document
    bucket so cross-document merges never happen accidentally.
    """
    if raw:
        m = _SECTION_RE.search(raw)
        if m:
            parts = m.group(1).split(".")
            return "sec:" + ".".join(parts[:2])
        # Non-numeric section identifier (e.g. "Attachment 2") — use the
        # lower-cased alphanumeric prefix as the bucket.
        slug = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
        if slug:
            return f"label:{slug[:40]}"
    return f"doc:{document_id}" if document_id else "doc:none"


# ── Clustering ──────────────────────────────────────────────────────────

def _cluster_questions(
    questions: Sequence[RfpQuestion], threshold: float
) -> List[List[RfpQuestion]]:
    """Single-linkage cosine clustering.

    Returns a list of clusters. Every input question appears in exactly one
    cluster. Singletons are returned as one-element lists.
    """
    if not questions:
        return []
    if len(questions) == 1:
        return [list(questions)]

    texts = [
        f"{(q.question_text or '').strip()}\n{(q.source_quote or '').strip()}"[:2000]
        for q in questions
    ]
    # Skip embedding work if the combined text is too short to be meaningful.
    if any(len(t) < MIN_EMBED_TEXT_CHARS for t in texts):
        # Fall back to trivial "one cluster per question" — no merging.
        return [[q] for q in questions]

    try:
        vectors = embed_texts(texts)
    except Exception as e:
        logger.warning(f"[consolidator] embed_texts failed: {e}; returning singletons")
        return [[q] for q in questions]

    # Union-Find on pairwise similarity
    n = len(questions)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        for j in range(i + 1, n):
            if cosine_similarity(vectors[i], vectors[j]) >= threshold:
                union(i, j)

    groups: dict[int, List[RfpQuestion]] = defaultdict(list)
    for i, q in enumerate(questions):
        groups[find(i)].append(q)

    clusters: List[List[RfpQuestion]] = []
    for members in groups.values():
        # Split oversize clusters into consecutive slices by id so the LLM
        # prompt stays tractable.
        if len(members) <= MAX_CLUSTER_SIZE:
            clusters.append(members)
        else:
            members_sorted = sorted(members, key=lambda q: (q.source_page or 0, q.id))
            for i in range(0, len(members_sorted), MAX_CLUSTER_SIZE):
                clusters.append(members_sorted[i : i + MAX_CLUSTER_SIZE])
    return clusters


# ── Cluster → merged question (LLM) ─────────────────────────────────────

_MERGE_SYSTEM = (
    "You are a senior capture manager preparing questions for the agency "
    "during a formal solicitation Q&A period. You merge near-duplicate draft "
    "questions into a single, precise, neutral question that asks for every "
    "missing specific the drafts collectively cared about. You never invent "
    "facts. You never pad with pleasantries. Respond in strict JSON only."
)


def _merge_prompt(cluster: Sequence[RfpQuestion]) -> str:
    lines: List[str] = []
    lines.append(
        "Below are near-duplicate draft questions generated from the same part "
        "of an RFP. Write ONE consolidated question that:\n"
        "  • asks for every distinct specific any draft asked for\n"
        "  • keeps the most precise section reference (e.g. '4.11.3.A (p.17)')\n"
        "  • is a single grammatical sentence ending in '?'\n"
        "  • uses neutral, professional tone (no 'please', no 'we would like')\n"
        "  • is 40–80 words, preserving every concrete number, date, threshold, "
        "or named criterion from the drafts\n\n"
        "Respond with JSON only, no prose, matching this schema:\n"
        '  {"question_text": string, "rationale": string, '
        '"source_section": string, "category": string, "priority": string}\n\n'
        "Where `category` is one of: clarification, risk, pricing, scope, "
        "competitive, form. `priority` is one of: critical, high, medium, low. "
        "Pick the highest priority and most actionable category represented "
        "in the drafts.\n\n"
        "=== DRAFT QUESTIONS TO MERGE ===\n"
    )
    for i, q in enumerate(cluster, 1):
        lines.append(f"\n[Draft {i}] id={q.id}  section={q.source_section or '-'}  "
                     f"page={q.source_page or '-'}  "
                     f"category={q.category or '-'}  priority={q.priority or '-'}")
        lines.append(f"Question: {(q.question_text or '').strip()}")
        if q.source_quote:
            lines.append(f"Cited text: {q.source_quote.strip()[:400]}")
        if q.rationale:
            lines.append(f"Rationale: {q.rationale.strip()[:300]}")
    return "\n".join(lines)


def _call_merger(cluster: Sequence[RfpQuestion]) -> Optional[dict]:
    prompt = _merge_prompt(cluster)
    try:
        raw = _call_ai(prompt, _MERGE_SYSTEM, max_tokens=1200)
    except Exception as e:
        logger.warning(f"[consolidator] merge LLM call failed for cluster "
                       f"{[q.id for q in cluster]}: {e}")
        return None

    # Tolerant JSON extraction
    text = raw.strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        logger.warning(f"[consolidator] merge response had no JSON object: "
                       f"{text[:200]!r}")
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        logger.warning(f"[consolidator] merge JSON parse failed: {text[:200]!r}")
        return None

    q_text = (data.get("question_text") or "").strip()
    if not q_text or not q_text.endswith("?"):
        logger.warning(f"[consolidator] merge returned non-question: {q_text!r}")
        return None

    return {
        "question_text": q_text,
        "rationale": (data.get("rationale") or "").strip()[:2000],
        "source_section": (data.get("source_section") or "").strip()[:120] or None,
        "category": (data.get("category") or "").strip().lower() or "clarification",
        "priority": (data.get("priority") or "").strip().lower() or "medium",
    }


# ── Plan building ───────────────────────────────────────────────────────

def _summarize_member(q: RfpQuestion) -> dict:
    return {
        "id": q.id,
        "question_text": q.question_text,
        "source_section": q.source_section,
        "source_page": q.source_page,
        "category": q.category,
        "priority": q.priority,
    }


def build_plan(
    db: Session,
    proposal_id: Optional[int] = None,
    document_id: Optional[int] = None,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    only_statuses: Iterable[str] = ("draft",),
    only_priorities: Optional[Iterable[str]] = None,
) -> dict:
    """Cluster candidate questions and produce a preview plan.

    The plan is a pure data structure (no DB writes); `apply_plan` executes
    it. This separation lets the UI show the user what will happen and get
    explicit approval before anything is modified.
    """
    q = db.query(RfpQuestion)
    if proposal_id is not None:
        q = q.filter(RfpQuestion.proposal_id == proposal_id)
    if document_id is not None:
        q = q.filter(RfpQuestion.document_id == document_id)
    statuses = list(only_statuses) if only_statuses else None
    if statuses:
        q = q.filter(RfpQuestion.status.in_(statuses))
    if only_priorities:
        q = q.filter(RfpQuestion.priority.in_(list(only_priorities)))

    candidates = q.all()
    total_in = len(candidates)
    if not candidates:
        return {
            "total_candidates": 0, "buckets": 0, "clusters": 0,
            "merges": [], "keeps": [], "summary": {
                "total_in": 0, "total_out": 0, "merged_away": 0, "merge_clusters": 0,
            },
        }

    # Bucket
    buckets: dict[str, List[RfpQuestion]] = defaultdict(list)
    for r in candidates:
        buckets[_normalize_section(r.source_section, r.document_id)].append(r)

    merges: List[dict] = []
    keeps: List[dict] = []

    for bucket_key, members in buckets.items():
        clusters = _cluster_questions(members, similarity_threshold)
        for cluster in clusters:
            if len(cluster) == 1:
                keeps.append({"bucket": bucket_key, **_summarize_member(cluster[0])})
                continue

            # Stable ordering inside cluster for deterministic prompts
            cluster_sorted = sorted(
                cluster,
                key=lambda q: (_PRIORITY_RANK.get(q.priority, 4), q.source_page or 0, q.id),
            )
            merged = _call_merger(cluster_sorted)
            if merged is None:
                # LLM failed; bail out of the merge, keep every draft.
                for m in cluster_sorted:
                    keeps.append({"bucket": bucket_key, **_summarize_member(m)})
                continue

            # Pick the most specific section cite from the members if the LLM
            # didn't give us one.
            if not merged["source_section"]:
                merged["source_section"] = next(
                    (m.source_section for m in cluster_sorted if m.source_section),
                    None,
                )
            # Highest-priority page wins
            merged_page = next(
                (m.source_page for m in cluster_sorted if m.source_page), None
            )
            # Union of source quotes (cap length)
            quotes = [m.source_quote.strip() for m in cluster_sorted if m.source_quote]
            merged_quote = (" || ".join(dict.fromkeys(quotes)))[:1500] or None

            merges.append({
                "bucket": bucket_key,
                "member_ids": [m.id for m in cluster_sorted],
                "members": [_summarize_member(m) for m in cluster_sorted],
                "new_question": {
                    "question_text": merged["question_text"],
                    "rationale": merged["rationale"],
                    "source_section": merged["source_section"],
                    "source_page": merged_page,
                    "source_quote": merged_quote,
                    "category": merged["category"],
                    "priority": merged["priority"],
                    "document_id": cluster_sorted[0].document_id,
                    "proposal_id": cluster_sorted[0].proposal_id,
                },
            })

    merged_away = sum(len(m["member_ids"]) for m in merges)
    total_out = len(keeps) + len(merges)
    plan = {
        "total_candidates": total_in,
        "buckets": len(buckets),
        "clusters": len(keeps) + len(merges),
        "similarity_threshold": similarity_threshold,
        "merges": merges,
        "keeps": keeps,
        "summary": {
            "total_in": total_in,
            "total_out": total_out,
            "merged_away": merged_away,
            "merge_clusters": len(merges),
            "reduction_pct": round(100 * (total_in - total_out) / total_in, 1)
                if total_in else 0.0,
        },
    }
    logger.info(
        f"[consolidator] plan built: {total_in} → {total_out} "
        f"({len(merges)} merge clusters, {merged_away} merged away)"
    )
    return plan


# ── Plan application ───────────────────────────────────────────────────

def apply_plan(
    db: Session,
    plan: dict,
    actor_user_id: Optional[int] = None,
) -> dict:
    """Execute a plan built by `build_plan`.

    For each merge: insert a new RfpQuestion row with the merged text, mark
    every member as `status="superseded"` (non-destructive — they stay in the
    table and can be restored by flipping the status back), and record the
    merge lineage in the new row's `related_requirement_ids` field (reused as
    a generic pointer because there's no dedicated column for question
    lineage yet).
    """
    if not plan or "merges" not in plan:
        raise ValueError("plan is missing 'merges' — call build_plan first")

    created: List[int] = []
    superseded: List[int] = []
    now = datetime.utcnow()

    for merge in plan["merges"]:
        member_ids = merge.get("member_ids") or []
        new_q = merge.get("new_question") or {}
        if not member_ids or not new_q.get("question_text"):
            continue

        members = (
            db.query(RfpQuestion)
            .filter(RfpQuestion.id.in_(member_ids))
            .all()
        )
        if not members:
            continue

        # Lineage (stored as JSON in related_requirement_ids; the name is
        # historical — the column is just a generic JSON ref list).
        lineage = json.dumps({"superseded_question_ids": member_ids})

        row = RfpQuestion(
            document_id=new_q.get("document_id"),
            proposal_id=new_q.get("proposal_id"),
            source_section=new_q.get("source_section"),
            source_page=new_q.get("source_page"),
            category=new_q.get("category") or "clarification",
            priority=new_q.get("priority") or "medium",
            question_text=new_q["question_text"],
            rationale=(new_q.get("rationale") or "")[:5000] or None,
            source_quote=new_q.get("source_quote"),
            related_requirement_ids=lineage,
            status="draft",
            created_by_ai=True,
            created_by_user_id=actor_user_id,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.flush()  # populate row.id so we can reference it from members
        created.append(row.id)

        for m in members:
            m.status = "superseded"
            m.review_notes = (m.review_notes or "") + (
                f"\n[auto-consolidated into Q{row.id} at "
                f"{now.isoformat(timespec='seconds')}]"
            )
            m.updated_at = now
            superseded.append(m.id)

    db.commit()

    logger.info(
        f"[consolidator] applied plan: created {len(created)} merged questions, "
        f"superseded {len(superseded)} originals"
    )
    return {
        "created_question_ids": created,
        "superseded_question_ids": superseded,
        "created_count": len(created),
        "superseded_count": len(superseded),
    }


def revert_consolidation(db: Session, merged_question_ids: Sequence[int]) -> dict:
    """Undo one or more merges: delete the merged question(s) and restore each
    original member to `status="draft"`. Idempotent-ish: if a merged row is
    already gone, the matching supersedes are still rehydrated."""
    restored: List[int] = []
    removed: List[int] = []

    for mid in merged_question_ids:
        row = db.query(RfpQuestion).filter(RfpQuestion.id == mid).first()
        if row is None:
            continue

        member_ids: List[int] = []
        if row.related_requirement_ids:
            try:
                meta = json.loads(row.related_requirement_ids)
                member_ids = list(meta.get("superseded_question_ids") or [])
            except json.JSONDecodeError:
                member_ids = []

        if member_ids:
            members = (
                db.query(RfpQuestion)
                .filter(RfpQuestion.id.in_(member_ids))
                .filter(RfpQuestion.status == "superseded")
                .all()
            )
            for m in members:
                m.status = "draft"
                m.updated_at = datetime.utcnow()
                restored.append(m.id)

        db.delete(row)
        removed.append(mid)

    db.commit()
    return {"restored_question_ids": restored, "removed_merged_ids": removed}

# ──────────────────────────────────────────────────────────────────────
# LLM-BUCKET CONSOLIDATION
# ──────────────────────────────────────────────────────────────────────
#
# When semantic embeddings aren't available (e.g. sbert blocked by corp
# firewall), this path replaces the cluster-then-merge flow with a single
# LLM pass per section bucket. Claude reads every draft in the bucket and
# returns a ranked, deduplicated, consolidated set — which also lets it
# drop low-value questions, combine partial overlaps, and keep the
# strongest phrasing, in one shot.
#
# Public entry point: build_plan_llm(db, proposal_id, ...) — returns the
# same plan shape as build_plan(), so apply_plan()/revert_consolidation()
# work unchanged.

# Hard cap — very large buckets get split so the prompt stays tractable
# and the LLM doesn't get overwhelmed. Empirically, Claude handles ~25
# drafts per call comfortably.
LLM_MAX_BUCKET_SIZE = 25
LLM_DEFAULT_MAX_WORKERS = 6


_BUCKET_REVIEW_SYSTEM = (
    "You are a senior capture manager preparing a consolidated Q&A submission "
    "for a formal solicitation. You receive a list of draft questions that all "
    "concern the same section of the RFP. You produce a cleaned-up, ranked, "
    "deduplicated set: combine near-duplicates into one tight question, drop "
    "low-value or nitpicky ones, and keep the most important. You preserve "
    "every concrete specific (numbers, dates, thresholds, named criteria) "
    "mentioned in the drafts. You never invent facts. Respond in strict JSON "
    "only — no prose outside the JSON."
)


def _bucket_prompt(
    bucket_key: str,
    drafts: Sequence[RfpQuestion],
    target_max: Optional[int],
) -> str:
    lines: List[str] = []
    lines.append(
        f"Section bucket: {bucket_key}\n"
        f"Draft question count in this bucket: {len(drafts)}\n"
    )
    if target_max:
        lines.append(
            f"Target ≤ {target_max} final questions for this bucket "
            f"(fewer is fine if consolidation allows).\n"
        )
    lines.append(
        "Produce the cleaned-up set of questions for this bucket. For each "
        "output question, tell us which draft ids it covers (may be 1-to-1 "
        "for a kept question, or many-to-1 for a merged one). Drop any draft "
        "that is nitpicky, redundant, or too minor to ask the agency.\n\n"
        "Rules for every output question:\n"
        "  • Single grammatical sentence ending in '?'.\n"
        "  • Neutral, professional tone (no 'please', no 'we would like').\n"
        "  • 40–80 words.\n"
        "  • Keeps the most precise section reference (e.g. '4.11.3.A (p.17)').\n"
        "  • Preserves every concrete number, date, threshold, or named "
        "criterion from the drafts it covers.\n\n"
        "Respond with JSON ONLY in this exact shape:\n"
        "{\n"
        '  "final_questions": [\n'
        "    {\n"
        '      "covers_draft_ids": [int, ...],   // 1+ ids; must be a subset of the drafts I gave you\n'
        '      "question_text": string,\n'
        '      "rationale": string,\n'
        '      "source_section": string,\n'
        '      "category": "clarification"|"risk"|"pricing"|"scope"|"competitive"|"form",\n'
        '      "priority": "critical"|"high"|"medium"|"low"\n'
        "    }\n"
        "  ],\n"
        '  "dropped_draft_ids": [int, ...]         // ids you chose to drop entirely\n'
        "}\n\n"
        "=== DRAFTS IN THIS BUCKET ===\n"
    )
    for d in drafts:
        lines.append(
            f"\n[id={d.id}] section={d.source_section or '-'}  "
            f"page={d.source_page or '-'}  category={d.category or '-'}  "
            f"priority={d.priority or '-'}"
        )
        lines.append(f"Question: {(d.question_text or '').strip()}")
        if d.source_quote:
            lines.append(f"Cited text: {d.source_quote.strip()[:400]}")
        if d.rationale:
            lines.append(f"Rationale: {d.rationale.strip()[:300]}")
    return "\n".join(lines)


def _call_bucket_reviewer(
    bucket_key: str,
    drafts: Sequence[RfpQuestion],
    target_max: Optional[int],
) -> Optional[dict]:
    """Call the LLM once for a bucket. Returns the parsed JSON dict or None
    on unrecoverable error."""
    prompt = _bucket_prompt(bucket_key, drafts, target_max)
    try:
        raw = _call_ai(prompt, _BUCKET_REVIEW_SYSTEM, max_tokens=4000)
    except Exception as e:
        logger.warning(f"[consolidator-llm] bucket {bucket_key!r} LLM call failed: {e}")
        return None

    text = raw.strip()
    # Tolerant JSON extraction — grab the outermost { ... } block.
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        logger.warning(f"[consolidator-llm] no JSON in response for {bucket_key!r}: "
                       f"{text[:200]!r}")
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        logger.warning(f"[consolidator-llm] JSON parse failed for {bucket_key!r}: "
                       f"{e}  body={text[:200]!r}")
        return None

    if not isinstance(data.get("final_questions"), list):
        logger.warning(f"[consolidator-llm] bad shape for {bucket_key!r}: "
                       f"{list(data.keys())}")
        return None
    return data


def _process_bucket_llm(
    bucket_key: str,
    drafts: Sequence[RfpQuestion],
    target_max: Optional[int],
) -> dict:
    """Process one bucket. Returns a sub-plan: {merges: [...], keeps: [...],
    dropped_ids: [...]}.

    If the LLM errors out or returns nonsense, falls back to keeping every
    draft in the bucket untouched (safe default — never loses questions
    silently).
    """
    draft_by_id = {d.id: d for d in drafts}
    sub_merges: List[dict] = []
    sub_keeps: List[dict] = []
    sub_dropped: List[int] = []

    # Split oversized bucket into sub-batches so prompts stay tractable.
    if len(drafts) > LLM_MAX_BUCKET_SIZE:
        logger.info(
            f"[consolidator-llm] bucket {bucket_key!r} has {len(drafts)} drafts; "
            f"splitting into sub-batches of {LLM_MAX_BUCKET_SIZE}"
        )
        drafts_sorted = sorted(drafts, key=lambda q: (q.source_page or 0, q.id))
        batches = [
            drafts_sorted[i : i + LLM_MAX_BUCKET_SIZE]
            for i in range(0, len(drafts_sorted), LLM_MAX_BUCKET_SIZE)
        ]
    else:
        batches = [list(drafts)]

    for batch in batches:
        data = _call_bucket_reviewer(bucket_key, batch, target_max)
        if data is None:
            # Safe fallback — keep every draft as-is.
            for d in batch:
                sub_keeps.append({"bucket": bucket_key, **_summarize_member(d)})
            continue

        seen_ids: set[int] = set()
        batch_draft_by_id = {d.id: d for d in batch}

        for out in data.get("final_questions", []):
            if not isinstance(out, dict):
                continue
            covers_raw = out.get("covers_draft_ids") or []
            # Strict: only accept ids that were actually in this batch.
            covers = [
                int(x) for x in covers_raw
                if isinstance(x, (int, str)) and str(x).lstrip("-").isdigit()
                and int(x) in batch_draft_by_id
            ]
            covers = [cid for cid in covers if cid not in seen_ids]
            if not covers:
                continue
            qt = (out.get("question_text") or "").strip()
            if not qt or not qt.endswith("?"):
                # LLM slipped — skip this output, members fall through to
                # keeps via the "unassigned" sweep below.
                continue

            members = [batch_draft_by_id[cid] for cid in covers]
            seen_ids.update(covers)

            if len(covers) == 1:
                # LLM kept this draft 1-to-1. Record as a keep (we don't need
                # a DB write for pure keeps, but we capture its contents for
                # the preview).
                sub_keeps.append({"bucket": bucket_key, **_summarize_member(members[0])})
                continue

            # Multi-member → merge. Pick best source_page, union of quotes.
            merged_section = (out.get("source_section") or "").strip()[:120] or next(
                (m.source_section for m in members if m.source_section), None
            )
            merged_page = next(
                (m.source_page for m in members if m.source_page), None
            )
            quotes = [m.source_quote.strip() for m in members if m.source_quote]
            merged_quote = (" || ".join(dict.fromkeys(quotes)))[:1500] or None
            sub_merges.append({
                "bucket": bucket_key,
                "member_ids": covers,
                "members": [_summarize_member(m) for m in members],
                "new_question": {
                    "question_text": qt,
                    "rationale": (out.get("rationale") or "").strip()[:2000],
                    "source_section": merged_section,
                    "source_page": merged_page,
                    "source_quote": merged_quote,
                    "category": (out.get("category") or "clarification").strip().lower(),
                    "priority": (out.get("priority") or "medium").strip().lower(),
                    "document_id": members[0].document_id,
                    "proposal_id": members[0].proposal_id,
                },
            })

        # Explicitly-dropped ids
        for did in data.get("dropped_draft_ids") or []:
            try:
                did_int = int(did)
            except (TypeError, ValueError):
                continue
            if did_int in batch_draft_by_id and did_int not in seen_ids:
                sub_dropped.append(did_int)
                seen_ids.add(did_int)

        # Any draft not mentioned at all → treat as a silent keep (defensive;
        # never lose a draft just because the LLM forgot it).
        for d in batch:
            if d.id not in seen_ids:
                sub_keeps.append({"bucket": bucket_key, **_summarize_member(d)})

    return {"merges": sub_merges, "keeps": sub_keeps, "dropped": sub_dropped}


def build_plan_llm(
    db: Session,
    proposal_id: Optional[int] = None,
    document_id: Optional[int] = None,
    only_statuses: Iterable[str] = ("draft",),
    only_priorities: Optional[Iterable[str]] = None,
    target_per_bucket: Optional[int] = None,
    max_workers: int = LLM_DEFAULT_MAX_WORKERS,
) -> dict:
    """Bucket candidate questions by section and LLM-review each bucket.

    Returns the same plan shape as `build_plan()` so `apply_plan()` works
    unchanged. Also attaches `dropped_ids: [int]` to each merge-plan for
    "questions Claude recommends cutting entirely" — these are NOT applied
    automatically; apply_plan only processes merges. If you want to drop
    them, a follow-up endpoint can mark those ids as superseded.
    """
    q = db.query(RfpQuestion)
    if proposal_id is not None:
        q = q.filter(RfpQuestion.proposal_id == proposal_id)
    if document_id is not None:
        q = q.filter(RfpQuestion.document_id == document_id)
    statuses = list(only_statuses) if only_statuses else None
    if statuses:
        q = q.filter(RfpQuestion.status.in_(statuses))
    if only_priorities:
        q = q.filter(RfpQuestion.priority.in_(list(only_priorities)))

    candidates = q.all()
    total_in = len(candidates)
    if not candidates:
        return {
            "total_candidates": 0, "buckets": 0, "clusters": 0,
            "merges": [], "keeps": [], "dropped_ids": [],
            "summary": {"total_in": 0, "total_out": 0, "merged_away": 0,
                        "dropped": 0, "merge_clusters": 0, "reduction_pct": 0.0},
            "mode": "llm-bucket-review",
        }

    # Bucket
    buckets: dict[str, List[RfpQuestion]] = defaultdict(list)
    for r in candidates:
        buckets[_normalize_section(r.source_section, r.document_id)].append(r)

    logger.info(
        f"[consolidator-llm] starting bucket review: {total_in} candidates → "
        f"{len(buckets)} buckets (max_workers={max_workers})"
    )

    all_merges: List[dict] = []
    all_keeps: List[dict] = []
    all_dropped: List[int] = []

    # Process buckets in parallel. Each worker makes 1+ LLM calls; the
    # _call_ai path is thread-safe (it's the same one the drafter uses).
    from concurrent.futures import ThreadPoolExecutor, as_completed

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_bucket = {
            pool.submit(_process_bucket_llm, bkey, members, target_per_bucket): bkey
            for bkey, members in buckets.items()
        }
        for fut in as_completed(future_to_bucket):
            bkey = future_to_bucket[fut]
            try:
                sub = fut.result()
            except Exception as e:
                logger.error(f"[consolidator-llm] bucket {bkey!r} worker failed: {e}")
                # Safe fallback — keep every draft
                for d in buckets[bkey]:
                    all_keeps.append({"bucket": bkey, **_summarize_member(d)})
                continue
            all_merges.extend(sub["merges"])
            all_keeps.extend(sub["keeps"])
            all_dropped.extend(sub["dropped"])
            logger.info(
                f"[consolidator-llm] bucket {bkey!r}: "
                f"{len(buckets[bkey])} in → {len(sub['keeps'])} keeps, "
                f"{len(sub['merges'])} merges, {len(sub['dropped'])} dropped"
            )

    merged_away = sum(len(m["member_ids"]) for m in all_merges)
    total_out = len(all_keeps) + len(all_merges)  # dropped are not in the output
    plan = {
        "mode": "llm-bucket-review",
        "total_candidates": total_in,
        "buckets": len(buckets),
        "clusters": len(all_keeps) + len(all_merges),
        "merges": all_merges,
        "keeps": all_keeps,
        "dropped_ids": all_dropped,
        "summary": {
            "total_in": total_in,
            "total_out": total_out,
            "merged_away": merged_away,
            "dropped": len(all_dropped),
            "merge_clusters": len(all_merges),
            "reduction_pct": round(100 * (total_in - total_out) / total_in, 1)
                if total_in else 0.0,
        },
    }
    logger.info(
        f"[consolidator-llm] plan built: {total_in} → {total_out} "
        f"({len(all_merges)} merges, {merged_away} merged away, "
        f"{len(all_dropped)} dropped)"
    )
    return plan


def apply_dropped(
    db: Session,
    dropped_ids: Sequence[int],
    actor_user_id: Optional[int] = None,
) -> dict:
    """Mark the LLM-recommended 'drop' list as superseded without creating a
    merge row. Separate from apply_plan because dropping is a stronger
    action that the user should confirm independently.
    """
    now = datetime.utcnow()
    n = 0
    rows = db.query(RfpQuestion).filter(RfpQuestion.id.in_(list(dropped_ids))).all()
    for r in rows:
        if r.status == "superseded":
            continue
        r.status = "superseded"
        r.review_notes = (r.review_notes or "") + (
            f"\n[auto-dropped by consolidator at "
            f"{now.isoformat(timespec='seconds')} (actor={actor_user_id})]"
        )
        r.updated_at = now
        n += 1
    db.commit()
    return {"dropped_count": n, "dropped_ids": [r.id for r in rows if r.status == "superseded"]}