"""CourtListener connector — federal RECAP dockets + opinions.

Hits the public REST v4 API at ``https://www.courtlistener.com/api/rest/v4/``.
Authenticated with a free token (5K req/day). Emits one EvidenceCard per
hit, one per (competitor name, alias) cross-search.

We intentionally search by exact phrase to keep precision high (the
``"Envirotest"`` search above already returned 134 federal cases on a single
historical brand — most of them genuine party-named lawsuits, not stray
text mentions).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional

from ..evidence_card import EvidenceCard
from ...ssl_utils import make_sync_httpx_client

logger = logging.getLogger(__name__)

API_BASE = "https://www.courtlistener.com/api/rest/v4"
SEARCH_URL = f"{API_BASE}/search/"

# CourtListener returns up to 20 results by default and supports pagination
# via ``cursor``. For a single competitor we cap at 50 hits per query type
# per name to avoid runaway DB writes when an alias is a generic word.
MAX_HITS_PER_QUERY = 50


def _hit_to_card(hit: Dict[str, Any], query_type: str, query_name: str) -> Optional[EvidenceCard]:
    """Map a CourtListener search hit into an EvidenceCard.

    ``query_type`` is "r" (RECAP federal docket) or "o" (opinion). The two
    response shapes differ slightly so we normalize them here.
    """
    caption = (hit.get("caseName") or hit.get("case_name") or "").strip()
    if not caption:
        return None

    # CourtListener ``court`` is sometimes a slug ("ca9"), sometimes a full
    # name string. Either is fine for display.
    court = hit.get("court") or hit.get("court_id") or ""
    date_filed = hit.get("dateFiled") or hit.get("date_filed") or None

    # Prefer ``absolute_url`` then fall back to building one from the docket id.
    rel_url = hit.get("absolute_url") or ""
    if not rel_url and hit.get("docket_id"):
        rel_url = f"/docket/{hit['docket_id']}/"
    citation_url = f"https://www.courtlistener.com{rel_url}" if rel_url.startswith("/") else (rel_url or None)

    # Snippet preference: ``snippet`` (highlighted), then ``description``.
    snippet = hit.get("snippet") or hit.get("description") or None
    if isinstance(snippet, str):
        snippet = snippet.strip()[:1000] or None

    title = caption[:240]
    if court:
        title = f"{caption[:180]} ({court[:50]})"

    return EvidenceCard(
        source_connector="courtlistener",
        claim_class="litigation",
        title=title,
        snippet=snippet,
        citation_url=citation_url,
        source_ref=str(hit.get("id") or hit.get("docket_id") or ""),
        event_date=date_filed,
        confidence="verified",  # court records are primary-source documents
        payload={
            "query_type": query_type,
            "query_name": query_name,
            "court": court,
            "case_name": caption,
            "docket_id": hit.get("docket_id"),
            "docket_number": hit.get("docketNumber") or hit.get("docket_number"),
            "date_terminated": hit.get("dateTerminated") or hit.get("date_terminated"),
            "judge": hit.get("judge"),
        },
    )


def _search(client, token: str, query: str, query_type: str) -> List[Dict[str, Any]]:
    """One search call. Returns raw hits or an empty list on any failure."""
    headers = {
        "Authorization": f"Token {token}",
        "User-Agent": "Parsons RFP Platform (research@parsons.com)",
        "Accept": "application/json",
    }
    try:
        r = client.get(SEARCH_URL, params={"type": query_type, "q": query}, headers=headers)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"CourtListener {query_type} search failed for {query!r}: {type(e).__name__}: {e}")
        return []
    if r.status_code == 401:
        logger.warning("CourtListener returned 401 — token invalid or revoked. Skipping.")
        return []
    if r.status_code == 429:
        logger.warning("CourtListener rate-limited (429). Skipping remaining queries.")
        return []
    if r.status_code != 200:
        logger.info(f"CourtListener {query_type} search non-200 ({r.status_code}) for {query!r}")
        return []
    try:
        results = r.json().get("results") or []
    except ValueError:
        return []
    return results[:MAX_HITS_PER_QUERY]


def fetch(
    competitor_name: str,
    aliases: Iterable[str],
    settings: Dict[str, str],
) -> List[EvidenceCard]:
    """Search RECAP + Opinions for the competitor name and every alias.

    ``settings`` must contain ``courtlistener_api_token``. If absent, returns
    an empty list (the orchestrator will record that the connector is
    disabled and continue).
    """
    token = (settings.get("courtlistener_api_token") or "").strip()
    if not token:
        logger.info("CourtListener token not configured — skipping connector")
        return []

    # Build a deduped query list. Always quote so we get phrase-match precision.
    seen: set[str] = set()
    queries: List[str] = []
    for raw in [competitor_name, *(aliases or [])]:
        if not raw:
            continue
        clean = raw.strip()
        if not clean or clean.lower() in seen:
            continue
        seen.add(clean.lower())
        queries.append(f'"{clean}"')

    cards: List[EvidenceCard] = []
    with make_sync_httpx_client(timeout=30.0) as client:
        for q in queries:
            for qtype in ("r", "o"):  # RECAP dockets, then opinions
                hits = _search(client, token, q, qtype)
                for h in hits:
                    card = _hit_to_card(h, qtype, q.strip('"'))
                    if card:
                        cards.append(card)

    logger.info(
        f"CourtListener: {len(cards)} evidence cards across "
        f"{len(queries)} name(s) for {competitor_name!r}"
    )
    return cards
