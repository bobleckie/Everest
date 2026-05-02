"""UK Contracts Finder connector — public-sector procurement notices.

Free JSON API published by the UK Cabinet Office under the OCDS standard.
Returns awarded contracts where the supplier name matches the competitor,
which is the closest thing to a public-sector "win/loss graph" we can get
without bespoke state-portal scraping.

Endpoint:
  GET https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search?stages=award&keyword=<vendor>

Each released contract becomes a ``contract_award`` evidence card carrying
buyer, value, and award date — exactly the fields the synthesizer needs to
plot UK awards on the timeline.
"""
from __future__ import annotations

import json
import logging
from typing import Dict, Iterable, List, Optional

from ..evidence_card import EvidenceCard
from ...ssl_utils import make_sync_httpx_client

logger = logging.getLogger(__name__)

API_URL = "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search"
MAX_PER_QUERY = 25


def _award_to_card(release: Dict, query_name: str) -> Optional[EvidenceCard]:
    """OCDS releases are nested. We pull the relevant fields out of the first
    award block. Releases without a matching supplier are dropped."""
    awards = release.get("awards") or []
    if not awards:
        return None
    award = awards[0]
    suppliers = award.get("suppliers") or []
    supplier_names = [s.get("name", "") for s in suppliers]
    # Keep only awards where the supplier name actually contains our query —
    # the keyword search is broad and matches in tender bodies too.
    if not any(query_name.lower() in (n or "").lower() for n in supplier_names):
        return None

    title = (award.get("title")
             or release.get("tender", {}).get("title")
             or "(untitled UK contract)")
    description = (award.get("description")
                   or release.get("tender", {}).get("description"))
    value = ((award.get("value") or {}).get("amount"))
    currency = ((award.get("value") or {}).get("currency"))
    date = award.get("date") or release.get("date")

    buyer = (release.get("buyer") or {}).get("name")
    snippet_bits = []
    if buyer:
        snippet_bits.append(f"Buyer: {buyer}")
    if supplier_names:
        snippet_bits.append(f"Supplier(s): {', '.join(supplier_names[:3])}")
    if value is not None:
        snippet_bits.append(f"Value: {currency or 'GBP'} {value:,.0f}")
    if description:
        snippet_bits.append(description[:400])

    citation_url = None
    for doc in (award.get("documents") or release.get("tender", {}).get("documents") or []):
        if doc.get("url"):
            citation_url = doc["url"]
            break

    return EvidenceCard(
        source_connector="uk_contracts_finder",
        claim_class="contract_award",
        title=title[:240],
        snippet="\n".join(snippet_bits) or None,
        citation_url=citation_url,
        source_ref=release.get("ocid"),
        event_date=date[:10] if isinstance(date, str) and len(date) >= 10 else date,
        jurisdiction="UK",
        amount_usd=None,  # left None — value is in source currency, conversion is fragile
        confidence="verified",
        payload={
            "buyer": buyer,
            "suppliers": supplier_names,
            "value": value,
            "currency": currency,
            "ocid": release.get("ocid"),
        },
    )


def fetch(competitor_name: str, aliases: Iterable[str], settings: Dict) -> List[EvidenceCard]:
    """Search Contracts Finder for awards naming the competitor or any alias."""
    seen: set[str] = set()
    cards: List[EvidenceCard] = []
    queries = [competitor_name, *(a for a in (aliases or []) if a)]

    headers = {
        "Accept": "application/json",
        "User-Agent": "Parsons RFP Platform (research@parsons.com)",
    }
    with make_sync_httpx_client(timeout=30.0) as client:
        for q in queries:
            if not q or not q.strip():
                continue
            try:
                r = client.get(
                    API_URL,
                    params={"stages": "award", "keyword": q, "limit": MAX_PER_QUERY},
                    headers=headers,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"UK Contracts Finder fetch failed for {q!r}: {type(e).__name__}: {e}")
                continue
            if r.status_code != 200:
                logger.info(f"UK Contracts Finder non-200 ({r.status_code}) for {q!r}")
                continue
            try:
                data = r.json()
            except (ValueError, json.JSONDecodeError):
                continue
            for release in (data.get("releases") or []):
                card = _award_to_card(release, q)
                if not card:
                    continue
                key = card.dedupe_key()
                if key in seen:
                    continue
                seen.add(key)
                cards.append(card)

    logger.info(f"UK Contracts Finder: {len(cards)} cards for {competitor_name!r}")
    return cards
