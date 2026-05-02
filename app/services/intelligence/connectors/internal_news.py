"""Internal news connector — CompetitorNewsItem rows surfaced as ``news``
evidence cards.

The news scheduler already runs daily and populates the ``competitor_news``
table. This connector simply makes those items first-class citations in
the unified evidence pool so the synthesizer doesn't have to special-case
"news" vs "everything else".
"""
from __future__ import annotations

import logging
from typing import Dict, Iterable, List

from sqlalchemy.orm import Session

from ....models import CompetitorNewsItem
from ..evidence_card import EvidenceCard

logger = logging.getLogger(__name__)


def fetch_for_competitor(db: Session, competitor_id: int, limit: int = 100) -> List[EvidenceCard]:
    rows = (
        db.query(CompetitorNewsItem)
        .filter(CompetitorNewsItem.competitor_id == competitor_id)
        .order_by(CompetitorNewsItem.published_at.desc().nullslast())
        .limit(limit)
        .all()
    )
    cards: List[EvidenceCard] = []
    for n in rows:
        date10 = n.published_at.isoformat()[:10] if n.published_at else None
        cards.append(EvidenceCard(
            source_connector="internal_news",
            claim_class="news",
            title=(n.title or "(untitled)")[:240],
            snippet=(n.summary or "")[:1000].strip() or None,
            citation_url=n.url,
            source_ref=str(n.id),
            event_date=date10,
            confidence="reported",
            payload={
                "news_id": n.id,
                "source": n.source,
                "fetched_at": n.fetched_at.isoformat() if getattr(n, "fetched_at", None) else None,
            },
        ))
    logger.info(f"internal_news: {len(cards)} cards for competitor_id={competitor_id}")
    return cards


def fetch(competitor_name: str, aliases: Iterable[str], settings: Dict) -> List[EvidenceCard]:
    raise RuntimeError(
        "internal_news.fetch() must not be called via the generic API "
        "connector path. Use fetch_for_competitor(db, competitor_id)."
    )
