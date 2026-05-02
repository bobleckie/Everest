"""Internal documents connector — already-uploaded FOIA / proposal / filing
documents, surfaced as evidence cards.

Reads ``IngestedDocument`` + ``DocumentChunk`` rows tagged to the competitor.
One card per document (not per chunk — chunks would balloon the pool to
thousands of rows). The chunk content is summarized into a 600-char
snippet so the synthesizer has quotable text without re-reading the whole
document. The actual full-content access is still available to category
drafters via the existing ``gather_competitor_context`` helper.
"""
from __future__ import annotations

import logging
from typing import Dict, Iterable, List

from sqlalchemy.orm import Session

from ....models import IngestedDocument, DocumentChunk
from ..evidence_card import EvidenceCard

logger = logging.getLogger(__name__)


# Map the existing IngestedDocument.source_type to a claim_class so uploaded
# docs land in the same buckets as connector-fetched evidence.
_SOURCE_TYPE_TO_CLASS = {
    "competitor_foia": "filing",
    "competitor_proposal": "filing",
    "rfp": "other",
    "reference": "other",
    "parsons": "other",
}


def fetch_for_competitor(db: Session, competitor_id: int) -> List[EvidenceCard]:
    """DB-backed connector — signature differs from the API connectors because
    we pass the live Session in. The orchestrator treats it specially."""
    docs = (
        db.query(IngestedDocument)
        .filter(IngestedDocument.competitor_id == competitor_id)
        .all()
    )
    if not docs:
        return []

    cards: List[EvidenceCard] = []
    for d in docs:
        # Build a snippet from the first 2 chunks so the synthesizer has
        # *some* substance without us shoveling full text into the pool.
        first_chunks = (
            db.query(DocumentChunk)
            .filter(DocumentChunk.document_id == d.id)
            .order_by(DocumentChunk.chunk_index)
            .limit(2)
            .all()
        )
        snippet_text = "\n\n".join(
            (c.content or "")[:300] for c in first_chunks if c.content
        ).strip() or None

        klass = _SOURCE_TYPE_TO_CLASS.get(d.source_type, "other")
        title_bits = [d.original_filename or d.filename or f"Document #{d.id}"]
        if d.source_type:
            title_bits.append(f"[{d.source_type}]")

        cards.append(EvidenceCard(
            source_connector="internal_documents",
            claim_class=klass,
            title=" ".join(title_bits)[:240],
            snippet=snippet_text,
            source_ref=str(d.id),
            citation_url=None,  # internal — UI links to /api/ingestion/documents/{id}
            event_date=(d.created_at.isoformat()[:10] if d.created_at else None),
            confidence="verified",  # uploaded by the user, treated as primary source
            payload={
                "document_id": d.id,
                "source_type": d.source_type,
                "document_type": getattr(d, "document_type", None),
                "total_pages": d.total_pages,
                "total_chunks": d.total_chunks,
            },
        ))

    logger.info(
        f"internal_documents: {len(cards)} cards for competitor_id={competitor_id}"
    )
    return cards


def fetch(competitor_name: str, aliases: Iterable[str], settings: Dict) -> List[EvidenceCard]:
    """Compatibility shim — internal connector should be invoked via
    ``fetch_for_competitor`` instead."""
    raise RuntimeError(
        "internal_documents.fetch() must not be called via the generic API "
        "connector path. Use fetch_for_competitor(db, competitor_id)."
    )
