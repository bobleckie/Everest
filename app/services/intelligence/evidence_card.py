"""Shared in-memory evidence-card dataclass used by every connector.

Connectors return a list of these instead of writing directly to the DB so
that:
  * the orchestrator can dedupe across connectors before persisting,
  * tests don't need a DB to validate connector output,
  * the synthesizer can hold the full pool in memory without re-querying.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional


# Canonical claim_class values. Keep this in sync with the DB column docs.
CLAIM_CLASSES = {
    "litigation",
    "filing",
    "m_and_a",
    "leadership",
    "contract_award",
    "contract_loss",
    "breach",
    "regulatory",
    "review_signal",
    "financial",
    "news",
    "other",
}

CONFIDENCE_LADDER = {"verified", "reported", "inferred", "unverified"}


@dataclass
class EvidenceCard:
    """One atomic factual claim about a competitor, with full provenance.

    Connectors construct these. The orchestrator persists them to
    ``competitor_evidence``. Synthesizer + drafter passes consume them.
    """
    # Required provenance.
    source_connector: str           # e.g. "courtlistener", "sec_edgar"
    claim_class: str                # one of CLAIM_CLASSES; defaults to "other" if unknown
    title: str                      # short headline for chip rendering

    # Optional but strongly preferred.
    citation_url: Optional[str] = None
    snippet: Optional[str] = None
    source_ref: Optional[str] = None  # connector-specific id / docket / ticker
    event_date: Optional[str] = None  # ISO YYYY-MM-DD when the event occurred
    jurisdiction: Optional[str] = None
    amount_usd: Optional[float] = None
    confidence: str = "reported"

    # Free-form payload — connectors stash structured detail here so the
    # synthesizer can introspect (parties to a lawsuit, full SEC filing
    # codes, etc.) without us pre-modeling every shape.
    payload: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:  # validate enums softly
        if self.claim_class not in CLAIM_CLASSES:
            self.claim_class = "other"
        if self.confidence not in CONFIDENCE_LADDER:
            self.confidence = "reported"

    def dedupe_key(self) -> str:
        """Stable key the orchestrator uses to dedupe before persisting.

        Two cards with the same connector + URL (or same connector +
        source_ref when there's no URL) are considered the same fact and
        the second write is skipped.
        """
        anchor = self.citation_url or self.source_ref or self.title
        return f"{self.source_connector}::{anchor}".lower()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
