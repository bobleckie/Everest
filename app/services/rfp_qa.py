"""RFP Q&A agent.

Vector-searches the RFP documents and synthesizes a grounded answer with
inline citations.

Three accuracy moves vs. a naive vector + LLM call:

1. **Query expansion.** The user's literal question is often the worst
   query — RFPs use formal procurement language ("Source Disclosure",
   "place of performance", "principal place of business") that doesn't
   appear verbatim in the question ("can the call center be outside NJ?").
   Before retrieving we ask a small LLM call to produce 4–6 alternate
   phrasings that an RFP would actually use, run vector search on EACH,
   and merge the results.

2. **Conversation memory.** The endpoint accepts ``history`` — prior
   user/agent turns. We pass it to both the query-expansion call (so
   "research each of them" is interpreted in context) and the final
   answer call.

3. **Honest gap-handling.** If after expanded retrieval still nothing
   relevant is found, the answer explicitly says so AND lists the exact
   queries that were tried, so the user can see the agent really did
   look broadly before giving up.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..models import (
    DocumentChunk, IngestedDocument, Proposal,
    RfpQuestion, RfpRequirement, RfpScheduleEvent,
)
from .embedding_service import cosine_similarity, embed_text
from .orchestrator_agent import _call_ai

logger = logging.getLogger(__name__)


_SYSTEM_ANSWER = (
    "You are a senior Parsons capture analyst answering one question about "
    "ONE specific active RFP. The user is working on a single proposal at a "
    "time — you will be told exactly which one in the ACTIVE RFP block "
    "below. Every excerpt you receive belongs ONLY to that RFP.\n\n"
    "═══════════════════════════════════════════════════════════════\n"
    "STRICT SCOPING RULES (NEVER VIOLATE)\n"
    "═══════════════════════════════════════════════════════════════\n"
    " 1. Every RFP excerpt you receive is from the ACTIVE RFP only. The "
    "    system has already filtered out documents from prior solicitations, "
    "    other proposals, competitor docs, and Parsons reference material. "
    "    You can trust the excerpts are scoped correctly.\n"
    " 2. NEVER reference prior solicitations, prior amendments, or prior "
    "    contract versions UNLESS the user explicitly asks for a "
    "    comparison. If they do, say 'I don't have prior-version data in "
    "    this view' — do NOT fabricate prior-version content.\n"
    " 3. ALWAYS preface answers that quote the RFP with the active RFP's "
    "    name (e.g. 'In the 2026 NJ MVC T1628 rebid, Section 4.5 states...'). "
    "    The user must never wonder which RFP you're describing.\n"
    " 4. If the user asks something the active RFP doesn't address, say "
    "    so explicitly — do NOT borrow from memory or training data.\n\n"
    "DATA SOURCES YOU HAVE\n"
    "═══════════════════════════════════════════════════════════════\n"
    " * **RFP Document Excerpts** — vector-retrieved passages from the "
    "   ACTIVE RFP's PDFs/DOCX files only. Cite as [doc=N p=P].\n"
    " * **Application Data** — schedule events, requirements, and "
    "   questions from the proposal tracker for the ACTIVE proposal. "
    "   When citing these, say 'the schedule tracker shows…' or "
    "   'the requirements database indicates…'.\n\n"
    "RESPONSE STYLE\n"
    "═══════════════════════════════════════════════════════════════\n"
    " * Quote the RFP verbatim when the user is asking about specific "
    "   language, deadlines, dollar amounts, page references.\n"
    " * For overdue events, risks, compliance gaps, or question status, "
    "   the Application Data section is your primary source.\n"
    " * If the data doesn't fully answer the question, say what it DOES "
    "   say + name what's still missing — do not fabricate.\n"
    " * Be direct and helpful. Answer the question first, then provide "
    "   supporting detail. Never tell the user to 'try a different search'.\n"
    " * Length: as long as the question demands. For a single-fact lookup, "
    "   1-2 paragraphs is plenty. For an EXHAUSTIVE-list question (e.g. "
    "   'every LD', 'all SLAs', 'every plan deliverable', 'list all the X'), "
    "   ENUMERATE EVERY ROW you can find in the excerpts — never truncate, "
    "   never summarize, never say 'the rest of the table is referenced "
    "   but not retrieved'. If you see SLA codes or section numbers in the "
    "   excerpts (I-1, I-2, ..., O-1, O-34, P-1, P-3, etc.), every distinct "
    "   code that appears in the excerpts MUST appear in your answer with "
    "   its formula and trigger.\n"
    " * Markdown OK. No JSON. No code fences."
)


_SYSTEM_EXPANSION = (
    "You are a query-expansion assistant for a Parsons RFP search agent. "
    "Given a user's question (and the recent conversation), produce a list "
    "of 4–6 short alternative search queries that an RFP would plausibly "
    "use to talk about the same topic. RFPs use formal procurement "
    "language; user questions are casual. Translate.\n\n"
    "EXAMPLES:\n"
    "  User: 'can the call center be outside NJ?'\n"
    "    → ['call center location', 'place of performance', "
    "       'Source Disclosure Certification', "
    "       'work performed within the United States', "
    "       'in-state preference', 'on-site presence requirement']\n\n"
    "  User: 'how much is the bid bond?'\n"
    "    → ['bid bond', 'bid security', 'performance bond', "
    "       'guarantee deposit', 'security deposit amount']\n\n"
    "  User (after a 'no hits' agent reply): 'try other phrasings'\n"
    "    → derive new variants the AGENT did not already try, based on "
    "      the conversation context.\n\n"
    "Output VALID JSON ONLY: {\"queries\": [\"...\", ...]}\n"
    "No preamble. No code fences."
)


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


def _expand_queries(
    question: str,
    history: List[Dict[str, str]],
) -> List[str]:
    """Return up to 7 search queries (original + up to 6 LLM variants).
    Falls back to just the original on any failure."""
    history_block = ""
    if history:
        recent = history[-8:]
        bits = []
        for h in recent:
            role = (h.get("role") or "user").lower()
            content = (h.get("content") or "").strip()[:1500]
            if not content:
                continue
            label = "USER" if role == "user" else "AGENT"
            bits.append(f"{label}: {content}")
        if bits:
            history_block = ("\nRECENT CONVERSATION (most recent last):\n"
                             + "\n\n".join(bits) + "\n")

    prompt = (
        f"{history_block}"
        f"\nLATEST USER QUESTION: {question}\n\n"
        f"Produce 4–6 alternate search queries an RFP would actually use. "
        f"Output JSON only."
    )
    try:
        raw = _call_ai(prompt, _SYSTEM_EXPANSION, max_tokens=400)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"query-expansion LLM failed: {e}")
        return [question]

    env = _parse_json_obj(raw or "")
    queries: List[str] = [question.strip()]
    seen = {question.strip().lower()}
    if env and isinstance(env.get("queries"), list):
        for q in env["queries"]:
            if not isinstance(q, str):
                continue
            qs = q.strip()
            if not qs or len(qs) > 200:
                continue
            if qs.lower() in seen:
                continue
            seen.add(qs.lower())
            queries.append(qs)
            if len(queries) >= 7:
                break
    return queries


def _extract_keyword_phrases(query: str) -> List[str]:
    """Extract distinctive multi-word phrases from a query for direct
    substring search. Captures short factual phrases like
    'hours per week', '55 hours', 'evaluation criteria' that vector
    search can fuzz over.

    Returns up to ~6 phrases — bigrams + key trigrams + standalone numbers
    that appear with units."""
    if not query:
        return []
    q = query.lower()
    # Tokenize, keep alphanumerics + apostrophes
    import re as _re
    tokens = _re.findall(r"[a-z0-9][a-z0-9'-]*", q)
    # Common stopwords we don't want as standalone phrases
    stop = {"the", "a", "an", "of", "for", "is", "it", "in", "on", "to", "be",
            "are", "was", "were", "this", "that", "these", "those", "and", "or",
            "but", "what", "where", "when", "how", "why", "do", "does", "did",
            "can", "could", "would", "should", "will", "have", "has", "had",
            "rfp", "section"}
    out: List[str] = []
    seen: set = set()

    # Bigrams of non-stopwords (e.g. "hours week", "55 hours")
    for i in range(len(tokens) - 1):
        a, b = tokens[i], tokens[i + 1]
        if a in stop and b in stop:
            continue
        bg = f"{a} {b}"
        if bg not in seen and len(bg) >= 6:
            out.append(bg)
            seen.add(bg)

    # Trigrams that have at least one non-stopword and >= 12 chars (more
    # selective phrases like "minimum hours per week")
    for i in range(len(tokens) - 2):
        a, b, c = tokens[i], tokens[i + 1], tokens[i + 2]
        if a in stop and c in stop:
            continue
        tg = f"{a} {b} {c}"
        if tg not in seen and len(tg) >= 12:
            out.append(tg)
            seen.add(tg)

    # Numeric anchors: any number that appears in the query becomes its own
    # phrase (e.g. "55", "1500"). Useful when the user remembers a value.
    for t in tokens:
        if t.isdigit() and len(t) >= 2 and t not in seen:
            out.append(t)
            seen.add(t)

    # ── Section / SLA / appendix codes ──────────────────────────────
    # User questions often imply a structural location (e.g. "what's
    # in 4.12", "is O-34 a wait time SLA"). Extract these explicitly so
    # the keyword pass anchors on the chunks that actually contain them.
    # Patterns:
    #   * SLA row codes: O-NN, P-NN, S-NN, F-NN  (uppercased in the doc)
    #   * Section numbers: N.N, N.N.N, N.N.N.N
    #   * Appendix refs: appendix N, exhibit X
    #   * Common SOW headers we know matter: "scope of work", "statement of work"
    raw_q = query  # case-sensitive for SLA codes
    for m in _re.findall(r"\b[OPSF]-\d{1,3}\b", raw_q, flags=_re.IGNORECASE):
        s = m.upper()
        if s not in seen:
            out.append(s)
            seen.add(s)
    # Section numbers like 4.12, 5.8.4, 4.20.9.3
    for m in _re.findall(r"\b\d+\.\d+(?:\.\d+){0,3}\b", q):
        if m not in seen and len(m) >= 3:
            out.append(m)
            seen.add(m)

    return out[:14]  # was 8 — bumped because section/SLA codes deserve room


def _keyword_match_chunks(
    chunks: List[Any],
    queries: List[str],
) -> Dict[int, Tuple[float, str]]:
    """Direct substring search across chunks. Returns
    {chunk_id: (score, matched_phrase)} for chunks containing any query
    phrase. Score is normalized by phrase **rarity** (IDF-style) so a
    chunk matching a distinctive phrase like "55 hours each week" beats
    a chunk that matches common phrases like "hours of operation".

    Chunks that contain the matched phrase NEAR a section header (i.e.
    section_id is set AND content starts with that section header) get
    an additional boost since they're likely the AUTHORITATIVE source
    for the user's question.
    """
    if not chunks or not queries:
        return {}
    # Pre-extract phrases per query and combine
    all_phrases: List[str] = []
    seen: set = set()
    for q in queries:
        for p in _extract_keyword_phrases(q):
            if p not in seen:
                all_phrases.append(p)
                seen.add(p)
    if not all_phrases:
        return {}

    # Pre-compute document frequency for each phrase (how many chunks contain it).
    # Rare phrases are far more discriminative than common ones.
    phrase_df: Dict[str, int] = {p: 0 for p in all_phrases}
    chunk_contents: List[Tuple[Any, str]] = []
    for c in chunks:
        content_lc = (c.content or "").lower()
        if not content_lc:
            continue
        chunk_contents.append((c, content_lc))
        for p in all_phrases:
            if p in content_lc:
                phrase_df[p] += 1

    total_chunks = max(1, len(chunk_contents))
    # IDF weight: log(N / df) — capped so a phrase appearing in 1 chunk
    # doesn't dominate completely. Also floor at 0.05 so common phrases
    # still contribute a tiny score (they're not zero-information).
    import math
    phrase_weight: Dict[str, float] = {}
    for p, df in phrase_df.items():
        if df == 0:
            phrase_weight[p] = 0.0
        else:
            # Higher weight = rarer phrase
            w = math.log(1 + total_chunks / df)
            # Normalize to a reasonable 0-1 range
            phrase_weight[p] = max(0.05, min(1.0, w / 8.0))

    matches: Dict[int, Tuple[float, str]] = {}
    for c, content_lc in chunk_contents:
        # Score = sum of IDF weights of unique matched phrases
        hit_phrases: List[Tuple[str, float]] = []
        for p in all_phrases:
            if p in content_lc:
                hit_phrases.append((p, phrase_weight[p]))
        if not hit_phrases:
            continue
        # Sort by weight desc so the "best" matched phrase is reported
        hit_phrases.sort(key=lambda x: -x[1])
        score = min(1.0, sum(w for _, w in hit_phrases) / 2.0)
        # Boost: if this chunk has a section_id AND the chunk text starts
        # with that section header, it's the authoritative chunk for that
        # section — bump it.
        sect_id = getattr(c, "section_id", None)
        if sect_id and content_lc.lstrip().startswith(sect_id.lower()):
            score = min(1.0, score + 0.30)
        matches[c.id] = (score, hit_phrases[0][0])
    return matches


def _retrieve_chunks_multi(
    db: Session,
    proposal_id: int,
    queries: List[str],
    *,
    top_k_per_query: int = 8,
    final_top_k: int = 10,
    min_sim: float = 0.15,
    page_neighbour_radius: int = 1,
    page_neighbour_top: int = 3,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Run vector retrieval for each query, merge by chunk_id keeping the
    max similarity. Returns (top hits, per-query hit count).

    Hybrid retrieval: for every query we run BOTH vector similarity AND
    direct keyword/substring search. Vector retrieval is great for
    semantic matches but can miss exact-phrase content (e.g. "at least
    55 hours each week" buried in a tangentially-named section). The
    keyword pass guarantees that any chunk containing a literal phrase
    from the user's question lands in the candidate pool.
    """
    # Exclude any superseded docs (Parsons-knowledge supersession applies
    # to docs of any source_type; a future RFP-amendment supersession would
    # use the same column).
    docs = (db.query(IngestedDocument)
            .filter(IngestedDocument.proposal_id == proposal_id)
            .filter(IngestedDocument.source_type == "rfp")
            .filter(IngestedDocument.superseded_by_document_id.is_(None))
            .all())
    if not docs:
        return [], {q: 0 for q in queries}
    doc_meta = {d.id: (d.original_filename or d.filename or f"doc {d.id}")
                for d in docs}
    chunks = (db.query(DocumentChunk)
              .filter(DocumentChunk.document_id.in_(list(doc_meta.keys())))
              .all())
    if not chunks:
        return [], {q: 0 for q in queries}

    chunk_vecs: List[Tuple[DocumentChunk, List[float]]] = []
    chunk_by_id: Dict[int, DocumentChunk] = {}
    for c in chunks:
        chunk_by_id[c.id] = c
        if not c.embedding:
            continue
        try:
            vec = json.loads(c.embedding)
        except (json.JSONDecodeError, TypeError):
            continue
        chunk_vecs.append((c, vec))

    merged: Dict[int, Dict[str, Any]] = {}
    per_query_counts: Dict[str, int] = {}
    # ── Vector pass ──────────────────────────────────────────────────
    for q in queries:
        try:
            q_vec = embed_text(q)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"embed failed for query {q!r}: {e}")
            per_query_counts[q] = 0
            continue
        scored: List[Tuple[float, DocumentChunk]] = []
        for c, vec in chunk_vecs:
            sim = cosine_similarity(q_vec, vec)
            if sim >= min_sim:
                scored.append((sim, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        per_query_counts[q] = len(scored)
        for sim, c in scored[:top_k_per_query]:
            existing = merged.get(c.id)
            if existing is None or existing["similarity"] < sim:
                merged[c.id] = {
                    "chunk_id": c.id,
                    "document_id": c.document_id,
                    "document_name": doc_meta[c.document_id],
                    "page": c.page_number,
                    "section_id": c.section_id,
                    "similarity": float(sim),
                    "content": c.content or "",
                    "matched_query": q,
                    "match_type": "vector",
                }

    # ── Keyword pass (guarantees exact-phrase chunks land in pool) ───
    # Score scaling: keyword-matched chunks get sim = 0.55 + score * 0.45.
    # A high-IDF score (rare-phrase match in an authoritative section)
    # produces sim ~1.0 and comfortably outranks vector hits (typically
    # 0.55–0.65). A low-IDF match (common phrase only) produces sim ~0.6
    # which competes with mid-tier vector hits.
    keyword_matches = _keyword_match_chunks(chunks, queries)
    for cid, (score, phrase) in keyword_matches.items():
        c = chunk_by_id.get(cid)
        if c is None:
            continue
        existing = merged.get(cid)
        boosted = 0.55 + score * 0.45
        # Take the max of the existing similarity (vector) and the keyword
        # boost — chunks that match BOTH are genuinely the strongest hits.
        if existing:
            boosted = max(boosted, existing["similarity"])
        if existing is None or boosted > existing["similarity"]:
            merged[cid] = {
                "chunk_id": cid,
                "document_id": c.document_id,
                "document_name": doc_meta.get(c.document_id, f"doc {c.document_id}"),
                "page": c.page_number,
                "section_id": c.section_id,
                "similarity": boosted,
                "content": c.content or "",
                "matched_query": f"keyword:{phrase!r}",
                "match_type": "keyword" if existing is None else "hybrid",
            }

    # ── Page-neighbour expansion ──────────────────────────────────────
    # Tables (SLA matrix, fee schedules, deliverables) often span several
    # chunks per page. Vector search ranks one or two of them; the rest
    # have lower scores and get cut. Pull every OTHER chunk on the same
    # page (and ±N pages) for the top-K hits so the LLM sees the full
    # table, not a fragment. Radius and top-count are tunable per query
    # type — exhaustive questions ("list every X") use larger values to
    # cover multi-page table spans.
    top_for_expansion = sorted(
        merged.values(), key=lambda x: x["similarity"], reverse=True
    )[:max(1, page_neighbour_top)]
    expansion_added = 0
    for top in top_for_expansion:
        target_doc = top["document_id"]
        target_page = top["page"]
        if target_page is None:
            continue
        for c in chunks:
            if c.id in merged:
                continue
            if c.document_id != target_doc:
                continue
            if c.page_number is None:
                continue
            if abs(c.page_number - target_page) > page_neighbour_radius:
                continue
            # Add as a "neighbour" entry with a slightly-discounted similarity
            # so it ranks below real hits but still survives the top-k cut.
            merged[c.id] = {
                "chunk_id": c.id,
                "document_id": c.document_id,
                "document_name": doc_meta.get(c.document_id, f"doc {c.document_id}"),
                "page": c.page_number,
                "section_id": c.section_id,
                "similarity": max(0.30, top["similarity"] - 0.15),
                "content": c.content or "",
                "matched_query": f"page-neighbour-of-chunk-{top['chunk_id']}",
                "match_type": "neighbour",
            }
            expansion_added += 1
    if expansion_added:
        logger.info(
            "rfp_qa page-neighbour expansion added %d chunks (radius=%d, top=%d)",
            expansion_added, page_neighbour_radius, page_neighbour_top,
        )

    out = sorted(merged.values(), key=lambda x: x["similarity"], reverse=True)
    out = out[:final_top_k]
    for h in out:
        snippet = h["content"].strip()
        if len(snippet) > 1500:
            snippet = snippet[:1500].rsplit(" ", 1)[0] + "…"
        h["snippet"] = snippet
        h["similarity"] = round(h["similarity"], 4)
        del h["content"]
    return out, per_query_counts


# ─────────────────────────────────────────────────────────────────────
# Structured data sources — schedule events, requirements, questions
# ─────────────────────────────────────────────────────────────────────
def _active_rfp_context(db: Session, proposal_id: int) -> str:
    """Build the 'ACTIVE RFP' block the LLM sees in every answer prompt.

    Names the proposal explicitly and lists the documents in scope, so the
    LLM can never confuse it with a prior solicitation, another proposal,
    or training data. Also names what is OUT of scope.
    """
    p = db.query(Proposal).filter(Proposal.id == proposal_id).first()
    docs = (
        db.query(IngestedDocument)
        .filter(IngestedDocument.proposal_id == proposal_id)
        .filter(IngestedDocument.source_type == "rfp")
        .all()
    )

    bits: List[str] = []
    bits.append("═══════════════════════════════════════════════════════════════")
    bits.append("ACTIVE RFP (the ONLY solicitation you can reference)")
    bits.append("═══════════════════════════════════════════════════════════════")
    if p:
        bits.append(f"Title:               {p.title or '(untitled)'}")
        if p.solicitation_number:
            bits.append(f"Solicitation number: {p.solicitation_number}")
        if p.rfp_reference:
            bits.append(f"RFP reference:       {p.rfp_reference}")
        if p.issuing_agency:
            bits.append(f"Issuing agency:      {p.issuing_agency}")
        if p.due_date:
            bits.append(f"Proposal due:        {p.due_date.strftime('%Y-%m-%d')}")
        if p.status:
            bits.append(f"Status:              {p.status}")
    bits.append("")
    bits.append(f"DOCUMENTS IN SCOPE FOR THIS RFP ({len(docs)} total):")
    for d in docs:
        type_str = f" [{d.document_type}]" if d.document_type else ""
        bits.append(f"  • doc={d.id}: {d.original_filename or d.filename}{type_str}")
    bits.append("")
    bits.append("OUT OF SCOPE — do not reference unless the user explicitly asks:")
    bits.append("  • Any prior solicitation or amendment from previous years")
    bits.append("  • Other Parsons proposals or capability dossiers")
    bits.append("  • Competitor proposal documents")
    bits.append("  • Anything from your training data not in the documents above")
    bits.append("═══════════════════════════════════════════════════════════════")
    return "\n".join(bits)


# The vector search only covers document chunks. But the user's question
# often refers to structured data (schedule events, risk factors,
# questions, requirements) that lives in dedicated DB tables. We pull
# relevant structured data and inject it into the LLM context alongside
# the document excerpts so the agent can answer from ALL data sources.

# Keywords that hint the user is asking about specific data domains.
_SCHEDULE_KEYWORDS = {
    "schedule", "event", "deadline", "due date", "overdue", "missed",
    "milestone", "timeline", "upcoming", "calendar", "mandatory date",
    "site visit", "pre-proposal", "oral presentation", "award date",
    "questions due", "proposal due", "submission date",
}
_QUESTION_KEYWORDS = {
    "question", "clarification", "submitted question", "q&a", "qanda",
    "ask the agency", "critical question", "unsubmitted",
}
_REQUIREMENT_KEYWORDS = {
    "requirement", "compliance", "mandatory", "scored", "not assessed",
    "non-compliant", "partial", "parsons evidence", "gap",
}


def _detect_data_domains(question: str, queries: List[str]) -> List[str]:
    """Detect which structured data domains the user might be asking about.
    Returns a list of domain names: 'schedule', 'questions', 'requirements'."""
    combined = " ".join([question] + queries).lower()
    domains = []
    if any(kw in combined for kw in _SCHEDULE_KEYWORDS):
        domains.append("schedule")
    if any(kw in combined for kw in _QUESTION_KEYWORDS):
        domains.append("questions")
    if any(kw in combined for kw in _REQUIREMENT_KEYWORDS):
        domains.append("requirements")
    return domains


def _fetch_schedule_context(db: Session, proposal_id: int) -> str:
    """Return a formatted block of schedule events for the proposal."""
    events = (
        db.query(RfpScheduleEvent)
        .filter(RfpScheduleEvent.proposal_id == proposal_id)
        .order_by(RfpScheduleEvent.event_date.asc().nullslast())
        .limit(50)
        .all()
    )
    if not events:
        return ""
    now = datetime.utcnow()
    # Terminal states — events in these states are NOT considered overdue
    # even if their date has passed (the user finished or cancelled them).
    terminal_statuses = {"complete", "cancelled"}
    lines = ["SCHEDULE EVENTS (from the proposal's schedule tracker):"]
    for ev in events:
        date_str = ev.event_date.strftime("%Y-%m-%d") if ev.event_date else "(no date)"
        status = ev.status or "upcoming"
        # An event is overdue if its date has passed AND it isn't in a
        # terminal state. Includes upcoming and in_progress past-due events.
        if ev.event_date and ev.event_date < now and status not in terminal_statuses:
            status = "OVERDUE"
        mandatory = " [MANDATORY]" if ev.is_mandatory else ""
        line = (
            f"  • {ev.label or ev.event_type} — {date_str} — status: {status}"
            f"{mandatory}"
        )
        if ev.source_text:
            line += f"\n    Source: {ev.source_text[:200]}"
        lines.append(line)
    return "\n".join(lines)


def _fetch_questions_context(db: Session, proposal_id: int,
                             limit: int = 20) -> str:
    """Return a formatted block of RFP questions."""
    qs = (
        db.query(RfpQuestion)
        .filter(RfpQuestion.proposal_id == proposal_id)
        .filter(RfpQuestion.status.in_(["draft", "reviewed", "approved", "submitted"]))
        .order_by(RfpQuestion.priority.desc(), RfpQuestion.id)
        .limit(limit)
        .all()
    )
    if not qs:
        return ""
    lines = [f"RFP QUESTIONS ({len(qs)} shown, by priority):"]
    for q in qs:
        lines.append(
            f"  • [#{q.id}] ({q.priority or 'medium'}/{q.status}) "
            f"{(q.question_text or '')[:200]}"
        )
    return "\n".join(lines)


def _fetch_requirements_context(db: Session, proposal_id: int,
                                limit: int = 30) -> str:
    """Return a formatted summary of requirements status."""
    reqs = (
        db.query(RfpRequirement)
        .filter(RfpRequirement.proposal_id == proposal_id)
        .order_by(RfpRequirement.priority.desc(), RfpRequirement.id)
        .limit(limit)
        .all()
    )
    if not reqs:
        return ""
    lines = [f"RFP REQUIREMENTS ({len(reqs)} shown, by priority):"]
    for r in reqs:
        lines.append(
            f"  • [{r.requirement_id or r.id}] §{r.section_id or '?'} "
            f"({r.category or '?'}/{r.priority or '?'}) "
            f"compliance={r.compliance_status or 'not_assessed'} "
            f"— {(r.title or '')[:150]}"
        )
    return "\n".join(lines)


def _build_answer_prompt(
    question: str,
    history: List[Dict[str, str]],
    queries_used: List[str],
    hits: List[Dict[str, Any]],
    per_query_counts: Dict[str, int],
    structured_blocks: Optional[List[str]] = None,
    rfp_context: Optional[str] = None,
    excerpt_char_budget: int = 32000,
) -> str:
    bits: List[str] = []
    # ── Scope lock: tell the LLM exactly which RFP it is reading ─────
    if rfp_context:
        bits.append(rfp_context)
        bits.append("")
    if history:
        recent = history[-8:]
        history_lines = []
        for h in recent:
            role = (h.get("role") or "user").lower()
            content = (h.get("content") or "").strip()
            if not content:
                continue
            label = "USER" if role == "user" else "AGENT"
            history_lines.append(f"{label}: {content[:2500]}")
        if history_lines:
            bits.append("CONVERSATION SO FAR (most recent last)")
            bits.append("\n\n".join(history_lines))
            bits.append("")
    bits.append(f"LATEST USER QUESTION: {question}")
    bits.append("")

    # ── Structured data from the application's own database ──────────
    # Schedule events, requirements, questions, etc. that are NOT in the
    # RFP document text but are part of the proposal management data.
    has_structured = structured_blocks and any(b.strip() for b in structured_blocks)
    if has_structured:
        bits.append("APPLICATION DATA (from the proposal tracker, not raw RFP text):")
        for block in structured_blocks:
            if block.strip():
                bits.append(block)
        bits.append("")

    # ── RFP document excerpts from vector search ─────────────────────
    # Budget: 32k chars (was 12k). Modern Claude/GPT context windows are
    # 100k+ — we were leaving most of it on the floor and slicing tables
    # in half. Each chunk averages ~870 chars in this codebase, so 32k
    # comfortably fits ~35 chunks at full length.
    if hits:
        bits.append(f"RETRIEVED RFP EXCERPTS ({len(hits)} — all from the ACTIVE RFP only)")
        used = 0
        for i, h in enumerate(hits, 1):
            section_str = f" §{h['section_id']}" if h.get("section_id") else ""
            tag = f" {h['match_type']}" if h.get("match_type") and h["match_type"] != "vector" else ""
            block = (
                f"\n-- Excerpt {i} (doc={h['document_id']} p={h['page']}"
                f"{section_str} sim={h['similarity']:.2f}{tag}) --\n"
                f"From ACTIVE RFP document: {h['document_name']}\n"
                f"{h['snippet']}\n"
            )
            if used + len(block) > excerpt_char_budget:
                bits.append(f"\n…{len(hits) - i + 1} more excerpts truncated.")
                break
            bits.append(block)
            used += len(block)
    else:
        bits.append("RETRIEVED RFP EXCERPTS: NONE")
    bits.append("")

    # ── Answering instructions ───────────────────────────────────────
    if has_structured or hits:
        bits.append(
            "Answer the user's question using ALL the data above — both the "
            "application data AND the RFP excerpts. For claims from RFP text, "
            "cite as [doc=N p=P]. For claims from application data (schedule "
            "events, questions, requirements), cite the data source (e.g. "
            "'the schedule tracker shows…'). Be specific and direct. "
            "Markdown OK."
        )
    else:
        bits.append(
            "No relevant data was found from any source. Answer the user "
            "honestly: explain that you checked both the RFP documents and "
            "the proposal's schedule, requirements, and questions but found "
            "no matching content. Suggest what they could check manually. "
            "Do NOT invent facts."
        )
    return "\n".join(bits)


def ask_rfp(
    db: Session,
    proposal_id: int,
    question: str,
    *,
    history: Optional[List[Dict[str, str]]] = None,
    top_k: int = 10,
) -> Dict[str, Any]:
    """Answer the user's RFP question using ALL available data sources:

    1. **Query expansion** — LLM produces alternate search queries.
    2. **Vector retrieval** — searches RFP document chunks.
    3. **Structured data** — automatically detects if the question relates
       to schedule events, requirements, or questions and pulls from those
       DB tables too. This means "what event is overdue?" finds the actual
       schedule event, not just RFP text about deadlines.
    4. **Auto-broadening** — if the initial vector search returns nothing,
       we silently widen the similarity threshold, try broader queries,
       AND always include schedule context (the most common miss-case).
       The user never sees "no results, try rephrasing" — we do that work
       internally.

    ``history`` — list of prior turns: ``[{"role":"user"|"agent",
    "content":"..."}]``. Most recent last."""
    q = (question or "").strip()
    if not q:
        return {"error": "question is required"}
    history = history or []

    queries = _expand_queries(q, history)
    logger.info(f"rfp_qa proposal={proposal_id} queries={queries}")

    # Heuristic: questions that ask for a table / list / "all of N" need
    # more context to answer correctly. Bump top_k 2x for these so we
    # don't slice the SLA matrix in half.
    q_lc = q.lower()
    table_signals = (
        "table", "thresholds", "threshold", "formula", "schedule of",
        "all of", "every", "list", "matrix", "sla", "liquidated damages",
        "all the", "each of", "rows", "deliverables", "all plans",
    )
    # Stronger "exhaustive" signal — the user is explicitly demanding
    # completeness. For these we widen the page-neighbour radius (multi-
    # page tables get covered end-to-end) and crank top_k further.
    exhaustive_signals = (
        "every ", " all ", "all the ", "exhaustive", "complete list",
        "do not stop", "don't stop", "until you have", "until you find",
        "all of them", "every single", "each and every",
    )
    is_tabley = any(s in q_lc for s in table_signals)
    is_exhaustive = any(s in q_lc for s in exhaustive_signals)
    if is_exhaustive:
        # Cover multi-page tables — SLA matrices in T1628 span 7 pages.
        effective_top_k = max(top_k, 50)
        page_radius = 4
        page_top = 5
    elif is_tabley:
        effective_top_k = max(top_k, 22)
        page_radius = 1
        page_top = 3
    else:
        effective_top_k = top_k
        page_radius = 1
        page_top = 3
    if is_tabley or is_exhaustive:
        logger.info(
            "rfp_qa style=%s top_k=%d page_radius=%d page_top=%d",
            "exhaustive" if is_exhaustive else "tabley",
            effective_top_k, page_radius, page_top,
        )

    hits, per_query_counts = _retrieve_chunks_multi(
        db, proposal_id, queries,
        top_k_per_query=14 if is_exhaustive else (12 if is_tabley else 8),
        final_top_k=effective_top_k, min_sim=0.15,
        page_neighbour_radius=page_radius,
        page_neighbour_top=page_top,
    )

    # ── Structured data: detect which domains to pull ────────────────
    # We ALWAYS detect domains from the question. If the initial vector
    # search found nothing, we also pull schedule data by default (the
    # most common failure case: dashboard risk mentions overdue events
    # and the user asks about it).
    domains = _detect_data_domains(q, queries)
    if not hits and "schedule" not in domains:
        # Auto-broaden: if nothing found, also try schedule + requirements
        domains = list(set(domains + ["schedule", "requirements"]))
        logger.info(f"rfp_qa auto-broadening: adding {domains}")

    structured_blocks: List[str] = []
    if "schedule" in domains:
        block = _fetch_schedule_context(db, proposal_id)
        if block:
            structured_blocks.append(block)
    if "questions" in domains:
        block = _fetch_questions_context(db, proposal_id)
        if block:
            structured_blocks.append(block)
    if "requirements" in domains:
        block = _fetch_requirements_context(db, proposal_id)
        if block:
            structured_blocks.append(block)

    # ── Auto-retry with lower threshold if still empty ───────────────
    if not hits and not structured_blocks:
        # Try again with a much lower similarity threshold
        hits, per_query_counts = _retrieve_chunks_multi(
            db, proposal_id, queries,
            top_k_per_query=14 if is_exhaustive else (12 if is_tabley else 8),
            final_top_k=effective_top_k, min_sim=0.05,
            page_neighbour_radius=page_radius,
            page_neighbour_top=page_top,
        )
        if hits:
            logger.info(f"rfp_qa auto-retry with min_sim=0.05 found {len(hits)} hits")

    rfp_context = _active_rfp_context(db, proposal_id)
    prompt = _build_answer_prompt(
        q, history, queries, hits, per_query_counts,
        structured_blocks=structured_blocks,
        rfp_context=rfp_context,
        # Grow the per-prompt excerpt budget for exhaustive questions —
        # 32 SLA rows * ~700 chars each + framing easily clears 32k.
        excerpt_char_budget=64000 if is_exhaustive else 32000,
    )
    # Answer length budget. Claude Opus 4.x supports up to 32k output
    # tokens; for an exhaustive comparison of 50+ SLA rows + tables +
    # cost scenarios we need real headroom. Earlier 8k cap was visibly
    # truncating answers mid-paragraph (e.g. "Daily average across all
    # operational hours = 32" with no completion).
    answer_max_tokens = (
        16000 if is_exhaustive else
        8000  if is_tabley     else
        2400
    )
    try:
        answer = _call_ai(prompt, _SYSTEM_ANSWER, max_tokens=answer_max_tokens) or ""
    except Exception as e:  # noqa: BLE001
        logger.exception(f"rfp_qa LLM call failed: {e}")
        return {"error": f"LLM call failed: {type(e).__name__}"}

    # ── Self-iteration: keep digging until the LLM stops saying it
    # ── needs more.
    # The LLM's own honest "I still need X" / "still missing" / "would
    # need to retrieve Y" phrases are the perfect trigger to run another
    # targeted retrieval. Without this loop the user has to manually
    # paste the gap back as a new question every time, then again, and
    # again. Cap iterations so a stubborn model can't loop forever.
    iteration_trace: list[dict] = [{"iter": 1, "answer_chars": len(answer)}]
    seen_chunk_ids = {h.get("chunk_id") for h in hits}
    if is_exhaustive or is_tabley:  # only worth the cost on enumeration questions
        # Iteration cap. Each iteration is one targeted retrieval + one
        # LLM follow-up call, so the total wall time scales roughly
        # linearly. Exhaustive questions (every LD, complete comparison,
        # etc.) get more rope because they're the failure mode the user
        # actually cares about — "you keep promising more and never
        # delivering". Generous cap, but still hard.
        max_iters = 5 if is_exhaustive else 2
        for it in range(2, max_iters + 1):
            still_missing = _detect_still_missing(answer)
            if not still_missing:
                break
            logger.info(
                "rfp_qa iter %d: model reports gap (%s) — running targeted retrieval",
                it, still_missing[:120],
            )
            # Ask the LLM to convert its self-reported gap into concrete
            # search queries we can run. Falls back to the gap text itself.
            gap_queries = _gap_to_queries(still_missing) or [still_missing]
            try:
                more_hits, _ = _retrieve_chunks_multi(
                    db, proposal_id, gap_queries,
                    top_k_per_query=14, final_top_k=40, min_sim=0.10,
                    page_neighbour_radius=4, page_neighbour_top=5,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("rfp_qa iter %d retrieval failed: %s", it, e)
                break
            new_hits = [h for h in more_hits if h.get("chunk_id") not in seen_chunk_ids]
            for h in new_hits:
                seen_chunk_ids.add(h.get("chunk_id"))
            if not new_hits:
                logger.info("rfp_qa iter %d: targeted retrieval returned nothing new — stopping", it)
                break
            # Combine with prior hits, keeping the highest-scoring
            # version of any duplicates and re-sorting by similarity.
            hits = sorted(
                {h.get("chunk_id"): h for h in (hits + new_hits)}.values(),
                key=lambda x: x.get("similarity", 0), reverse=True,
            )
            # Build a follow-up prompt that includes the prior answer +
            # the new excerpts and asks the model to FILL THE GAPS,
            # producing one consolidated answer.
            followup = _build_followup_prompt(
                q, prior_answer=answer, hits=hits,
                queries_used=queries + gap_queries,
                per_query_counts=per_query_counts,
                structured_blocks=structured_blocks,
                rfp_context=rfp_context,
                excerpt_char_budget=64000,
            )
            try:
                answer = _call_ai(followup, _SYSTEM_ANSWER, max_tokens=answer_max_tokens) or answer
            except Exception as e:  # noqa: BLE001
                logger.warning("rfp_qa iter %d follow-up call failed: %s", it, e)
                break
            iteration_trace.append({
                "iter": it,
                "gap_terms": still_missing[:200],
                "new_hits": len(new_hits),
                "answer_chars": len(answer),
            })

    return {
        "proposal_id": proposal_id,
        "question": q,
        "answer": answer.strip(),
        "queries_used": queries,
        "per_query_counts": per_query_counts,
        "citations": hits,
        "iterations": iteration_trace,
        "answered_at": datetime.utcnow().isoformat(),
    }


# ── Self-iteration helpers ──────────────────────────────────────────

# Phrases the LLM uses when it knows its own answer is incomplete or
# unverified. Conservative: only fire on patterns where the model is
# ADMITTING a specific gap or hedge that requires more retrieval.
_GAP_REGEXES = [
    r"\bstill\s+(missing|need(?:ed)?|to\s+(?:find|retrieve|pull|locate))",
    r"\b(?:not|weren'?t|were\s+not)\s+(?:returned|retrieved|pulled|in\s+(?:this|the)\s+retrieval)",
    r"\bI\s+(?:do\s+not|don'?t|haven'?t)\s+have\s+(?:the\s+)?(?:full\s+)?(?:remaining|other|additional)",
    r"\bgap\b[^a-z]{0,5}(?:still\s+missing|what'?s\s+still\s+missing|remaining)",
    r"\b(?:remaining|other|additional)\s+(?:rows?|entries|items?|sections?)\s+(?:were|are)\s+not",
    r"\bwould\s+need\s+to\s+(?:retrieve|pull|search\s+for|fetch|find)",
    r"\bI\s+(?:can(?:not)?|cannot)\s+confirm\s+(?:from|in)\s+(?:this|these|the)\s+(?:retrieval|excerpts?)",
    r"\bnot\s+(?:in\s+)?(?:this|the)\s+retrieval\s+pass",
    r"\bI'?ll?\s+flag\s+that\s+gap",
    r"\b(?:please|let\s+me|recommend)\s+(?:run|pull|do)\s+(?:another|an?\s+additional)\s+(?:search|pass|retrieval)",
    # New patterns observed on real Opus answers that the loop missed:
    # ─────────────────────────────────────────────────────────────────
    # Self-doubt + "verify before locking" hedges
    r"\bI\s+(?:cannot|can\s+not)\s+be\s+100\s*%?\s+confident",
    r"\bnot\s+(?:final|verified|locked)\b",
    r"\b(?:must|should|need(?:s)?\s+to)\s+be\s+(?:verified|confirmed|pulled|retrieved)\s+before",
    r"\bbefore\s+(?:pricing|the\s+table|that)\s+(?:is|can\s+be)\s+(?:locked|finalized)",
    r"\bworth\s+(?:pulling|retrieving|a\s+(?:targeted|follow-?up)\s+(?:retrieval|pull))",
    r"\bworth\s+a\s+follow-?up",
    r"\bif\s+you\s+(?:want|need),?\s+I\s+can\s+(?:run|pull|fetch)\s+(?:targeted|another)",
    r"\bsay\s+so\s+and\s+I\s+(?:will|'?ll)\s+pull",
    r"\bnot\s+in\s+my\s+retrieved\s+excerpts",
    r"\bnot\s+in\s+(?:my|the)\s+retrieval\s+set",
    r"\bnot\s+in\s+(?:this|the)\s+excerpt\s+set",
    r"\bnot\s+(?:fully\s+)?resolved\s+by\s+the\s+excerpts",
    r"\bI\s+inferred\s+it[,;]\s*I\s+did\s+not\s+quote",
    # The literal "Items NOT Found" / "What's Still Missing" headers
    r"^\s*#{1,4}.*(?:not\s+found|still\s+missing|gaps?)\b",
    # "I have not seen … verbatim"
    r"\bI\s+have\s+not\s+seen\s+the\s+full",
    # "you should not treat … as final"
    r"\byou\s+should\s+not\s+treat\b",
    # Truncation tail — explicit signal that the model trailed off
    r"…\s*$|\.\.\.\s*$",
]
_GAP_PATTERN = re.compile("|".join(_GAP_REGEXES), re.IGNORECASE | re.MULTILINE)


# Sentinel for "the answer was probably truncated by max_tokens".
# Looks at the final ~120 chars of the answer; if it doesn't end with a
# clean sentence boundary, list bullet, table row, or markdown
# block-end, we assume Claude ran out of output budget mid-thought.
def _looks_truncated(answer: str) -> bool:
    if not answer:
        return False
    tail = answer.rstrip()
    if not tail:
        return False
    last = tail[-1]
    if last in ".!?\")`":
        return False
    # A complete table row ends with `|`. A complete bullet line ends
    # with a real terminator above. Common truncation tails: `=`, `:`,
    # ` 32`, `**~`, `-`, `–`, etc.
    if last in "|":
        # If the trailing line is a table row (starts with |) and the
        # previous line also looks like a row, treat as complete.
        last_line = tail.splitlines()[-1] if tail.splitlines() else ""
        if last_line.lstrip().startswith("|"):
            return False
    # Heuristic: if the last 30 chars contain a unicode "..." or "…",
    # or look like an unfinished number / equation / "**~", call it
    # truncated.
    suspicious_tail = tail[-30:]
    if any(s in suspicious_tail for s in ("…", "...", "**~", "= ")):
        return True
    # If the very last character is alphanumeric or operator, probably mid-word.
    if last.isalnum() or last in "+-=*/<>(){}[":
        return True
    return False


def _detect_still_missing(answer: str) -> Optional[str]:
    """If the LLM's answer admits a specific gap OR appears truncated,
    return a short snippet of the trailing context that we can use to
    drive a follow-up retrieval. Returns None if no signal.
    """
    if not answer:
        return None
    matches = list(_GAP_PATTERN.finditer(answer))
    truncated = _looks_truncated(answer)
    if not matches and not truncated:
        return None
    pieces: list[str] = []
    for m in matches[:5]:
        start = max(0, m.start() - 50)
        end = min(len(answer), m.end() + 250)
        pieces.append(answer[start:end].strip())
    if truncated:
        # Tail of the answer is the best clue to where the model trailed
        # off — e.g. "Daily average across all operational hours = 32"
        # tells the gap-to-queries step that the next retrieval should
        # focus on daily-average + scenarios.
        pieces.append(
            "TRUNCATED MID-RESPONSE — tail: " + answer.rstrip()[-400:]
        )
    return " | ".join(pieces)[:1800]


_SYSTEM_GAP_TO_QUERIES = (
    "You are a query-extraction assistant for an RFP search agent. The "
    "previous answer admitted a gap (e.g. 'I still need to retrieve the "
    "I-prefix SLA rows from the implementation phase table'). Read the "
    "gap text and produce a JSON array of 3-6 short search-query strings "
    "(2-6 words each) that would retrieve the missing content from RFP "
    "text. NO prose, NO markdown, NO code fences — just the JSON array. "
    "Examples:\n"
    "  Gap: 'I-prefix Implementation SLA rows were not returned'\n"
    "    -> [\"I-1 implementation SLA\", \"Implementation Phase SLA table\", "
    "        \"liquidated damages implementation phase\", "
    "        \"5.8 implementation SLAs\", \"section 4.32 implementation\"]\n"
    "  Gap: 'remaining O-* operational rows missing from retrieval'\n"
    "    -> [\"O-1 operational SLA\", \"Operational Phase liquidated damages\", "
    "        \"5.8 operational SLA table\", \"O-29 O-30 wait time\"]"
)


def _gap_to_queries(gap_text: str) -> list[str]:
    """Use the LLM to convert the gap snippet into concrete search queries.
    Falls back to keyword extraction if the call fails or returns junk.
    """
    if not gap_text:
        return []
    user = f"Gap text from prior answer:\n{gap_text}\n\nReturn 3-6 search queries as a JSON array."
    try:
        raw = _call_ai(user, _SYSTEM_GAP_TO_QUERIES, max_tokens=200) or ""
    except Exception:  # noqa: BLE001
        raw = ""
    # Find the first [ ... ] block.
    m = re.search(r"\[\s*\".*?\"\s*(?:,\s*\".*?\"\s*)*\]", raw, re.DOTALL)
    if m:
        try:
            arr = json.loads(m.group(0))
            queries = [str(s).strip() for s in arr if isinstance(s, str) and s.strip()]
            if queries:
                return queries[:6]
        except json.JSONDecodeError:
            pass
    # Fallback: pull rare-phrase chunks straight out of the gap text.
    return _extract_keyword_phrases(gap_text)[:6]


def _build_followup_prompt(
    question: str,
    prior_answer: str,
    hits: List[Dict[str, Any]],
    queries_used: List[str],
    per_query_counts: Dict[str, int],
    structured_blocks: Optional[List[str]] = None,
    rfp_context: Optional[str] = None,
    excerpt_char_budget: int = 64000,
) -> str:
    """Build the second-pass prompt: prior answer + augmented hit pool +
    explicit instruction to PRODUCE ONE CONSOLIDATED REPLACEMENT answer
    that fills the gaps, NOT a delta.
    """
    bits: List[str] = []
    if rfp_context:
        bits.append(rfp_context)
        bits.append("")
    bits.append(f"ORIGINAL USER QUESTION: {question}")
    bits.append("")
    bits.append("YOUR PRIOR ANSWER (which had gaps):")
    bits.append(prior_answer.strip())
    bits.append("")
    if structured_blocks and any(b.strip() for b in structured_blocks):
        bits.append("APPLICATION DATA (from the proposal tracker):")
        for block in structured_blocks:
            if block.strip():
                bits.append(block)
        bits.append("")
    if hits:
        bits.append(
            f"AUGMENTED RFP EXCERPT POOL ({len(hits)} chunks — your prior "
            f"hits PLUS targeted gap-filling retrieval):"
        )
        used = 0
        for i, h in enumerate(hits, 1):
            section_str = f" §{h['section_id']}" if h.get("section_id") else ""
            tag = f" {h['match_type']}" if h.get("match_type") and h["match_type"] != "vector" else ""
            block = (
                f"\n-- Excerpt {i} (doc={h['document_id']} p={h['page']}"
                f"{section_str} sim={h['similarity']:.2f}{tag}) --\n"
                f"From ACTIVE RFP document: {h.get('document_name','')}\n"
                f"{h.get('snippet') or h.get('content') or ''}\n"
            )
            if used + len(block) > excerpt_char_budget:
                bits.append(f"\n…{len(hits) - i + 1} more excerpts truncated.")
                break
            bits.append(block)
            used += len(block)
    bits.append("")
    bits.append(
        "TASK: Produce ONE consolidated replacement answer that "
        "INCORPORATES everything from your prior answer AND fills the "
        "gaps using the new excerpts above. Do NOT produce a delta or "
        "summary of additions — give me ONE complete answer the user "
        "can read end-to-end. If after reviewing the new excerpts a "
        "specific item is still genuinely not in the corpus, say so "
        "with one short sentence — do NOT promise a future search."
    )
    return "\n".join(bits)
