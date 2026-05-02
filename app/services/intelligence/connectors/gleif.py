"""GLEIF connector — Legal Entity Identifier (LEI) parent chain (free).

The killer use of GLEIF is the parent / ultimate-parent chain — given a
subsidiary's LEI, we can walk up to the ultimate corporate parent. Maps
directly onto the user's described "OPUS lost AZ → bought the winner"
pattern: knowing the ultimate parent tells us which subsidiaries to fold
into the search.
"""
from __future__ import annotations

import logging
from typing import Dict, Iterable, List

from ..evidence_card import EvidenceCard
from ...deep_vendor_research import fetch_gleif

logger = logging.getLogger(__name__)


def _record_card(rec: Dict) -> EvidenceCard:
    legal = rec.get("legal_name") or "(unknown)"
    juris = (rec.get("jurisdiction") or "").upper() or None
    title = f"GLEIF: {legal}"
    if juris:
        title += f" [{juris}]"
    snippet_lines = [f"LEI: {rec.get('lei')}"]
    if rec.get("category"):
        snippet_lines.append(f"Category: {rec['category']}")
    if rec.get("status"):
        snippet_lines.append(f"Status: {rec['status']}")
    if rec.get("parent_lei"):
        snippet_lines.append(f"Parent LEI: {rec['parent_lei']}")
    return EvidenceCard(
        source_connector="gleif",
        claim_class="filing",
        title=title,
        snippet="\n".join(snippet_lines),
        citation_url=(
            f"https://search.gleif.org/#/record/{rec.get('lei')}"
            if rec.get("lei") else None
        ),
        source_ref=rec.get("lei"),
        jurisdiction=juris,
        confidence="verified",
        payload=rec,
    )


def fetch(competitor_name: str, aliases: Iterable[str], settings: Dict) -> List[EvidenceCard]:
    seen: set[str] = set()
    cards: List[EvidenceCard] = []
    for name in [competitor_name, *(aliases or [])]:
        if not name:
            continue
        try:
            res = fetch_gleif(name)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"GLEIF lookup failed for {name!r}: {type(e).__name__}: {e}")
            continue
        if not res.get("available"):
            continue
        for rec in res.get("records") or []:
            lei = rec.get("lei")
            if not lei or lei in seen:
                continue
            seen.add(lei)
            cards.append(_record_card(rec))
    logger.info(f"GLEIF: {len(cards)} cards for {competitor_name!r}")
    return cards
