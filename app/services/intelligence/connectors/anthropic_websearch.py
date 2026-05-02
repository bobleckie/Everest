"""Anthropic web_search connector — autonomous web research.

This is the connector that closes the autonomous-discovery loop. It hands
Claude Opus 4.7 the structured evidence already gathered by the deterministic
connectors (CourtListener, SEC EDGAR, OpenCorporates, GLEIF, Wikidata, UK
Contracts Finder) plus the competitor's name and aliases, and asks Claude
to autonomously run targeted web searches for the **gaps** — pattern
discovery the user described:
  * "When did this company lose state inspection contracts? Who won them
    and was the winner subsequently acquired?"
  * "What breaches have they or their acquired subsidiaries disclosed?"
  * "What change-order disputes / litigation patterns appear in news?"

The Anthropic SDK runs the tool-use loop server-side; we call once and get
back the synthesized text + every URL Claude cited along the way. Each
URL becomes one EvidenceCard with claim_class inferred from the host and
title (heuristic — see ``_infer_claim_class``).
"""
from __future__ import annotations

import logging
from typing import Dict, Iterable, List, Optional

from ..evidence_card import EvidenceCard
from ...deep_vendor_research import _run_opus_with_search

logger = logging.getLogger(__name__)


# Heuristic mapping from URL host/title patterns to claim_class. Order matters.
_HOST_RULES = [
    # Court records
    ("courtlistener.com", "litigation"),
    ("pacer.uscourts.gov", "litigation"),
    ("law.justia.com", "litigation"),
    ("casetext.com", "litigation"),
    # Filings
    ("sec.gov", "filing"),
    ("companieshouse.gov.uk", "filing"),
    ("contractsfinder.service.gov.uk", "contract_award"),
    # Procurement
    ("usaspending.gov", "contract_award"),
    ("sam.gov", "contract_award"),
    ("nj.gov", "contract_award"),
    ("ny.gov", "contract_award"),
    ("ca.gov", "contract_award"),
    # Breach disclosures
    ("hhs.gov/ocr/breach", "breach"),
    ("oag.ca.gov", "breach"),
    ("oag.state.ny.us", "breach"),
    # Reviews
    ("yelp.com", "review_signal"),
    ("trustpilot.com", "review_signal"),
    ("glassdoor.com", "review_signal"),
    # M&A press wires
    ("businesswire.com", "news"),
    ("prnewswire.com", "news"),
    ("globenewswire.com", "news"),
]

# Title keyword overrides. Applied after host check; "win" beats "news" if the
# host is generic.
_TITLE_KEYWORDS = [
    (("acquir", "merger", "buyout"), "m_and_a"),
    (("breach", "data leak", "ransomware"), "breach"),
    (("award", "selected", "wins contract", "contract for"), "contract_award"),
    (("loses", "lost contract", "not selected", "denied", "rejected bid"), "contract_loss"),
    (("lawsuit", "sued", "court ruling", "settlement", "complaint"), "litigation"),
    (("ceo", "president", "appointed", "resigned", "departure"), "leadership"),
    (("sec investigation", "fined", "penalty", "consent decree"), "regulatory"),
]


def _infer_claim_class(url: Optional[str], title: Optional[str]) -> str:
    """Best-effort claim_class from URL host + title text. Defaults to 'news'."""
    host = ""
    if url:
        try:
            from urllib.parse import urlparse
            host = (urlparse(url).hostname or "").lower()
        except Exception:
            host = ""
    if host:
        for needle, klass in _HOST_RULES:
            if needle in host:
                return klass
    if title:
        t = title.lower()
        for needles, klass in _TITLE_KEYWORDS:
            if any(n in t for n in needles):
                return klass
    return "news"


# ── Prompt: senior capture analyst, given structured evidence already
# gathered, asked to fill gaps autonomously via web_search. ───────────
_SYSTEM = (
    "You are a senior competitive-intelligence analyst supporting a capture team "
    "preparing to bid against a known competitor. You already have structured "
    "evidence from court records, SEC filings, corporate registries, and the "
    "competitor's news feed. Your job is to autonomously run the web_search tool "
    "to fill the most decision-critical gaps and surface PATTERNS the structured "
    "data alone cannot show: \n"
    " - acquisition / divestiture history and especially M&A that follows a "
    "   contract loss (the 'acquire-the-winner' pattern);\n"
    " - leadership turnover and recent departures in the relevant business unit;\n"
    " - data breaches, regulatory consent decrees, and audit findings — including "
    "   any inherited via M&A;\n"
    " - state DMV / public-sector contract WINS AND LOSSES with named jurisdictions;\n"
    " - patterns of change-order escalation, settlement amounts, or vendor "
    "   underbidding that recur across contracts;\n"
    " - rebrand history (former corporate names, brands they retired).\n"
    "\n"
    "RULES:\n"
    "1. Run targeted searches — do NOT generic-Google the company name. Frame each "
    "   search as a specific question (e.g. 'OPUS Inspection Arizona contract loss').\n"
    "2. Cite every claim with the URL the search returned. Do not synthesize claims "
    "   that are not in your search results.\n"
    "3. Prefer official sources (court filings, state DMV press releases, SEC, "
    "   company 8-K, breach notification portals) over general news.\n"
    "4. If you cannot find evidence for a question after one or two searches, MOVE ON. "
    "   Do not pad; gaps are useful information for a human reviewer.\n"
    "5. Output a brief markdown synthesis at the end (under 800 words), but the "
    "   important deliverable is the URL citations — those are what get persisted."
)


def _build_user_prompt(
    competitor_name: str,
    aliases: List[str],
    structured_summary: str,
) -> str:
    """The user-message body. Keep it terse — Claude wastes tool budget when we
    over-prompt. The structured_summary is a compact JSON dump from upstream
    connectors so Claude can see what's already known and skip those facts."""
    alias_str = ", ".join(aliases) if aliases else "(none)"
    return (
        f"Competitor: {competitor_name}\n"
        f"Known aliases / former names: {alias_str}\n\n"
        f"=== Already-gathered structured evidence (do not re-fetch these) ===\n"
        f"{structured_summary or '(no upstream data — start from scratch)'}\n\n"
        f"=== Your task ===\n"
        f"Run web_search to fill gaps and surface pattern threads, per the rules. "
        f"Focus on: contract wins/losses by jurisdiction, M&A timeline (especially "
        f"acquisitions immediately after a contract loss), breaches and regulatory "
        f"actions, leadership turnover, and rebrand history. End with a brief "
        f"markdown synthesis."
    )


def fetch(
    competitor_name: str,
    aliases: Iterable[str],
    settings: Dict,
    *,
    structured_summary: str = "",
    max_searches: int = 12,
    model: Optional[str] = None,
) -> List[EvidenceCard]:
    """Run one Claude Opus turn with web_search enabled, emit one card per
    cited URL.

    The orchestrator passes a pre-built ``structured_summary`` so Claude
    doesn't re-discover what the deterministic connectors already covered.
    """
    api_key = (settings.get("anthropic_api_key") or "").strip()
    if not api_key:
        logger.info("Anthropic API key not configured — skipping web_search connector")
        return []

    chosen_model = model or settings.get("claude_research_model") or "claude-opus-4-7"
    aliases_list = [a for a in (aliases or []) if a]

    try:
        result = _run_opus_with_search(
            anthropic_key=api_key,
            model=chosen_model,
            system=_SYSTEM,
            user_content=_build_user_prompt(competitor_name, aliases_list, structured_summary),
            max_searches=max_searches,
            max_tokens=4000,
            timeout=600.0,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Anthropic web_search failed for {competitor_name!r}: {type(e).__name__}: {e}")
        return []

    citations = result.get("citations") or []
    synthesis_text = result.get("text") or ""

    cards: List[EvidenceCard] = []
    seen_urls: set[str] = set()

    # One card per cited URL.
    for c in citations:
        url = c.get("url")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        title = (c.get("title") or url)[:240]
        snippet = (c.get("cited_text") or "")[:1000].strip() or None
        klass = _infer_claim_class(url, title)
        cards.append(EvidenceCard(
            source_connector="anthropic_websearch",
            claim_class=klass,
            title=title,
            snippet=snippet,
            citation_url=url,
            source_ref=url,
            confidence="reported",  # web sources — the synthesizer can promote to verified
            payload={"cited_by": "claude_opus_web_search"},
        ))

    # One bonus synthesis card so the synthesizer pass has Claude's own framing
    # to work with — flagged as "inferred" because it's analysis, not primary
    # source.
    if synthesis_text:
        cards.append(EvidenceCard(
            source_connector="anthropic_websearch",
            claim_class="other",
            title=f"Web-research synthesis (Claude Opus): {competitor_name}",
            snippet=synthesis_text[:1500],
            source_ref=f"websearch_synthesis::{competitor_name.lower()}",
            confidence="inferred",
            payload={
                "model": result.get("model"),
                "stop_reason": result.get("stop_reason"),
                "full_text": synthesis_text,
            },
        ))

    logger.info(
        f"anthropic_websearch: {len(cards)} cards "
        f"({len(citations)} citations) for {competitor_name!r}"
    )
    return cards
