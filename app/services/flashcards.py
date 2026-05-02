"""Flashcard generation + SM-2 spaced repetition.

Generates Q/A flashcards from `rfp_requirements` rows. Default scope is
Section 4 (Statement of Work) for the given proposal, which is what the
user wants to test comprehension of (NOT bid mechanics in 1.* / 2.*).

LLM choice: gpt-4o-mini by default (10x cheaper than Claude Opus, plenty
good for Q/A on already-extracted text). Falls back to Claude if OpenAI
isn't configured.

Idempotent: skips requirements that already have a flashcard.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from ..models import Flashcard, RfpRequirement

logger = logging.getLogger(__name__)


# ── Generator ────────────────────────────────────────────────────────

_GENERATOR_SYSTEM_PROMPT = """You are an instructional designer building flashcards to test a proposal team's comprehension of a Statement of Work (SOW) requirement from an RFP.

Return ONLY valid JSON (an array of 1 to 3 cards). No prose, no markdown fences.

Each card is an object:
  {
    "question": "...",
    "answer":   "...",
    "card_type": "recall" | "concept" | "numeric" | "cross_reference",
    "difficulty": "easy" | "medium" | "hard"
  }

Rules:
  * Generate at most 3 cards per requirement. Often 1 is enough; only generate more when the requirement has multiple distinct testable facts.
  * Questions must be answerable from the requirement text — do NOT ask things that require the rest of the RFP unless you're explicitly testing cross-reference recall.
  * Prefer specific, concrete questions over generic ones.
    BAD:  "What does Section 4.10.5 require?"  (too vague)
    GOOD: "Per Section 4.10.5, what's the maximum allowed time per vehicle inspection?"
  * Numeric facts (durations, quantities, percentages, dollar thresholds, page counts, deadlines expressed as Start + Nd) are gold — turn them into card_type "numeric".
  * Use card_type "concept" for definitions, distinctions, or process descriptions.
  * Use card_type "cross_reference" when the requirement mentions a partner section or document the reader needs to remember.
  * Skip requirements that are pure boilerplate or repeat earlier text — emit an empty array [].
  * Answers should be 1-3 sentences MAX. Cite the section number in the answer.
  * Do NOT invent facts. Stick to what's actually in the requirement text.
"""


def _provider_order(db: Session) -> list[tuple[str, str]]:
    """Return an ordered list of (provider, model) candidates to try.

    Honors the same DB-backed `app_settings.llm_provider` config that
    the orchestrator and competitor analyst use — so on a corp network
    where OpenAI is blocked by TLS inspection, this falls in line with
    the rest of the app instead of insisting on OpenAI.

    Order:
      1. The configured `llm_provider` (DB or env) — that's "anthropic"
         on this machine.
      2. The other provider, IF its key is configured.

    Cheap models are preferred per provider:
      * Anthropic: claude-sonnet-4-5-20250929 (known-good in this corp
        environment; used by competitor_analyst). Override with
        FLASHCARD_ANTHROPIC_MODEL=claude-haiku-... once you've
        confirmed a Haiku tag works on your tenant.
      * OpenAI:    gpt-4o-mini
    """
    import os
    # Reuse the canonical loader from orchestrator_agent so we get the
    # full DB+env merge (and the keys it found).
    try:
        from .orchestrator_agent import _load_ai_settings as _oa_load
        cfg = _oa_load()
    except Exception:  # noqa: BLE001 — fall back to env-only
        cfg = {
            "llm_provider": os.getenv("LLM_PROVIDER", "anthropic").lower(),
            "openai_api_key": os.getenv("OPENAI_API_KEY", ""),
            "anthropic_api_key": os.getenv("ANTHROPIC_API_KEY", ""),
        }
    preferred = (cfg.get("llm_provider") or "anthropic").lower()
    has_openai = bool((cfg.get("openai_api_key") or "").strip()) or bool(os.getenv("OPENAI_API_KEY", "").strip())
    has_anth = bool((cfg.get("anthropic_api_key") or "").strip()) or bool(os.getenv("ANTHROPIC_API_KEY", "").strip())

    # Default flashcard models — overridable via env for tuning.
    # claude-sonnet-4-5-20250929 is what competitor_analyst uses
    # successfully on this corp tenant; safer default than claude-haiku-*.
    anth_model = os.getenv("FLASHCARD_ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")
    openai_model = os.getenv("FLASHCARD_OPENAI_MODEL", "gpt-4o-mini")

    candidates: list[tuple[str, str]] = []
    if preferred == "anthropic":
        if has_anth:   candidates.append(("anthropic", anth_model))
        if has_openai: candidates.append(("openai", openai_model))
    else:
        if has_openai: candidates.append(("openai", openai_model))
        if has_anth:   candidates.append(("anthropic", anth_model))

    if not candidates:
        raise RuntimeError("No LLM provider configured (OPENAI_API_KEY / ANTHROPIC_API_KEY)")
    return candidates


def _call_llm_one(provider: str, model: str, system: str, user_prompt: str,
                  max_tokens: int = 1500) -> str:
    """Single-shot call against ONE provider. Caller handles provider
    fallback. Uses the same SSL-aware httpx client + API-key merge as
    the rest of the app so DB-backed `app_settings.openai_api_key` and
    `anthropic_api_key` are picked up (corp-network requirement).
    """
    from .ssl_utils import make_sync_httpx_client
    from .orchestrator_agent import _load_ai_settings as _oa_load
    cfg = _oa_load()
    if provider == "openai":
        import openai
        api_key = (cfg.get("openai_api_key") or "").strip()
        client = openai.OpenAI(
            api_key=api_key or None,
            http_client=make_sync_httpx_client(timeout=120.0),
        )
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=0.2,
        )
        try:
            from . import llm_usage as _usage
            in_tok, out_tok = _usage.extract_openai_usage(resp)
            _usage.record(
                provider="openai", model=model,
                input_tokens=in_tok, output_tokens=out_tok,
                endpoint="flashcards.generate",
            )
        except Exception:  # noqa: BLE001
            pass
        return resp.choices[0].message.content or ""
    elif provider == "anthropic":
        import anthropic
        api_key = (cfg.get("anthropic_api_key") or "").strip()
        client = anthropic.Anthropic(
            api_key=api_key or None,
            http_client=make_sync_httpx_client(timeout=120.0),
        )
        msg = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
        )
        try:
            from . import llm_usage as _usage
            in_tok, out_tok = _usage.extract_anthropic_usage(msg)
            _usage.record(
                provider="anthropic", model=model,
                input_tokens=in_tok, output_tokens=out_tok,
                endpoint="flashcards.generate",
            )
        except Exception:  # noqa: BLE001
            pass
        parts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
        return "\n".join(parts).strip()
    raise RuntimeError(f"Unsupported provider: {provider}")


def _call_llm_with_fallback(candidates: list[tuple[str, str]], system: str,
                            user_prompt: str, max_tokens: int = 1500) -> tuple[str, str, str]:
    """Try each (provider, model) in order. Returns
    (raw_text, provider_used, model_used). Raises only if EVERY candidate
    fails — that way a single 302 from a corp-blocked OpenAI doesn't
    abort the whole batch.
    """
    last_err: Exception | None = None
    for provider, model in candidates:
        try:
            raw = _call_llm_one(provider, model, system, user_prompt, max_tokens)
            return raw, provider, model
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning(
                "[flashcards] provider %s model %s failed: %s",
                provider, model, type(e).__name__,
            )
            continue
    raise last_err if last_err else RuntimeError("No provider succeeded")


def _parse_cards(raw: str) -> list[dict]:
    if not raw:
        return []
    s = raw.strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", s)
    if m:
        s = m.group(1).strip()
    first, last = s.find("["), s.rfind("]")
    if first == -1 or last <= first:
        return []
    candidate = s[first:last + 1]
    try:
        data = json.loads(candidate)
        if isinstance(data, list):
            return [c for c in data if isinstance(c, dict) and c.get("question") and c.get("answer")]
    except json.JSONDecodeError:
        # Trim trailing commas and retry once
        trimmed = re.sub(r",(\s*[}\]])", r"\1", candidate)
        try:
            data = json.loads(trimmed)
            if isinstance(data, list):
                return [c for c in data if isinstance(c, dict) and c.get("question") and c.get("answer")]
        except json.JSONDecodeError:
            return []
    return []


def generate_for_proposal(
    db: Session,
    proposal_id: int,
    section_prefix: str = "4",
    max_requirements: Optional[int] = None,
    progress_cb=None,
) -> dict:
    """Generate flashcards for every Section-`section_prefix` requirement
    on the proposal that doesn't already have a card.

    Idempotent: skips reqs whose id is already referenced by any
    Flashcard row.

    Returns summary dict.
    """
    candidates = _provider_order(db)
    logger.info(
        "[flashcards] provider candidates (in priority order): %s",
        [f"{p}:{m}" for p, m in candidates],
    )

    # Existing requirement_ids that already have at least one card.
    existing = {
        r[0] for r in db.query(Flashcard.requirement_id)
        .filter(Flashcard.proposal_id == proposal_id)
        .filter(Flashcard.requirement_id.isnot(None))
        .all()
    }

    q = (
        db.query(RfpRequirement)
        .filter(RfpRequirement.proposal_id == proposal_id)
        .filter(RfpRequirement.section_id.like(f"{section_prefix}%"))
        # Skip purely informational fluff — those are usually boilerplate.
        # We DO want mandatory + numeric + deadline + certification reqs.
        .order_by(RfpRequirement.section_id.asc(), RfpRequirement.id.asc())
    )
    if max_requirements:
        q = q.limit(max_requirements * 2)  # over-fetch so the existing-skip doesn't shrink it
    reqs = q.all()
    todo = [r for r in reqs if r.id not in existing]
    if max_requirements:
        todo = todo[:max_requirements]

    logger.info(
        "[flashcards] proposal=%s section=%s candidates=%d already=%d to_generate=%d",
        proposal_id, section_prefix, len(reqs), len(existing), len(todo),
    )

    cards_created = 0
    reqs_processed = 0
    errors: list[str] = []
    t_start = time.time()

    for i, req in enumerate(todo):
        try:
            user_prompt = (
                f"Section: {req.section_id}\n"
                f"Title: {req.title or '(none)'}\n"
                f"Category: {req.category or '(none)'}\n"
                f"Description: {req.description or req.source_text or '(none)'}\n"
            )
            raw, used_provider, used_model = _call_llm_with_fallback(
                candidates, _GENERATOR_SYSTEM_PROMPT, user_prompt,
            )
            cards = _parse_cards(raw)
            for c in cards:
                fc = Flashcard(
                    proposal_id=proposal_id,
                    requirement_id=req.id,
                    section_id=req.section_id,
                    question=str(c.get("question") or "")[:2000],
                    answer=str(c.get("answer") or "")[:2000],
                    card_type=str(c.get("card_type") or "recall").lower()[:30],
                    difficulty=str(c.get("difficulty") or "medium").lower()[:10],
                    source_page=req.source_page,
                    source_text=(req.source_text or "")[:1000],
                    generated_by_model=f"{used_provider}:{used_model}",
                )
                db.add(fc)
                cards_created += 1
            reqs_processed += 1
            if cards:  # only commit when we actually produced something
                db.commit()
            if progress_cb:
                try:
                    progress_cb({
                        "processed": i + 1, "total": len(todo),
                        "cards_created": cards_created,
                    })
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001
            errors.append(f"req {req.id}: {e}")
            logger.warning("[flashcards] req %s failed: %s", req.id, e)

    elapsed = round(time.time() - t_start, 1)
    return {
        "proposal_id": proposal_id,
        "section_prefix": section_prefix,
        "candidates": len(reqs),
        "skipped_existing": len(reqs) - len(todo),
        "requirements_processed": reqs_processed,
        "cards_created": cards_created,
        "errors_count": len(errors),
        "errors": errors[:10],
        "elapsed_seconds": elapsed,
        "providers_tried": [f"{p}:{m}" for p, m in candidates],
    }


# ── Spaced repetition (simplified SM-2) ──────────────────────────────

# Rating → ease delta + min interval factor.
# Loosely follows Anki's modern SM-2 derivative without the multi-step
# learning queue (we just dispatch directly to the review interval).
_RATING_RULES = {
    "again": {"ease_delta": -0.20, "interval_factor": 0.0,  "next_min_days": 1},
    "good":  {"ease_delta":  0.0,  "interval_factor": 1.0,  "next_min_days": 1},
    "easy":  {"ease_delta": +0.15, "interval_factor": 1.3,  "next_min_days": 4},
}


def apply_review(card: Flashcard, rating: str) -> dict:
    """Update card SM-2 state in-place. Returns a summary dict for the
    response. Does NOT commit — caller handles that.
    """
    rating = (rating or "").strip().lower()
    if rating not in _RATING_RULES:
        raise ValueError(f"rating must be one of {list(_RATING_RULES)}")
    rule = _RATING_RULES[rating]

    now = datetime.utcnow()
    card.review_count = (card.review_count or 0) + 1
    if rating != "again":
        card.correct_count = (card.correct_count or 0) + 1

    # Ease bounds (SM-2 typical floor at 1.3).
    new_ease = max(1.3, min(3.0, (card.ease_factor or 2.5) + rule["ease_delta"]))
    card.ease_factor = new_ease

    # Interval calculation.
    if rating == "again":
        card.interval_days = 0
        next_days = 1
    elif (card.interval_days or 0) == 0:
        # First successful review.
        next_days = rule["next_min_days"]
        card.interval_days = next_days
    else:
        # Subsequent: prev_interval * ease * factor (factor = 1 for good, 1.3 for easy).
        next_days = max(
            rule["next_min_days"],
            int(round((card.interval_days or 1) * new_ease * rule["interval_factor"])),
        )
        card.interval_days = next_days

    card.last_rating = rating
    card.last_reviewed_at = now
    card.next_review_at = now + timedelta(days=next_days)
    return {
        "rating": rating,
        "ease_factor": round(new_ease, 2),
        "interval_days": card.interval_days,
        "next_review_at": card.next_review_at.isoformat(),
        "review_count": card.review_count,
        "correct_count": card.correct_count,
    }
