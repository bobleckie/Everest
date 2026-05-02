"""Google Places connector — review-signal evidence cards.

Wraps the existing ``deep_vendor_research.fetch_google_places_reviews``
helper. We emit one card per *location* (rolling up that location's reviews
into the snippet) plus one summary card with the overall rating across
all locations — both classed as ``review_signal`` so the synthesizer can
spot regional patterns ("all Texas locations <3.0 stars").
"""
from __future__ import annotations

import logging
from typing import Dict, Iterable, List

from ..evidence_card import EvidenceCard
from ...deep_vendor_research import fetch_google_places_reviews

logger = logging.getLogger(__name__)


def _summary_card(name: str, res: Dict) -> EvidenceCard:
    """The roll-up card the synthesizer can quote when describing the
    competitor's customer-facing reputation in aggregate."""
    return EvidenceCard(
        source_connector="google_places",
        claim_class="review_signal",
        title=f"{name} — Google Places footprint",
        snippet=(
            f"Locations indexed: {res.get('locations_found', 0)}\n"
            f"Average rating: {res.get('avg_rating') if res.get('avg_rating') is not None else '—'}\n"
            f"Total reviews across locations: {res.get('total_reviews_across_locations', 0)}"
        ),
        source_ref=f"google_places_summary::{name.lower()}",
        confidence="reported",
        payload={
            "locations_found": res.get("locations_found"),
            "avg_rating": res.get("avg_rating"),
            "total_reviews": res.get("total_reviews_across_locations"),
        },
    )


def _location_card(place: Dict) -> EvidenceCard:
    name = place.get("name") or "(unnamed location)"
    addr = place.get("address") or ""
    rating = place.get("rating")
    total = place.get("total_ratings")
    reviews = place.get("reviews") or []

    title_bits = [name]
    if rating is not None:
        title_bits.append(f"★{rating}")
    if total:
        title_bits.append(f"({total} reviews)")
    title = " ".join(title_bits)

    snippet_lines = []
    if addr:
        snippet_lines.append(addr)
    # Pull up to 3 most-recent review excerpts so the synthesizer has direct
    # quotable evidence; truncate each to 180 chars to keep prompt sane.
    for rv in reviews[:3]:
        line = f"★{rv.get('rating', '?')}: \"{(rv.get('text') or '').strip()[:180]}\""
        if rv.get('relative_time'):
            line += f" — {rv['relative_time']}"
        snippet_lines.append(line)

    return EvidenceCard(
        source_connector="google_places",
        claim_class="review_signal",
        title=title[:240],
        snippet="\n".join(snippet_lines) or None,
        citation_url=place.get("maps_url"),
        source_ref=place.get("maps_url") or name,
        confidence="reported",
        payload={
            "rating": rating,
            "total_ratings": total,
            "address": addr,
            "business_status": place.get("business_status"),
            "types": place.get("types"),
            "reviews": reviews[:5],
        },
    )


def fetch(competitor_name: str, aliases: Iterable[str], settings: Dict) -> List[EvidenceCard]:
    api_key = (settings.get("google_places_api_key") or "").strip()
    if not api_key:
        logger.info("Google Places API key not configured — skipping connector")
        return []

    cards: List[EvidenceCard] = []
    for name in [competitor_name, *(aliases or [])]:
        if not name or not name.strip():
            continue
        try:
            res = fetch_google_places_reviews(name.strip(), api_key=api_key)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Google Places fetch failed for {name!r}: {type(e).__name__}: {e}")
            continue
        if not res.get("available") or not (res.get("places") or []):
            continue
        cards.append(_summary_card(name.strip(), res))
        for place in res.get("places") or []:
            cards.append(_location_card(place))
    logger.info(f"Google Places: {len(cards)} cards for {competitor_name!r}")
    return cards
