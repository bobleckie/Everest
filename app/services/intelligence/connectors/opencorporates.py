"""OpenCorporates connector — global company-registry records.

Each search result becomes a ``filing`` evidence card carrying jurisdiction,
incorporation date, status, and registry URL. Useful for mapping the
competitor's full corporate family across jurisdictions (so we know which
subsidiaries to also search in CourtListener / EDGAR).
"""
from __future__ import annotations

import logging
from typing import Dict, Iterable, List

from ..evidence_card import EvidenceCard
from ...deep_vendor_research import fetch_opencorporates

logger = logging.getLogger(__name__)


def _company_card(c: Dict) -> EvidenceCard:
    name = c.get("name") or "(unknown)"
    juris = c.get("jurisdiction") or "?"
    status = c.get("status") or ""
    title = f"{name} — {juris}"
    if status:
        title += f" [{status}]"
    snippet_bits = []
    if c.get("number"):
        snippet_bits.append(f"Reg #: {c['number']}")
    if c.get("type"):
        snippet_bits.append(f"Type: {c['type']}")
    if c.get("incorporated"):
        snippet_bits.append(f"Incorporated: {c['incorporated']}")
    return EvidenceCard(
        source_connector="opencorporates",
        claim_class="filing",
        title=title,
        snippet="\n".join(snippet_bits) or None,
        citation_url=c.get("url"),
        source_ref=f"{juris}:{c.get('number','')}",
        event_date=c.get("incorporated"),
        jurisdiction=juris.upper() if isinstance(juris, str) else None,
        confidence="verified",
        payload=c,
    )


def fetch(competitor_name: str, aliases: Iterable[str], settings: Dict) -> List[EvidenceCard]:
    seen_keys: set[str] = set()
    cards: List[EvidenceCard] = []
    for name in [competitor_name, *(aliases or [])]:
        if not name:
            continue
        try:
            res = fetch_opencorporates(name)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"OpenCorporates lookup failed for {name!r}: {type(e).__name__}: {e}")
            continue
        if not res.get("available"):
            continue
        for c in res.get("companies") or []:
            card = _company_card(c)
            if card.dedupe_key() in seen_keys:
                continue
            seen_keys.add(card.dedupe_key())
            cards.append(card)
    logger.info(f"OpenCorporates: {len(cards)} cards for {competitor_name!r}")
    return cards
