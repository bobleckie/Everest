"""
Multi-Agent Orchestrator — manages persona interactions, consensus loops,
and task delegation for the RFP response pipeline.

Key design:
- Personas interact with each other through the orchestrator
- Max 3 consensus loops before the orchestrator decides
- All conversations are logged for traceability
"""
import json
import logging
from datetime import datetime
from typing import List, Optional, Dict

from sqlalchemy.orm import Session

from ..models import (
    AgentConversation, AgentMessage, Persona, RfpRequirement,
    DocumentChunk, IngestedDocument, ExtractedFact,
)
from .persona_factory import get_persona_by_type

logger = logging.getLogger(__name__)

# Default models (overridable via Settings → AI / Models → Refiner Model,
# or env vars CLAUDE_REFINER_MODEL / OPENAI_REFINER_MODEL).
import os as _os
DEFAULT_CLAUDE_MODEL = _os.getenv("CLAUDE_REFINER_MODEL", "claude-opus-4-7")
DEFAULT_OPENAI_MODEL = _os.getenv("OPENAI_REFINER_MODEL", "gpt-4o")


def _load_ai_settings() -> Dict[str, str]:
    """Load AI settings from the app_settings DB table, falling back to env vars."""
    import os
    from ..database import SessionLocal
    from ..models import AppSetting

    result = {
        "llm_provider": os.getenv("LLM_PROVIDER", "openai"),
        "openai_api_key": os.getenv("OPENAI_API_KEY", ""),
        "anthropic_api_key": os.getenv("ANTHROPIC_API_KEY", ""),
        "refiner_model": os.getenv("REFINER_MODEL", ""),
    }
    try:
        s = SessionLocal()
        try:
            for row in s.query(AppSetting).all():
                try:
                    val = json.loads(row.value_json) if row.value_json else ""
                except (json.JSONDecodeError, TypeError):
                    val = row.value_json or ""
                if row.key in result and val:
                    result[row.key] = val
        finally:
            s.close()
    except Exception as e:
        logger.debug(f"Could not load AI settings from DB: {e}")
    return result


def _is_transient_error(exc: Exception) -> bool:
    """Classify whether an LLM-provider error is worth retrying.

    Transient: network blips, timeouts, 5xx, 429 rate-limits, 529 overloaded.
    Non-transient: auth (401/403), bad-request (400), schema issues.
    """
    name = type(exc).__name__
    if name in {
        # httpx / network / generic I/O
        "ConnectError", "ConnectTimeout", "ReadTimeout", "WriteTimeout",
        "PoolTimeout", "RemoteProtocolError", "ReadError", "WriteError",
        "NetworkError", "TimeoutError", "ConnectionError", "ConnectionResetError",
        "ConnectionAbortedError",
        # provider SDK wrappers for transient conditions
        "APIConnectionError", "APITimeoutError",
        "RateLimitError", "ServiceUnavailableError", "InternalServerError",
        "APIError",  # generic; status checked below
    }:
        # For generic APIError, inspect status_code if present
        status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
        if name == "APIError" and status is not None:
            return int(status) >= 500 or int(status) in (408, 429, 529)
        return True
    # HTTP status codes surfaced on any exception (anthropic.APIStatusError etc.)
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status is not None:
        try:
            s = int(status)
            return s >= 500 or s in (408, 429, 529)
        except (TypeError, ValueError):
            pass
    return False


def _call_ai(prompt: str, system: str, max_tokens: int = 3000) -> str:
    """Call AI using DB-configured provider/key, with env var fallback.

    Each provider attempt is wrapped in a retry loop with exponential backoff
    for transient errors (network drops, timeouts, 5xx, 429, 529). Non-transient
    errors fall through to the next provider immediately.

    Retry tuning via env vars (read fresh each call so an operator can bump
    aggressiveness without restarting):
      AI_RETRY_MAX_ATTEMPTS  default 6   (retries per provider attempt)
      AI_RETRY_BASE_DELAY_S  default 2.0 (initial backoff)
      AI_RETRY_MAX_DELAY_S   default 120.0 (cap per-sleep)
    """
    import time as _time
    import random as _random
    import os as _os_retry

    cfg = _load_ai_settings()
    provider = (cfg.get("llm_provider") or "openai").lower()
    openai_key = cfg.get("openai_api_key") or ""
    anthropic_key = cfg.get("anthropic_api_key") or ""
    refiner_model = cfg.get("refiner_model") or ""

    try:
        _max_attempts = int(_os_retry.getenv("AI_RETRY_MAX_ATTEMPTS", "6"))
    except ValueError:
        _max_attempts = 6
    try:
        _base_delay = float(_os_retry.getenv("AI_RETRY_BASE_DELAY_S", "2.0"))
    except ValueError:
        _base_delay = 2.0
    try:
        _max_delay = float(_os_retry.getenv("AI_RETRY_MAX_DELAY_S", "120.0"))
    except ValueError:
        _max_delay = 120.0

    # Build ordered list of providers to try, preferred first
    order = []
    if provider == "anthropic":
        if anthropic_key:
            order.append("anthropic")
        if openai_key:
            order.append("openai")
    else:
        if openai_key:
            order.append("openai")
        if anthropic_key:
            order.append("anthropic")

    from .ssl_utils import make_sync_httpx_client

    def _attempt_anthropic() -> Optional[str]:
        import anthropic  # noqa: F401
        from . import llm_usage as _usage
        client = anthropic.Anthropic(
            api_key=anthropic_key,
            http_client=make_sync_httpx_client(timeout=600.0),
        )
        model = refiner_model if refiner_model.startswith("claude") else DEFAULT_CLAUDE_MODEL
        _t0 = _time.time()
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        # Best-effort usage record (never raises).
        in_tok, out_tok = _usage.extract_anthropic_usage(resp)
        _usage.record(
            provider="anthropic", model=model,
            input_tokens=in_tok, output_tokens=out_tok,
            latency_ms=_usage.time_block_ms(_t0),
        )
        blocks = getattr(resp, "content", None) or []
        text_parts = [t for b in blocks if (t := getattr(b, "text", None))]
        if text_parts:
            return "".join(text_parts)
        stop_reason = getattr(resp, "stop_reason", None)
        logger.warning(
            f"Anthropic returned empty content (stop_reason={stop_reason}); "
            f"falling through to next provider."
        )
        return None  # empty → fall through (not retryable; provider answered)

    def _attempt_openai() -> Optional[str]:
        import openai  # noqa: F401
        from . import llm_usage as _usage
        client = openai.OpenAI(
            api_key=openai_key,
            http_client=make_sync_httpx_client(timeout=600.0),
        )
        model = refiner_model if refiner_model and not refiner_model.startswith("claude") else DEFAULT_OPENAI_MODEL
        _t0 = _time.time()
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
        )
        in_tok, out_tok = _usage.extract_openai_usage(resp)
        _usage.record(
            provider="openai", model=model,
            input_tokens=in_tok, output_tokens=out_tok,
            latency_ms=_usage.time_block_ms(_t0),
        )
        return resp.choices[0].message.content

    for p in order:
        fn = _attempt_anthropic if p == "anthropic" else _attempt_openai
        for attempt in range(1, _max_attempts + 1):
            try:
                result = fn()
                if result is not None:
                    return result
                # None = provider answered but output was empty; break inner
                # retry loop and let the OUTER provider loop try the next one.
                break
            except Exception as e:
                if _is_transient_error(e) and attempt < _max_attempts:
                    delay = min(_base_delay * (2 ** (attempt - 1)), _max_delay)
                    # Jitter: ±25%
                    delay = delay * (0.75 + _random.random() * 0.5)
                    logger.warning(
                        "%s call transient error (attempt %d/%d): %s: %s — retry in %.1fs",
                        p.capitalize(), attempt, _max_attempts, type(e).__name__, e, delay,
                    )
                    _time.sleep(delay)
                    continue
                # Non-transient, or final attempt: log and fall through to next provider
                logger.warning(
                    "%s call failed (attempt %d/%d, giving up on this provider): %s: %s",
                    p.capitalize(), attempt, _max_attempts, type(e).__name__, e,
                )
                break

    logger.error(
        "All AI providers exhausted. Check Settings → AI Configuration "
        "to verify API keys. Providers tried: %s", order or "(none configured)",
    )
    raise RuntimeError(
        "No AI provider is configured or reachable. "
        "Check Settings → AI Configuration to set an API key."
    )


# ── Conversation management ──────────────────────────────────────────

def start_conversation(
    db: Session,
    conversation_type: str,
    proposal_id: Optional[int] = None,
    section_id: Optional[str] = None,
    max_loops: int = 3,
) -> AgentConversation:
    """Start a new multi-agent conversation."""
    conv = AgentConversation(
        conversation_type=conversation_type,
        proposal_id=proposal_id,
        section_id=section_id,
        max_loops=max_loops,
        status="active",
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def add_message(
    db: Session,
    conversation_id: int,
    persona_name: str,
    persona_type: str,
    message_type: str,
    content: str,
    loop_number: int = 0,
) -> AgentMessage:
    """Add a message to a conversation."""
    msg = AgentMessage(
        conversation_id=conversation_id,
        persona_name=persona_name,
        persona_type=persona_type,
        message_type=message_type,
        content=content,
        loop_number=loop_number,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def get_conversation_history(db: Session, conversation_id: int) -> List[dict]:
    """Get all messages in a conversation."""
    msgs = (
        db.query(AgentMessage)
        .filter(AgentMessage.conversation_id == conversation_id)
        .order_by(AgentMessage.created_at)
        .all()
    )
    return [
        {
            "id": m.id,
            "persona_name": m.persona_name,
            "persona_type": m.persona_type,
            "message_type": m.message_type,
            "content": m.content,
            "loop_number": m.loop_number,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in msgs
    ]


# ── RFP Extraction Pipeline (triple-checked) ────────────────────────

# ── Windowed, triple-checked extraction ─────────────────────────────

# Target chars per analysis window. Claude 3.5/4 Sonnet supports ~200K tokens,
# but keeping each window modest gives the model room for the analyst +
# validator + reconciliation output without truncation and forces it to be
# thorough on each window. A 122-page PDF becomes ~25 windows at 22K chars.
WINDOW_CHAR_TARGET = 22000
WINDOW_CHAR_MAX = 28000  # never exceed this per window

# Minimum chars for a document to be treated as "real text" worth analyzing.
# Anything under this is treated as a pass-through (skipped).
MIN_DOC_CHARS = 200


def _build_windows(chunks) -> List[dict]:
    """
    Group ordered DocumentChunks into analysis windows of ~WINDOW_CHAR_TARGET
    chars. Each window carries start_page / end_page / text for page-accurate
    citation later. The text contains inline ``[PAGE n]`` markers on page
    transitions so the LLM can emit the actual page number with each item.
    """
    windows = []
    buf = []           # pieces already joined with "\n\n"
    buf_chars = 0
    start_page = None
    end_page = None
    last_marker_page = None

    def flush():
        nonlocal buf, buf_chars, start_page, end_page, last_marker_page
        if not buf:
            return
        windows.append({
            "start_page": start_page or 1,
            "end_page": end_page or (start_page or 1),
            "text": "\n\n".join(buf),
            "char_count": buf_chars,
        })
        buf = []
        buf_chars = 0
        start_page = None
        end_page = None
        last_marker_page = None

    for c in chunks:
        txt = c.content or ""
        if not txt.strip():
            continue
        pg = c.page_number or (end_page or 1)
        if start_page is None:
            start_page = pg
        end_page = pg

        # If adding this chunk would exceed the hard max, flush first.
        if buf_chars + len(txt) > WINDOW_CHAR_MAX and buf_chars > 0:
            flush()
            start_page = pg
            end_page = pg

        # Emit a [PAGE n] marker on page transitions (or at window start).
        if pg != last_marker_page:
            marker = f"[PAGE {pg}]"
            buf.append(marker)
            buf_chars += len(marker) + 2  # account for the "\n\n" join
            last_marker_page = pg

        buf.append(txt)
        buf_chars += len(txt)

        # If we've crossed the soft target, flush at chunk boundary.
        if buf_chars >= WINDOW_CHAR_TARGET:
            flush()

    flush()
    return windows


def _norm_title(s: str) -> str:
    """Normalized key for dedupe across windows."""
    import re as _re
    if not s:
        return ""
    t = _re.sub(r"\s+", " ", s.lower()).strip()
    # collapse punctuation noise
    t = _re.sub(r"[^a-z0-9 ]+", "", t)
    return t[:120]


def _extract_from_window(window: dict, window_idx: int, total_windows: int, doc_label: str) -> list:
    """
    Run the triple-check (analyst → validator → orchestrator) on a single
    window of text. Returns a list of dict requirements. Tolerates failures
    by returning [] so the whole document doesn't abort.
    """
    text = window["text"]
    page_lo = window["start_page"]
    page_hi = window["end_page"]
    loc = f"{doc_label} window {window_idx}/{total_windows} (pages {page_lo}-{page_hi})"

    analyst_system = (
        "You are a senior RFP analyst. You extract EVERY explicit and implied requirement "
        "from government solicitations. You never summarize, never paraphrase mandatory "
        "language, and always quote the exact source text. Treat every 'shall', 'must', "
        "'will', 'is required to', 'no later than', and 'at a minimum' as a requirement. "
        "Treat every form, certification, signature, attestation, bond, and insurance "
        "requirement as a separate item. Treat each due date as a requirement."
    )
    analyst_prompt = f"""Analyze the following RFP text segment. Extract EVERY requirement.

The source text contains inline [PAGE n] markers on each page transition. Use
the most recent [PAGE n] marker before the quoted sentence as the requirement's
source_page.

--- SOURCE TEXT ({loc}) ---
{text}
--- END SOURCE TEXT ---

Output a JSON array. Each item MUST have:
  "req_id":        locally unique string, e.g. "A-001"
  "category":      one of mandatory | scored | informational | certification | form | signature | deadline
  "section_id":    the RFP section/subsection this maps to (e.g. "1.4", "3.2.1", "Attachment 1")
  "title":         short, specific, imperative (e.g. "Submit Ownership Disclosure Form")
  "description":   what the bidder must do, in the bidder's own words
  "source_text":   the EXACT verbatim quote from the source (keep it short — just the key sentence)
  "source_page":   integer page number (from the most recent [PAGE n] marker above the quote)
  "priority":      critical | high | medium | low

Rules:
- Output ONLY the JSON array. No preamble, no commentary, no code fences.
- Never invent requirements not in the text.
- If a single sentence contains multiple obligations, split them.
- Keep "source_text" verbatim; do not "clean up" quoted text.
- Do NOT include the [PAGE n] marker inside source_text.
"""

    # Accuracy note: max_tokens raised from 8000 → 16000 for the analyst because
    # a dense RFP window easily produces 10K+ chars of JSON (~20-40 requirements),
    # and hitting the output cap silently truncates the JSON array mid-item.
    # Truncation loss is the #1 cause of missed mandatory requirements.
    logger.info("  [win %d/%d] analyst pass starting (%d chars in)", window_idx, total_windows, len(text))
    analyst_response = _call_ai(analyst_prompt, analyst_system, max_tokens=16000)
    logger.info("  [win %d/%d] analyst done (%d chars out)", window_idx, total_windows, len(analyst_response or ""))

    validator_system = (
        "You are a compliance validator. You triple-check RFP extractions for omissions. "
        "You always answer with a JSON array only."
    )
    validator_prompt = f"""Another analyst produced the extraction below. Review the SOURCE TEXT independently and find anything MISSED or MISCATEGORIZED.

--- SOURCE TEXT ({loc}) ---
{text}
--- END SOURCE TEXT ---

--- ANALYST EXTRACTION ---
{analyst_response}
--- END ANALYST EXTRACTION ---

Output a JSON array of ONLY the additional or corrected requirements (do NOT repeat items the analyst already captured correctly). Same schema as before:
  req_id, category, section_id, title, description, source_text, priority

Pay extra attention to:
- Signature blocks, attestations, certifications, bonds, insurance minimums
- Appendices/exhibits/attachments referenced in passing
- Implied requirements (e.g. "as required" + another section reference)
- NJ-specific statutory refs (N.J.S.A., N.J.A.C., P.L. citations)
- Subcontractor disclosures, Business Registration Certificate, MacBride Principles,
  Ownership Disclosure, Source Disclosure, EEO/Affirmative Action, Americans with
  Disabilities, Iran/Russia/Belarus disinvestment, Standard Terms and Conditions

Output ONLY the JSON array. Empty array [] if the analyst caught everything.
"""

    logger.info("  [win %d/%d] validator pass starting", window_idx, total_windows)
    validator_response = _call_ai(validator_prompt, validator_system, max_tokens=12000)
    logger.info("  [win %d/%d] validator done (%d chars out)", window_idx, total_windows, len(validator_response or ""))

    orch_system = (
        "You are the orchestrator. You merge two requirement extractions into a single, "
        "definitive, de-duplicated list. You always answer with a JSON array only."
    )
    orch_prompt = f"""You are merging an analyst extraction with a validator's supplemental findings.

IMPORTANT context about the inputs:
- ANALYST produced the INITIAL, FULL extraction for this window.
- VALIDATOR was asked to ONLY report ADDITIONS or CORRECTIONS — i.e. items the analyst
  missed, or items where the category/section was wrong. Items absent from the validator's
  output were implicitly CONFIRMED by the validator (the validator saw the analyst's work
  and did not flag them).

Produce one de-duplicated JSON array for this source window that includes EVERY
requirement from the analyst plus EVERY addition/correction from the validator.

For each item, set:
  "agreement":  "both"           if the analyst and validator both reported the item
                                  (look for near-duplicate source_text/title).
                "analyst_confirmed" if only the analyst had it and the validator did not
                                  explicitly challenge it (treat as confirmed by silence).
                "validator_only"   if only the validator reported it (new addition).
  "confidence": "high"    if agreement is "both" or "analyst_confirmed".
                "medium"  if the validator rewrote/corrected an analyst item.
                "low"     if the item seems ambiguous or the source_text is indirect.

If the analyst and validator disagree on category or section, make a decision and put the
reasoning in a "notes" field on that item.

Favor the most verbatim, specific source_text across the two inputs.

--- ANALYST (initial full extraction) ---
{analyst_response}
--- VALIDATOR (additions/corrections only) ---
{validator_response}

Output ONLY the final JSON array.
"""

    logger.info("  [win %d/%d] reconciler pass starting", window_idx, total_windows)
    orch_response = _call_ai(orch_prompt, orch_system, max_tokens=25000)
    logger.info("  [win %d/%d] reconciler done (%d chars out)", window_idx, total_windows, len(orch_response or ""))

    reqs = _parse_requirements_json(orch_response)
    # Attach page bounds so the storer can set source_page.
    for r in reqs:
        r.setdefault("_window_page_lo", page_lo)
        r.setdefault("_window_page_hi", page_hi)
    return reqs


def _parse_requirements_json(response_text: str) -> list:
    """Extract a list of requirement dicts from a model response. Uses fence
    stripping → greedy array parse → trailing-comma fix → brace-walk salvage."""
    import re as _re

    if not response_text:
        return []
    cleaned = response_text.strip()

    # Drop leading ```json fence / trailing ``` fence
    fence = _re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned)
    if fence:
        cleaned = fence.group(1).strip()

    first = cleaned.find("[")
    last = cleaned.rfind("]")
    if first != -1 and last != -1 and last > first:
        candidate = cleaned[first : last + 1]
        for attempt in (candidate, _re.sub(r",(\s*[}\]])", r"\1", candidate)):
            try:
                parsed = json.loads(attempt)
                if isinstance(parsed, list):
                    return [x for x in parsed if isinstance(x, dict)]
            except json.JSONDecodeError:
                continue

    salvaged = _salvage_json_objects(cleaned)
    if salvaged:
        logger.warning(
            "Requirements JSON did not parse cleanly; salvaged %d objects via brace walk.",
            len(salvaged),
        )
        return salvaged
    return []


def extract_rfp_requirements(
    db: Session,
    document_id: int,
    proposal_id: Optional[int] = None,
    replace_existing: bool = True,
) -> dict:
    """
    Windowed, triple-checked requirement extraction for a document.

    For each ~22K-char text window:
      1. RFP Analyst does first pass
      2. Compliance Validator does independent second pass (on same window)
      3. Orchestrator reconciles and emits final JSON for the window
    Per-window results are merged into the document-level set, de-duped by
    (section_id, normalized_title), and persisted with page-bounded citations.

    Parameters
    ----------
    replace_existing : if True, delete any existing rfp_requirements rows for
        this (proposal_id, document_id) pair before inserting new ones so
        re-running the pipeline is idempotent.
    """
    doc = db.query(IngestedDocument).filter(IngestedDocument.id == document_id).first()
    if not doc:
        return {"error": "Document not found"}

    if proposal_id is None:
        proposal_id = doc.proposal_id

    # Preflight: require AI provider.
    cfg = _load_ai_settings()
    if not (cfg.get("openai_api_key") or cfg.get("anthropic_api_key")):
        return {
            "error": (
                "No AI provider configured. Open Settings → AI / Models and "
                "paste an OpenAI or Anthropic API key, then save before extracting."
            ),
            "requirements_extracted": 0,
        }

    chunks = (
        db.query(DocumentChunk)
        .filter(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.chunk_index)
        .all()
    )
    if not chunks:
        return {"error": "Document has no chunks; run ingestion first.", "requirements_extracted": 0}

    total_chars = sum((c.char_count or len(c.content or "")) for c in chunks)
    if total_chars < MIN_DOC_CHARS:
        return {
            "requirements_extracted": 0,
            "reason": f"Document too short ({total_chars} chars); skipped.",
            "document_id": document_id,
        }

    windows = _build_windows(chunks)
    doc_label = doc.original_filename or f"doc_{document_id}"
    logger.info(
        "Extracting requirements from '%s' (%d chars, %d windows)",
        doc_label, total_chars, len(windows),
    )

    # Clear stale rows for this doc (scoped to the proposal if we have one).
    if replace_existing:
        q = db.query(RfpRequirement).filter(RfpRequirement.document_id == document_id)
        if proposal_id is not None:
            q = q.filter(RfpRequirement.proposal_id == proposal_id)
        deleted = q.delete(synchronize_session=False)
        if deleted:
            logger.info("Cleared %d prior requirements for doc %d.", deleted, document_id)
        db.commit()

    # Track seen keys across windows for dedupe.
    seen_keys: set = set()
    all_reqs: list = []
    window_summaries: list = []

    conv = start_conversation(db, "rfp_extraction", proposal_id=proposal_id)

    for idx, win in enumerate(windows, start=1):
        try:
            win_reqs = _extract_from_window(win, idx, len(windows), doc_label)
        except Exception as e:  # never abort the whole doc on a single window
            logger.exception("Window %d failed: %s", idx, e)
            win_reqs = []

        kept = 0
        for r in win_reqs:
            if not isinstance(r, dict):
                continue
            title = r.get("title") or r.get("description") or ""
            section = r.get("section_id") or ""
            key = (section.strip().lower(), _norm_title(title))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            all_reqs.append(r)
            kept += 1

        window_summaries.append({
            "window": idx,
            "pages": f"{win['start_page']}-{win['end_page']}",
            "chars": win["char_count"],
            "raw_candidates": len(win_reqs),
            "kept_after_dedupe": kept,
        })
        add_message(
            db, conv.id,
            "Orchestrator", "orchestrator", "window_result",
            json.dumps({
                "window": idx,
                "pages": f"{win['start_page']}-{win['end_page']}",
                "raw": len(win_reqs),
                "kept": kept,
            }),
            loop_number=idx,
        )

    # Persist the merged set.
    stored = _store_requirements(
        db,
        document_id=document_id,
        proposal_id=proposal_id,
        reqs=all_reqs,
    )

    conv.status = "completed"
    conv.summary = (
        f"Extracted {stored} requirements from {len(windows)} windows of "
        f"'{doc_label}' ({total_chars:,} chars)."
    )
    db.commit()

    return {
        "conversation_id": conv.id,
        "document_id": document_id,
        "proposal_id": proposal_id,
        "total_chars": total_chars,
        "windows": len(windows),
        "window_summaries": window_summaries,
        "requirements_extracted": stored,
        "passes": ["rfp_analyst", "compliance_validator", "orchestrator_reconciliation"],
    }


def _store_requirements(
    db: Session,
    document_id: int,
    proposal_id: Optional[int],
    reqs: list,
) -> int:
    """Persist merged requirement dicts with proposal/page/audit metadata."""
    if not reqs:
        return 0

    count = 0
    for i, req in enumerate(reqs, start=1):
        if not isinstance(req, dict):
            continue
        page_lo = req.get("_window_page_lo")
        page_hi = req.get("_window_page_hi")
        # Prefer the LLM-emitted per-requirement source_page (from [PAGE n]
        # markers). Clamp it to the window's page range so a hallucinated
        # value can't escape the window. Fall back to window start page.
        llm_page = req.get("source_page")
        try:
            llm_page = int(llm_page) if llm_page is not None else None
        except (TypeError, ValueError):
            llm_page = None
        source_page = None
        if isinstance(llm_page, int) and isinstance(page_lo, int) and isinstance(page_hi, int):
            if page_lo <= llm_page <= page_hi:
                source_page = llm_page
        if source_page is None:
            source_page = page_lo if isinstance(page_lo, int) else None

        agreement = (req.get("agreement") or "").strip().lower()
        if agreement in ("both", "analyst_confirmed"):
            extraction_pass = "reconciled"
        elif agreement in ("analyst_only", "validator_only"):
            extraction_pass = agreement
        else:
            extraction_pass = "reconciled"
        confidence = (req.get("confidence") or "").strip().lower() or None

        # Compose any orchestrator-supplied notes with a pages marker so the
        # UI can show "pp. 12-15".
        notes_bits = []
        if page_lo and page_hi:
            notes_bits.append(f"pp. {page_lo}-{page_hi}")
        if req.get("notes"):
            notes_bits.append(str(req["notes"]))
        notes = " | ".join(notes_bits) or None

        db.add(RfpRequirement(
            document_id=document_id,
            proposal_id=proposal_id,
            requirement_id=req.get("req_id") or f"REQ-{i:04d}",
            section_id=req.get("section_id") or "general",
            category=(req.get("category") or "informational").lower(),
            priority=(req.get("priority") or None),
            title=(req.get("title") or "Untitled requirement")[:500],
            description=req.get("description") or "",
            source_page=source_page,
            source_text=req.get("source_text") or "",
            reviewer_confidence=confidence,
            extraction_pass=extraction_pass,
            notes=notes,
        ))
        count += 1

    db.commit()
    return count


def _salvage_json_objects(text: str) -> list:
    """Best-effort recovery of individual {...} objects from a possibly-truncated
    JSON array. Walks the text with a brace/string-aware scanner and tries
    json.loads on each complete top-level object."""
    out = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth = 0
        in_str = False
        esc = False
        start = i
        while i < n:
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[start : i + 1]
                        try:
                            out.append(json.loads(candidate))
                        except json.JSONDecodeError:
                            pass
                        i += 1
                        break
            i += 1
        else:
            # ran off the end mid-object (truncated)
            break
    return out


def _parse_and_store_requirements(db: Session, document_id: int, orchestrator_response: str) -> int:
    """Parse the orchestrator's JSON output and store as RfpRequirement records."""
    import re

    # Strip common wrappers the model might add (``` fences, prose lead-in)
    cleaned = orchestrator_response.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned)
    if fence:
        cleaned = fence.group(1).strip()

    # Greedy match from first [ to last ]
    first = cleaned.find("[")
    last = cleaned.rfind("]")
    reqs = None
    if first != -1 and last != -1 and last > first:
        candidate = cleaned[first:last + 1]
        try:
            reqs = json.loads(candidate)
        except json.JSONDecodeError:
            # Try trimming trailing commas which models sometimes emit
            trimmed = re.sub(r",(\s*[}\]])", r"\1", candidate)
            try:
                reqs = json.loads(trimmed)
            except json.JSONDecodeError:
                reqs = None

    # Salvage path: if the array didn't parse (e.g. truncated output), walk the
    # text and collect whatever complete {...} objects we can find so the user
    # at least gets the requirements the model managed to emit.
    if not isinstance(reqs, list):
        salvaged = _salvage_json_objects(cleaned)
        if salvaged:
            logger.warning(
                "Orchestrator JSON did not parse as a full array; salvaged %d individual objects.",
                len(salvaged),
            )
            reqs = salvaged

    if not isinstance(reqs, list) or not reqs:
        logger.warning("Could not parse requirements JSON from orchestrator — returning 0")
        return 0

    count = 0
    for req in reqs:
        if not isinstance(req, dict):
            continue
        db.add(RfpRequirement(
            document_id=document_id,
            requirement_id=req.get("req_id", f"REQ-{count + 1:03d}"),
            section_id=req.get("section_id", "general"),
            category=req.get("category", "informational"),
            title=req.get("title", "Untitled requirement"),
            description=req.get("description", ""),
            source_text=req.get("source_text", ""),
        ))
        count += 1

    db.commit()
    return count


# ── Generic multi-persona task ───────────────────────────────────────

def run_persona_task(
    db: Session,
    persona_type: str,
    task_prompt: str,
    context: str = "",
    conversation_type: str = "general",
    proposal_id: Optional[int] = None,
    section_id: Optional[str] = None,
) -> dict:
    """Run a single persona on a task and log the conversation."""
    persona = get_persona_by_type(db, persona_type)
    if not persona:
        return {"error": f"Persona type '{persona_type}' not found. Run seed_system_personas first."}

    conv = start_conversation(db, conversation_type, proposal_id, section_id)

    full_prompt = task_prompt
    if context:
        full_prompt = f"CONTEXT:\n{context}\n\nTASK:\n{task_prompt}"

    response = _call_ai(full_prompt, persona.system_prompt)
    add_message(db, conv.id, persona.name, persona.persona_type, "analysis", response, loop_number=1)

    conv.status = "completed"
    conv.summary = response[:500]
    db.commit()

    return {
        "conversation_id": conv.id,
        "persona": persona.name,
        "response": response,
    }


# ── Multi-persona consensus loop ────────────────────────────────────

def run_consensus_loop(
    db: Session,
    persona_types: List[str],
    task_prompt: str,
    context: str = "",
    conversation_type: str = "consensus",
    max_loops: int = 3,
    proposal_id: Optional[int] = None,
    section_id: Optional[str] = None,
) -> dict:
    """
    Run multiple personas on the same task, then have the orchestrator
    synthesize and decide. If disagreements exist, loop up to max_loops.
    """
    conv = start_conversation(db, conversation_type, proposal_id, section_id, max_loops)
    orch = get_persona_by_type(db, "orchestrator")

    for loop in range(1, max_loops + 1):
        conv.current_loop = loop

        # Each persona responds
        responses = {}
        for pt in persona_types:
            persona = get_persona_by_type(db, pt)
            if not persona:
                continue

            if loop == 1:
                prompt = f"CONTEXT:\n{context}\n\nTASK:\n{task_prompt}" if context else task_prompt
            else:
                # On subsequent loops, include previous feedback
                prev_msgs = get_conversation_history(db, conv.id)
                feedback = "\n\n".join(
                    f"[{m['persona_name']}]: {m['content'][:1000]}"
                    for m in prev_msgs if m['loop_number'] == loop - 1
                )
                prompt = f"Previous round of discussion:\n{feedback}\n\nRevise your position or confirm. Be specific about what you agree/disagree with."

            response = _call_ai(prompt, persona.system_prompt)
            add_message(db, conv.id, persona.name, persona.persona_type, "analysis", response, loop_number=loop)
            responses[persona.name] = response

        # Orchestrator evaluates
        orch_system = orch.system_prompt if orch else "Synthesize and decide."
        persona_outputs = "\n\n".join(
            f"**{name}**: {resp[:2000]}" for name, resp in responses.items()
        )
        orch_prompt = f"""Loop {loop} of {max_loops}. Evaluate these persona responses:

{persona_outputs}

Questions:
1. Is there consensus? If yes, state the agreed position.
2. If not, what are the specific disagreements?
3. If this is loop {max_loops}, make a FINAL DECISION.

Respond with:
- CONSENSUS: YES/NO
- DECISION: [your synthesis or final decision]
- ACTION_ITEMS: [list of any follow-ups needed]"""

        orch_response = _call_ai(orch_prompt, orch_system)
        add_message(db, conv.id, "Orchestrator", "orchestrator", "decision", orch_response, loop_number=loop)

        # Check if consensus reached
        if "CONSENSUS: YES" in orch_response.upper() or loop == max_loops:
            conv.status = "consensus_reached" if "CONSENSUS: YES" in orch_response.upper() else "max_loops"
            conv.summary = orch_response[:1000]
            db.commit()
            break

    db.commit()
    return {
        "conversation_id": conv.id,
        "loops_completed": conv.current_loop,
        "status": conv.status,
        "summary": conv.summary,
        "messages": get_conversation_history(db, conv.id),
    }
