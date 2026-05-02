"""Wikidata connector — corporate-family discovery via SPARQL.

Wikidata's structured ontology has explicit ``parent organization`` (P749),
``subsidiary`` (P355), ``owner of`` (P1830), ``acquired by`` style links,
``industry`` (P452), ``founded by`` (P112), and ``inception`` (P571)
properties. Walking these edges autonomously surfaces the M&A graph that
the user described — without any human curation.

Strategy:
  1. Search by label to find the company entity (Q…).
  2. SPARQL pull for: parent, subsidiaries, founder, inception, country,
     and any ``acquired`` qualifier on subsidiaries.
  3. One ``filing`` card for the company itself, plus one ``m_and_a`` card
     per parent / subsidiary edge so each link is independently citable.
"""
from __future__ import annotations

import logging
from typing import Dict, Iterable, List, Optional

from ..evidence_card import EvidenceCard
from ...ssl_utils import make_sync_httpx_client

logger = logging.getLogger(__name__)

WD_SEARCH_API = "https://www.wikidata.org/w/api.php"
WD_SPARQL = "https://query.wikidata.org/sparql"

UA = "Parsons RFP Platform (research@parsons.com)"
HEADERS = {"User-Agent": UA, "Accept": "application/json"}

# Properties of interest
P_INSTANCE_OF = "P31"       # instance of (we want company/subsidiary)
COMPANY_QIDS = {            # filter the search to corporate entities
    "Q4830453",  # business
    "Q783794",   # company
    "Q43229",    # organization
    "Q6881511",  # enterprise
    "Q161726",   # multinational corporation
}


def _search_entity(client, label: str) -> Optional[str]:
    """Return the QID of the most likely corporate entity matching ``label``."""
    try:
        r = client.get(WD_SEARCH_API, headers=HEADERS, params={
            "action": "wbsearchentities",
            "search": label,
            "language": "en",
            "type": "item",
            "limit": 5,
            "format": "json",
        })
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Wikidata search failed for {label!r}: {type(e).__name__}: {e}")
        return None
    if r.status_code != 200:
        return None
    hits = (r.json().get("search") or [])
    return hits[0]["id"] if hits else None


_QUERY = """
SELECT ?qid ?qidLabel ?inception ?industryLabel ?countryLabel
       ?parent ?parentLabel ?parentInception
       ?sub ?subLabel ?subInception ?subAcq
WHERE {
  VALUES ?qid { wd:%(QID)s }
  OPTIONAL { ?qid wdt:P571 ?inception. }
  OPTIONAL { ?qid wdt:P452 ?industry. }
  OPTIONAL { ?qid wdt:P17  ?country. }
  OPTIONAL { ?qid wdt:P749 ?parent.
             OPTIONAL { ?parent wdt:P571 ?parentInception. } }
  OPTIONAL { ?qid wdt:P355 ?sub.
             OPTIONAL { ?sub wdt:P571 ?subInception. }
             OPTIONAL { ?qid p:P355 ?subStmt.
                        ?subStmt ps:P355 ?sub.
                        ?subStmt pq:P580 ?subAcq. } }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
LIMIT 100
"""


def _run_sparql(client, qid: str) -> List[Dict]:
    try:
        r = client.get(WD_SPARQL, headers=HEADERS, params={
            "query": _QUERY % {"QID": qid},
            "format": "json",
        })
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Wikidata SPARQL failed for {qid}: {type(e).__name__}: {e}")
        return []
    if r.status_code != 200:
        return []
    try:
        return (r.json().get("results", {}).get("bindings") or [])
    except ValueError:
        return []


def _val(binding: Dict, key: str) -> Optional[str]:
    return (binding.get(key) or {}).get("value")


def _date10(s: Optional[str]) -> Optional[str]:
    return s[:10] if isinstance(s, str) and len(s) >= 10 else s


def _build_cards(qid: str, label: str, rows: List[Dict]) -> List[EvidenceCard]:
    if not rows:
        return []
    first = rows[0]
    cards: List[EvidenceCard] = []
    company_url = f"https://www.wikidata.org/wiki/{qid}"

    # The "self" card — Wikidata's structured profile for the entity.
    snippet_bits = []
    if _val(first, "inception"):
        snippet_bits.append(f"Founded: {_date10(_val(first, 'inception'))}")
    if _val(first, "industryLabel"):
        snippet_bits.append(f"Industry: {_val(first, 'industryLabel')}")
    if _val(first, "countryLabel"):
        snippet_bits.append(f"Country: {_val(first, 'countryLabel')}")
    cards.append(EvidenceCard(
        source_connector="wikidata",
        claim_class="filing",
        title=f"Wikidata profile: {label} ({qid})",
        snippet="\n".join(snippet_bits) or None,
        citation_url=company_url,
        source_ref=qid,
        event_date=_date10(_val(first, "inception")),
        confidence="reported",
        payload={
            "qid": qid,
            "industry": _val(first, "industryLabel"),
            "country": _val(first, "countryLabel"),
        },
    ))

    seen_links: set[str] = set()

    # Parent edges
    for row in rows:
        parent = _val(row, "parent")
        plabel = _val(row, "parentLabel")
        if parent and parent not in seen_links:
            seen_links.add(parent)
            cards.append(EvidenceCard(
                source_connector="wikidata",
                claim_class="m_and_a",
                title=f"Parent organization: {plabel}",
                snippet=f"{label} is a subsidiary of {plabel}",
                citation_url=parent,
                source_ref=parent.rsplit("/", 1)[-1],
                event_date=_date10(_val(row, "parentInception")),
                confidence="reported",
                payload={"relation": "parent", "subject_qid": qid, "object": parent},
            ))

    # Subsidiary / acquisition edges
    for row in rows:
        sub = _val(row, "sub")
        slabel = _val(row, "subLabel")
        sacq = _date10(_val(row, "subAcq"))
        if sub and sub not in seen_links:
            seen_links.add(sub)
            cards.append(EvidenceCard(
                source_connector="wikidata",
                claim_class="m_and_a",
                title=(f"Acquired/owns subsidiary: {slabel}"
                       if sacq else f"Subsidiary: {slabel}"),
                snippet=(f"{label} owns {slabel}" + (f" (acquired {sacq})" if sacq else "")),
                citation_url=sub,
                source_ref=sub.rsplit("/", 1)[-1],
                event_date=sacq or _date10(_val(row, "subInception")),
                confidence="reported",
                payload={"relation": "subsidiary", "subject_qid": qid,
                         "object": sub, "acquired_at": sacq},
            ))

    return cards


def fetch(competitor_name: str, aliases: Iterable[str], settings: Dict) -> List[EvidenceCard]:
    seen_qids: set[str] = set()
    cards: List[EvidenceCard] = []
    with make_sync_httpx_client(timeout=30.0) as client:
        for name in [competitor_name, *(aliases or [])]:
            if not name or not name.strip():
                continue
            qid = _search_entity(client, name.strip())
            if not qid or qid in seen_qids:
                continue
            seen_qids.add(qid)
            rows = _run_sparql(client, qid)
            cards.extend(_build_cards(qid, name.strip(), rows))
    logger.info(f"Wikidata: {len(cards)} cards for {competitor_name!r}")
    return cards
