"""SEC EDGAR connector — corporate filings (10-K, 10-Q, 8-K, DEF 14A, …).

Wraps the existing ``deep_vendor_research.fetch_sec_edgar`` helper so we
don't reimplement the ticker-lookup → submissions-fetch dance, then fans
the result out into one EvidenceCard per recent filing.

8-K filings whose form code or item codes hint at M&A get reclassified as
``m_and_a`` so the synthesizer can pick them up for the acquisition timeline.
"""
from __future__ import annotations

import logging
from typing import Dict, Iterable, List

from ..evidence_card import EvidenceCard
from ...deep_vendor_research import fetch_sec_edgar

logger = logging.getLogger(__name__)

# Form -> default claim_class. Most are "filing"; 8-Ks containing M&A item
# codes (1.01 / 2.01 / 8.01) we tag as m_and_a downstream.
_FORM_CLASS = {
    "10-K": "filing",
    "10-Q": "filing",
    "8-K": "filing",
    "DEF 14A": "filing",
    "DEFA14A": "filing",
    "S-1": "filing",
    "S-4": "m_and_a",       # registration statement for M&A securities
    "SC 13D": "m_and_a",    # ≥5% beneficial-ownership disclosure
    "SC 13G": "filing",
    "13F-HR": "filing",
}


def _entity_card(profile: Dict) -> EvidenceCard:
    """A summary card so the entity profile (legal name, ticker, CIK, former
    names) lives in the evidence pool too — the synthesizer uses former
    names when reasoning about rebranding patterns."""
    legal = profile.get("legal_name") or "(unknown)"
    ticker = profile.get("ticker")
    cik = profile.get("cik")
    former = profile.get("former_names") or []
    title_bits = [f"SEC entity profile: {legal}"]
    if ticker:
        title_bits.append(f"({ticker})")
    title = " ".join(title_bits)
    snippet_lines = [f"CIK: {cik or '?'}"]
    if profile.get("sic"):
        snippet_lines.append(f"SIC: {profile['sic']}")
    if former:
        snippet_lines.append(f"Former names: {', '.join(former)}")
    return EvidenceCard(
        source_connector="sec_edgar",
        claim_class="filing",
        title=title,
        snippet="\n".join(snippet_lines),
        citation_url=(
            f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"
            if cik else None
        ),
        source_ref=ticker or cik,
        confidence="verified",
        payload={
            "ticker": ticker,
            "cik": cik,
            "legal_name": legal,
            "former_names": former,
            "sic": profile.get("sic"),
        },
    )


def _filing_card(filing: Dict, ticker: str | None, cik: str | None) -> EvidenceCard:
    form = (filing.get("form") or "").strip().upper()
    filed = filing.get("filed")
    klass = _FORM_CLASS.get(form, "filing")

    title = f"SEC {form} — filed {filed}" if filed else f"SEC {form}"
    if ticker:
        title = f"{ticker}: {title}"

    return EvidenceCard(
        source_connector="sec_edgar",
        claim_class=klass,
        title=title,
        citation_url=filing.get("url"),
        source_ref=f"{cik}:{form}:{filed}" if (cik and filed) else None,
        event_date=filed,
        confidence="verified",  # SEC filings are primary-source documents
        payload={"form": form, "ticker": ticker, "cik": cik},
    )


def fetch(competitor_name: str, aliases: Iterable[str], settings: Dict) -> List[EvidenceCard]:
    """Search EDGAR by primary name + aliases. Most vendors only resolve on
    one name; we still try aliases so a recently-renamed entity matches."""
    seen_ciks: set[str] = set()
    cards: List[EvidenceCard] = []
    names_to_try = [competitor_name, *(a for a in (aliases or []) if a)]

    for name in names_to_try:
        try:
            profile = fetch_sec_edgar(name)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"SEC EDGAR lookup failed for {name!r}: {type(e).__name__}: {e}")
            continue
        if not profile.get("available"):
            continue
        cik = str(profile.get("cik") or "")
        if cik in seen_ciks:
            continue
        seen_ciks.add(cik)

        cards.append(_entity_card(profile))
        for filing in (profile.get("recent_filings") or []):
            cards.append(_filing_card(filing, profile.get("ticker"), cik))

    logger.info(f"SEC EDGAR: {len(cards)} cards for {competitor_name!r}")
    return cards
