"""
Question Drafter — generates questions to submit to the procurement agency
during the solicitation's Q&A period.

Three-pass pipeline, modeled on the requirement extractor:
  1. Spotter   — scans a window for ambiguities, conflicts, gaps, undefined terms,
                 missing specs, blank appendix refs, and vague "as directed" clauses.
  2. Strategist — scores each candidate for value (clarification / risk / pricing /
                 scope / competitive / form) and drops nitpicky / low-value ones.
  3. Drafter   — writes the final, neutral-tone question text ready for NJSTART
                 submission, each asking exactly one thing with a precise section cite.

Outputs are persisted as RfpQuestion rows with status="draft".

Public entry points:
  - draft_questions_from_document(db, document_id, proposal_id=None, replace_existing=True)
  - draft_questions_from_window(window, window_idx, total_windows, doc_label)
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from sqlalchemy.orm import Session

from ..models import (
    DocumentChunk, IngestedDocument, RfpQuestion, RfpRequirement,
)
# Reuse the exact window-builder + JSON parser + _call_ai so we benefit from
# the SSL-aware httpx client, DB-configured model, and salvage logic.
from .orchestrator_agent import (
    _build_windows,
    _call_ai,
    _parse_requirements_json as _parse_json_array,
    _load_ai_settings,
)

logger = logging.getLogger(__name__)


ALLOWED_CATEGORIES = {
    "clarification", "risk", "pricing", "scope", "competitive", "form",
}
ALLOWED_PRIORITIES = {"critical", "high", "medium", "low"}


# ── Question-text quality guardrail ─────────────────────────────────────────
# Many LLM drafts still come back as imperatives ("Please confirm ...", "Please
# provide ..."). Rewrite those into interrogative form so the submitted text
# reads as an actual question. This is a best-effort surface rewrite; the
# underlying semantics are preserved.

_IMPERATIVE_REWRITES = [
    # Order matters — more specific prefixes first.
    ("please confirm whether",          "Can the State confirm whether"),
    ("please confirm that",              "Can the State confirm that"),
    ("please confirm",                   "Can the State confirm"),
    ("please clarify whether",           "Can the State clarify whether"),
    ("please clarify",                   "Can the State clarify"),
    ("please define",                    "How does the State define"),
    ("please describe",                  "Can the State describe"),
    ("please explain",                   "Can the State explain"),
    ("please document",                  "Can the State document"),
    ("please enumerate",                 "Can the State enumerate"),
    ("please list",                      "Can the State list"),
    ("please identify",                  "Can the State identify"),
    ("please indicate",                  "Can the State indicate"),
    ("please advise",                    "Can the State advise"),
    ("please disclose",                  "Can the State disclose"),
    ("please specify",                   "What are the specific"),
    ("please state",                     "What does the State consider"),
    ("please provide",                   "Can the State provide"),
    ("please furnish",                   "Can the State furnish"),
    ("please supply",                    "Can the State supply"),
    ("please ",                          "Can the State "),
    # Catch "kindly"/"would you" variants just in case.
    ("kindly confirm",                   "Can the State confirm"),
    ("kindly provide",                   "Can the State provide"),
    ("kindly clarify",                   "Can the State clarify"),
]


def _coerce_to_interrogative(text: str) -> str:
    """Rewrite imperative-phrased question text into interrogative form and
    ensure it ends with a '?'. Idempotent for text that is already a question."""
    if not text:
        return text
    t = text.strip()
    # Split off any leading "Ref. Section X (p.Y):" citation so we only rewrite
    # the body of the question.
    cite = ""
    body = t
    if ":" in t[:120]:
        head, _, rest = t.partition(":")
        if head.lower().startswith(("ref.", "ref ", "reference", "section ", "sec.", "sec ")):
            cite = head.strip() + ": "
            body = rest.strip()

    lower = body.lower()
    for needle, replacement in _IMPERATIVE_REWRITES:
        if lower.startswith(needle):
            # Preserve remainder verbatim (case-sensitive after prefix).
            remainder = body[len(needle):]
            body = replacement + remainder
            break

    # Ensure terminal question mark (and strip any trailing "." first).
    body = body.rstrip()
    if body.endswith("."):
        body = body[:-1].rstrip()
    if not body.endswith("?"):
        body += "?"

    return (cite + body).strip()


def _draft_from_window(window: dict, window_idx: int, total_windows: int, doc_label: str) -> list:
    """Run spotter → strategist → drafter on a single window. Returns a list of
    dict questions with keys: category, priority, source_section, source_page,
    question_text, rationale, source_quote."""
    text = window["text"]
    page_lo = window["start_page"]
    page_hi = window["end_page"]
    loc = f"{doc_label} window {window_idx}/{total_windows} (pages {page_lo}-{page_hi})"

    # ─── Pass 1: Spotter ───────────────────────────────────────────────
    spotter_system = (
        "You are a senior proposal capture analyst with 20+ years of government-RFP "
        "experience. Your job is to read RFP text adversarially and surface every "
        "place where the solicitation is (a) internally inconsistent or self-contradictory, "
        "(b) silently imposes an unreasonable or costly assumption on the bidder, or "
        "(c) materially ambiguous in a way that creates pricing, scope, or compliance "
        "risk. You never invent issues — every candidate must cite exact verbatim text. "
        "You always respond with a JSON array only."
    )
    spotter_prompt = f"""Analyze the RFP text segment below ADVERSARIALLY. Your job is NOT to
summarize what is said — it is to find every flaw a serious bidder would ask about
before committing price. Missing one of these costs our firm real money.

Source text contains inline [PAGE n] markers. Use the most recent [PAGE n] marker
before the quoted sentence as source_page.

--- SOURCE TEXT ({loc}) ---
{text}
--- END SOURCE TEXT ---

FLAG EVERY INSTANCE OF THE FOLLOWING (do not limit yourself to one per category):

A) INCONSISTENCIES / CONTRADICTIONS
   - Two clauses that conflict (dates, quantities, SLAs, scopes, responsibilities).
   - Numbers or specs stated one way in body text and another way in a table/appendix.
   - Terms used with two different meanings in the same document.
   - Cross-references that point to missing or mismatched content (e.g. "see Section 5.2"
     when 5.2 is blank or covers a different topic).
   - Obvious typos in material facts (dates, dollar amounts, section numbers).

B) UNREASONABLE OR COSTLY HIDDEN ASSUMPTIONS
   - Performance obligations with no stated volume, frequency, or peak-load assumption
     (bidder would be forced to guess and either overprice or underbid).
   - SLAs, liquidated damages, or penalty clauses with no defined trigger date or
     measurement method.
   - Open-ended indemnity, uncapped liability, or "as-directed" rework language.
   - Transition or warranty periods with no defined scope boundary.
   - Price-escalation, extension-year, or change-order language that shifts risk
     entirely onto the bidder.
   - Mandatory infrastructure (bandwidth, staffing counts, facilities) implied but
     never quantified.
   - Third-party license, software, or hardware procurement on the bidder's account
     without defined counts, terms, or named products.

C) MATERIAL AMBIGUITY (WEASEL-WORD DETECTION — be aggressive)
   This is the most common defect and the most expensive to miss. Any time the
   RFP uses a vague, subjective, or uncountable term for a deliverable,
   performance obligation, or acceptance criterion, it MUST be flagged.

   Flag every occurrence of these weasel terms when they modify scope, pricing,
   staffing, schedule, performance, or acceptance:
       "as needed", "as directed", "as required", "as requested",
       "reasonable", "reasonably", "sufficient", "appropriate", "adequate",
       "timely", "periodic", "periodically", "routine", "routinely",
       "occasionally", "from time to time", "when required", "as applicable",
       "as appropriate", "as necessary", "at the State's discretion",
       "industry standard", "best practices", "good engineering practice",
       "customary", "typical", "standard", "normal", "ordinary",
       "major", "minor", "significant", "material", "substantial",
       "promptly", "immediately" (without a defined clock), "expeditiously",
       "satisfactory", "to the State's satisfaction", "acceptable",
       "approximately", "about", "up to", "not less than" (without a floor),
       "including but not limited to" (when attached to a scope list),
       "such as" (when listing deliverables), "etc." (in a scope or spec list),
       "and/or" in a scope obligation, "may", "should" (when defining a duty).

   Also flag:
   - Undefined acronyms or terms that drive scope or pricing.
   - Missing numeric specs (volumes, frequencies, uptime %, staffing minimums,
     retention periods, report cadence, response time, resolution time).
   - Repair, maintenance, or capital-work scope described WITHOUT: what
     triggers the work, who decides, how often, what quality standard is met,
     and who pays. THIS IS THE "CAPITAL REPAIRS" PATTERN — it shows up as
     "perform repairs as needed" / "maintain in good condition" / "keep
     equipment in proper working order" — every such phrase must become a
     question asking for the specific trigger, frequency, acceptance
     standard, and cost allocation.
   - Blank appendix/attachment/exhibit references.
   - Form or certification requirements with unclear submission format,
     copies, signature, or notarization expectations.
   - Acceptance criteria that cannot be objectively verified.
   - Pricing-form instructions that are incomplete, self-contradictory, or
     that treat "No Charge" entries ambiguously.
   - Mandatory site visits / conferences / demos without clear logistics.

   Rule of thumb for ambiguity: if two reasonable bidders could read the
   clause and price it differently by more than 5%, it must become a question.

Output a JSON array. Each item MUST have:
  "source_section":    RFP section/subsection (e.g. "4.11", "3.2.1", "Attachment 2")
  "source_page":       integer page from the most recent [PAGE n] marker
  "source_quote":      EXACT verbatim quote of the problematic text (short; 1-2 sentences)
  "defect_type":       "inconsistency" | "costly_assumption" | "ambiguity"
  "issue":             what's unclear / conflicting / costly, in plain English
  "impact":            why this matters to the bid (pricing, scope, DQ risk, etc.)
  "category":          one of: clarification | risk | pricing | scope | competitive | form
  "proposed_question": a one-sentence draft question phrased as a QUESTION (must end with "?")

Rules:
- Output ONLY the JSON array. No preamble, no commentary, no code fences.
- Never invent issues not grounded in the source_quote.
- One issue per item. Split compound issues into separate items.
- Empty array [] if no clarifying questions are warranted for this window.
- Your proposed_question MUST be an interrogative sentence ending in "?". Do not
  phrase it as "Please provide ..." or "Please confirm ..."; phrase it as
  "Can the State ...?", "What is the ...?", "How will ...?", "Will the State ...?", etc.
"""

    logger.info("  [Q win %d/%d] spotter pass starting (%d chars in)", window_idx, total_windows, len(text))
    spotter_response = _call_ai(spotter_prompt, spotter_system, max_tokens=12000)
    logger.info("  [Q win %d/%d] spotter done (%d chars out)", window_idx, total_windows, len(spotter_response or ""))

    # ─── Pass 2: Strategist ────────────────────────────────────────────
    strategist_system = (
        "You are a proposal strategist. You review draft questions and decide which "
        "are worth asking the agency. You drop nitpicky, obvious, or self-evident ones. "
        "You set priority based on DQ risk, pricing impact, and scope uncertainty. "
        "You always respond with a JSON array only."
    )
    strategist_prompt = f"""Review the candidate questions below and decide which are worth submitting.

--- SOURCE TEXT ({loc}) ---
{text}
--- END SOURCE TEXT ---

--- CANDIDATE QUESTIONS ---
{spotter_response}
--- END CANDIDATE QUESTIONS ---

For EACH candidate, decide:
- Keep it? A question is worth keeping ONLY if answering it would materially change
  our bid — pricing, scope, schedule, compliance posture, or competitive position.
  Drop questions whose answer is obvious from context, addressed elsewhere in the
  RFP, or that would not change a single line of our proposal.
- Priority:
    critical  — DQ risk, >$100K pricing uncertainty, or uncapped liability if we guess wrong
    high      — meaningful pricing/scope impact, SLA/LD exposure, or compliance ambiguity
    medium    — clarification that removes rework risk or downstream disputes
    low       — nice-to-have; drop unless the question is very cheap to ask
- Rationale — one internal-only sentence explaining the bid impact.

Output a JSON array of ONLY the kept questions. Each MUST have:
  "source_section", "source_page", "source_quote",
  "defect_type"    (inconsistency | costly_assumption | ambiguity),
  "category"       (clarification | risk | pricing | scope | competitive | form),
  "priority"       (critical | high | medium | low),
  "question_text"  (refined interrogative sentence, must end with "?"),
  "rationale"      (internal-only, why it matters to our bid)

Rules:
- Output ONLY the JSON array. No preamble, no commentary, no code fences.
- Merge duplicates across candidates — keep the stronger wording and highest priority.
- Keep question_text neutral and professional (no accusatory tone), and phrased
  as a QUESTION ending in "?". Prefer "Can the State confirm ...?", "Will the
  State provide ...?", "What is the ...?", "How will the State define ...?"
  over imperative "Please ..." phrasing.
- Each question asks ONE thing. Split compound asks.
"""

    logger.info("  [Q win %d/%d] strategist pass starting", window_idx, total_windows)
    strategist_response = _call_ai(strategist_prompt, strategist_system, max_tokens=10000)
    logger.info("  [Q win %d/%d] strategist done (%d chars out)", window_idx, total_windows, len(strategist_response or ""))

    # ─── Pass 3: Drafter ───────────────────────────────────────────────
    drafter_system = (
        "You are a contracts specialist drafting final questions for NJSTART submission. "
        "Your questions are neutral, specific, and cite the exact section/page. "
        "You always respond with a JSON array only."
    )
    drafter_prompt = f"""Produce the final, submission-ready questions.

--- STRATEGIST OUTPUT ---
{strategist_response}
--- END STRATEGIST OUTPUT ---

For each question, ensure:
- question_text begins with a clear section cite: "Ref. Section X.Y (p.Z): ..."
- The body AFTER the cite is an INTERROGATIVE sentence ending with a question mark.
  Never phrase it as an imperative ("Please provide ...", "Please confirm ...").
- When the defect is AMBIGUITY, the question MUST:
    1. QUOTE the exact ambiguous phrase from the RFP in single quotes.
    2. Ask for the specific MISSING CRITERION — a number, a threshold, a
       frequency, a measurable standard, a dollar amount, a named party,
       or a defined trigger event.
    3. Be answerable with a concrete value or rule, not a narrative.
  Rewrite examples:
    BAD : "Ref. Section 4.4 (p.12): Please confirm the maximum response time."
    GOOD: "Ref. Section 4.4 (p.12): What is the maximum response time in hours
           the State will accept for 'critical incidents', measured from
           ticket-open to on-site arrival?"
    BAD : "Ref. Section 5.1 (p.30): Please provide the minimum staffing levels."
    GOOD: "Ref. Section 5.1 (p.30): What are the minimum on-shift staffing
           counts by role the State requires at each Centralized Inspection
           Facility during posted operating hours?"
    BAD : "Ref. Section 7.2 (p.44): Please clarify capital repairs."
    GOOD: "Ref. Section 7.2 (p.44): Regarding the obligation to perform
           'capital repairs as needed' on State-furnished equipment, what
           dollar threshold separates capital repairs from routine
           maintenance, who authorizes the repair, and will the State
           reimburse parts and labor in excess of that threshold?"
    BAD : "Ref. Section 3.8 (p.22): Please define satisfactory performance."
    GOOD: "Ref. Section 3.8 (p.22): What objective, measurable criteria will
           the State use to determine whether performance is 'satisfactory',
           and what notice and cure period applies before a finding of
           unsatisfactory performance?"
- Neutral, professional tone — never accusatory, never assumes bad drafting.
- Asks ONE specific thing the agency can answer with yes/no, a number, a name,
  a list, a dollar amount, or a definition.
- Under 500 characters total (allow slightly longer for multi-part clarification
  of ambiguous scope).
- If the question depends on a definition or quotes RFP language, embed that
  ambiguous phrase in single quotes inside the question.

Output a JSON array. Each item MUST have:
  "source_section", "source_page", "source_quote",
  "defect_type"    (inconsistency | costly_assumption | ambiguity),
  "category"       (clarification | risk | pricing | scope | competitive | form),
  "priority"       (critical | high | medium | low),
  "question_text"  (final submission-ready text, MUST end with "?"),
  "rationale"      (internal-only)

Rules:
- Output ONLY the JSON array. No preamble, no commentary, no code fences.
- Do not drop items from the strategist output; only refine wording.
- Every question_text MUST end with "?". If a strategist item does not, rewrite it.
"""

    logger.info("  [Q win %d/%d] drafter pass starting", window_idx, total_windows)
    drafter_response = _call_ai(drafter_prompt, drafter_system, max_tokens=12000)
    logger.info("  [Q win %d/%d] drafter done (%d chars out)", window_idx, total_windows, len(drafter_response or ""))

    questions = _parse_json_array(drafter_response)
    # Attach window page bounds so the storer has a fallback if source_page missing.
    for q in questions:
        q.setdefault("_window_page_lo", page_lo)
        q.setdefault("_window_page_hi", page_hi)
    return questions


def draft_questions_from_window(window: dict, window_idx: int, total_windows: int, doc_label: str) -> list:
    """Public wrapper around the triple-pass pipeline for a single window."""
    try:
        return _draft_from_window(window, window_idx, total_windows, doc_label)
    except Exception as e:
        logger.error(f"Question drafting failed on window {window_idx}/{total_windows}: {e}")
        return []


def _store_questions(
    db: Session,
    questions: List[dict],
    document_id: int,
    proposal_id: Optional[int],
) -> int:
    """Persist AI-drafted questions. Returns count saved."""
    saved = 0
    for q in questions:
        category = (q.get("category") or "clarification").lower().strip()
        if category not in ALLOWED_CATEGORIES:
            category = "clarification"
        priority = (q.get("priority") or "medium").lower().strip()
        if priority not in ALLOWED_PRIORITIES:
            priority = "medium"

        question_text = (q.get("question_text") or "").strip()
        if not question_text:
            continue  # skip empties

        # Normalize to interrogative form (fixes "Please ..." imperatives).
        question_text = _coerce_to_interrogative(question_text)

        src_page = q.get("source_page")
        if not isinstance(src_page, int):
            src_page = q.get("_window_page_lo")

        row = RfpQuestion(
            document_id=document_id,
            proposal_id=proposal_id,
            source_section=(q.get("source_section") or None),
            source_page=src_page,
            category=category,
            priority=priority,
            question_text=question_text[:4000],
            rationale=(q.get("rationale") or None),
            source_quote=(q.get("source_quote") or None),
            related_requirement_ids=None,  # linked in a follow-up pass
            status="draft",
            created_by_ai=True,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(row)
        saved += 1
    db.commit()
    return saved


# ── Checkpoint / resume ────────────────────────────────────────────────────
# State is kept in a small JSON file under logs/.qdraft/ so an interrupted run
# (network outage, reboot, Ctrl-C) can pick up where it left off without redoing
# completed windows or producing duplicates.

def _checkpoint_dir() -> Path:
    # Env-overridable via EVEREST_LOGS_DIR; default <workspace>/logs/.qdraft.
    from .. import paths as _paths
    return _paths.qdraft_dir()


def _checkpoint_path(document_id: int, proposal_id: Optional[int]) -> Path:
    key = f"doc{document_id}_prop{proposal_id if proposal_id is not None else 'none'}.json"
    return _checkpoint_dir() / key


def _load_checkpoint(document_id: int, proposal_id: Optional[int]) -> dict:
    p = _checkpoint_path(document_id, proposal_id)
    if not p.exists():
        return {"document_id": document_id, "proposal_id": proposal_id,
                "completed_windows": [], "saved_total": 0, "total_windows": None}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Checkpoint unreadable (%s); starting fresh.", e)
        return {"document_id": document_id, "proposal_id": proposal_id,
                "completed_windows": [], "saved_total": 0, "total_windows": None}


def _save_checkpoint(state: dict) -> None:
    p = _checkpoint_path(state["document_id"], state.get("proposal_id"))
    tmp = p.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, p)  # atomic on Windows + POSIX
    except OSError as e:
        logger.warning("Checkpoint write failed: %s", e)


def _clear_checkpoint(document_id: int, proposal_id: Optional[int]) -> None:
    p = _checkpoint_path(document_id, proposal_id)
    try:
        if p.exists():
            p.unlink()
    except OSError:
        pass


def draft_questions_from_document(
    db: Session,
    document_id: int,
    proposal_id: Optional[int] = None,
    replace_existing: bool = True,
    resume: bool = False,
) -> dict:
    """
    Window-by-window question drafting for a single document. Commits per window
    so a crash at window N only loses that window.

    When resume=True, any existing checkpoint for (document_id, proposal_id) is
    honored: completed windows are skipped, existing draft rows are preserved,
    and the run picks up where it left off. `replace_existing` is forced False
    in resume mode to avoid wiping prior partial results.
    """
    doc = db.query(IngestedDocument).filter_by(id=document_id).first()
    if not doc:
        raise ValueError(f"Document {document_id} not found")

    doc_label = doc.filename or f"doc#{document_id}"
    cfg = _load_ai_settings()
    logger.info(
        "Drafting questions for doc %s (provider=%s, refiner_model=%r, resume=%s)",
        doc_label, cfg.get("llm_provider"), cfg.get("refiner_model"), resume,
    )

    # If not explicitly passed, take proposal_id from the document row.
    if proposal_id is None:
        proposal_id = doc.proposal_id

    state = _load_checkpoint(document_id, proposal_id) if resume else {
        "document_id": document_id, "proposal_id": proposal_id,
        "completed_windows": [], "saved_total": 0, "total_windows": None,
    }
    completed = set(state.get("completed_windows") or [])

    # In resume mode, NEVER wipe prior rows — the earlier run already saved them.
    effective_replace = replace_existing and not resume
    if effective_replace:
        q = db.query(RfpQuestion).filter_by(document_id=document_id, created_by_ai=True)
        if proposal_id is not None:
            q = q.filter_by(proposal_id=proposal_id)
        deleted = q.filter_by(status="draft").delete(synchronize_session=False)
        # Keep approved/submitted rows untouched — only wipe AI drafts.
        db.commit()
        logger.info("Cleared %d prior AI-drafted questions (status=draft) for doc %s", deleted, doc_label)
        # Fresh start — nuke any stale checkpoint
        _clear_checkpoint(document_id, proposal_id)
        completed = set()
        state["completed_windows"] = []
        state["saved_total"] = 0

    chunks = (
        db.query(DocumentChunk)
        .filter_by(document_id=document_id)
        .order_by(DocumentChunk.page_number, DocumentChunk.chunk_index)
        .all()
    )
    windows = _build_windows(chunks)
    total = len(windows)
    state["total_windows"] = total
    logger.info(
        "Built %d windows for Q-drafting on doc %s (%d already done, %d to go)",
        total, doc_label, len(completed), total - len(completed),
    )

    saved_total = int(state.get("saved_total") or 0)

    # Build the todo list — only windows not yet completed.
    todo = [(i, w) for i, w in enumerate(windows, start=1) if i not in completed]

    # Parallelism knob. Default 1 = sequential (today's behavior).
    # Set QDRAFT_PARALLEL=4 to run 4 windows concurrently. The LLM calls are
    # I/O-bound so threads are appropriate; the SQLAlchemy session is NOT
    # shared — only the main thread touches `db`. Workers return dicts.
    try:
        max_workers = max(1, int(os.environ.get("QDRAFT_PARALLEL", "1")))
    except ValueError:
        max_workers = 1

    if max_workers <= 1 or len(todo) <= 1:
        # Sequential path (unchanged)
        for i, w in todo:
            t0 = datetime.utcnow()
            try:
                draft = draft_questions_from_window(w, i, total, doc_label)
                n = _store_questions(db, draft, document_id, proposal_id)
                saved_total += n
                completed.add(i)
                state["completed_windows"] = sorted(completed)
                state["saved_total"] = saved_total
                _save_checkpoint(state)
                dt = (datetime.utcnow() - t0).total_seconds()
                logger.info(
                    "[Q win %2d/%d] pages=%s-%s chars=%s raw=%3d saved=%3d total=%3d t=%5.1fs",
                    i, total, w["start_page"], w["end_page"],
                    f"{len(w['text']):,}", len(draft), n, saved_total, dt,
                )
            except Exception as e:
                logger.error(f"[Q win {i}/{total}] failed: {e}")
    else:
        # Parallel path. Workers call the LLM (3 passes) with no DB access;
        # the main thread stores + checkpoints as each window returns.
        from concurrent.futures import ThreadPoolExecutor, as_completed
        logger.info(
            "[Q] launching ThreadPoolExecutor(max_workers=%d) across %d windows",
            max_workers, len(todo),
        )
        win_start_times: dict[int, datetime] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {}
            for i, w in todo:
                win_start_times[i] = datetime.utcnow()
                fut = pool.submit(draft_questions_from_window, w, i, total, doc_label)
                futures[fut] = (i, w)

            for fut in as_completed(futures):
                i, w = futures[fut]
                t0 = win_start_times.get(i, datetime.utcnow())
                try:
                    draft = fut.result()
                except Exception as e:
                    logger.error(f"[Q win {i}/{total}] worker failed: {e}")
                    continue
                try:
                    n = _store_questions(db, draft, document_id, proposal_id)
                except Exception as e:
                    logger.error(f"[Q win {i}/{total}] store failed: {e}")
                    # roll back and move on; window is NOT marked completed
                    try:
                        db.rollback()
                    except Exception:
                        pass
                    continue
                saved_total += n
                completed.add(i)
                state["completed_windows"] = sorted(completed)
                state["saved_total"] = saved_total
                _save_checkpoint(state)
                dt = (datetime.utcnow() - t0).total_seconds()
                logger.info(
                    "[Q win %2d/%d] pages=%s-%s chars=%s raw=%3d saved=%3d total=%3d t=%5.1fs (parallel)",
                    i, total, w["start_page"], w["end_page"],
                    f"{len(w['text']):,}", len(draft), n, saved_total, dt,
                )

    # Only clear checkpoint if ALL windows finished (completed set == full range)
    if len(completed) >= total:
        _clear_checkpoint(document_id, proposal_id)
        logger.info("All %d windows complete; checkpoint cleared.", total)
    else:
        logger.info(
            "Run ended with %d/%d windows complete; checkpoint preserved at %s",
            len(completed), total, _checkpoint_path(document_id, proposal_id),
        )

    return {
        "document_id": document_id,
        "proposal_id": proposal_id,
        "windows_processed": total,
        "windows_completed_this_run": total - (total - len(completed)),
        "windows_completed_total": len(completed),
        "questions_saved": saved_total,
    }
