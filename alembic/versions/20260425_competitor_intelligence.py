"""competitor intelligence: evidence, threads, timeline events

Revision ID: 20260425_competitor_intelligence
Revises: 20260424_response_workbench
Create Date: 2026-04-25

These three tables back the unified competitor-intelligence pipeline. They
replace the previous "single opaque markdown blob per category" pattern with
a normalized evidence pool that every category and every synthesized thread
can cite.

Schema rationale:
  * ``competitor_evidence`` is the deduped pool of facts the system has
    learned about a competitor — one row per claim, each with provenance
    (URL, source connector, optional document/news pointer) so categories
    and threads cite by ``evidence_id`` instead of free-text.
  * ``competitor_threads`` are the cross-category narratives ("acquire-the-
    winner playbook", "change-order strategy", "M&A-inherited cyber risk")
    surfaced by the synthesizer pass. Each thread carries the evidence_ids
    that prove it and the category_tags it should appear under.
  * ``competitor_timeline_events`` is the chronological skeleton — M&A
    closings, contract awards, lawsuit filings, leadership changes — used
    by the new Timeline tab.
"""
from alembic import op
import sqlalchemy as sa


revision = "20260425_competitor_intelligence"
down_revision = "20260424_response_workbench"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── competitor_evidence ─────────────────────────────────────────
    # One row per atomic factual claim about a competitor, with provenance.
    # The unified intelligence run produces these and every downstream pass
    # (threads, categories, fact-check) cites by id.
    op.create_table(
        "competitor_evidence",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("competitor_id", sa.Integer,
                  sa.ForeignKey("competitors.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        # Connector that produced this row (courtlistener | sec_edgar |
        # opencorporates | gleif | wikidata | google_places | uk_contracts_finder
        # | anthropic_web_search | uploaded_document | competitor_news).
        sa.Column("source_connector", sa.String, nullable=False, index=True),
        # Free-text source identifier (court id, SEC ticker, URL, doc filename).
        sa.Column("source_ref", sa.String, nullable=True),
        # Canonical URL we can show the user as the citation.
        sa.Column("citation_url", sa.String, nullable=True),
        # Short title/snippet for chip rendering.
        sa.Column("title", sa.String, nullable=True),
        # Full snippet / quote we extracted (kept short — full doc lives elsewhere).
        sa.Column("snippet", sa.Text, nullable=True),
        # Class buckets: "litigation" | "filing" | "m_and_a" | "leadership" |
        # "contract_award" | "contract_loss" | "breach" | "regulatory" |
        # "review_signal" | "financial" | "news" | "other"
        sa.Column("claim_class", sa.String, nullable=False, index=True, default="other"),
        # ISO date the underlying event occurred (NOT when we fetched it).
        # Stored as String to avoid timezone parsing pain across connectors.
        sa.Column("event_date", sa.String, nullable=True),
        # Optional jurisdiction (e.g. "AZ", "NJ", "UK") to power timeline filtering.
        sa.Column("jurisdiction", sa.String, nullable=True),
        # Optional dollar amount when the claim is financial / contract-value.
        sa.Column("amount_usd", sa.Float, nullable=True),
        # Free JSON payload — connectors can stash structured detail
        # (parties to a lawsuit, full SEC filing item codes, etc.) that
        # the synthesizer can introspect without us pre-modeling everything.
        sa.Column("payload_json", sa.Text, nullable=True),
        # Confidence ladder: "verified" | "reported" | "inferred" | "unverified".
        sa.Column("confidence", sa.String, nullable=False, default="reported"),
        sa.Column("fetched_at", sa.DateTime, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index(
        "ix_competitor_evidence_class_date",
        "competitor_evidence",
        ["competitor_id", "claim_class", "event_date"],
    )

    # ── competitor_threads ───────────────────────────────────────────
    # Cross-category narratives produced by the synthesizer. A thread links
    # a set of evidence rows into a story (e.g. acquire-the-winner pattern
    # tying together a contract loss + a subsequent acquisition).
    op.create_table(
        "competitor_threads",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("competitor_id", sa.Integer,
                  sa.ForeignKey("competitors.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        # Slug like "acquire_the_winner" — stable across re-runs so the UI
        # can deep-link to a thread.
        sa.Column("slug", sa.String, nullable=False, index=True),
        sa.Column("title", sa.String, nullable=False),
        # One-line decision-useful summary.
        sa.Column("headline", sa.Text, nullable=True),
        # Full narrative, markdown, citing evidence ids inline as
        # ``[ev:42]`` / ``[ev:51]`` placeholders rendered into chips by the UI.
        sa.Column("narrative_markdown", sa.Text, nullable=True),
        # JSON array of evidence ids supporting the thread.
        sa.Column("evidence_ids_json", sa.Text, nullable=False, default="[]"),
        # JSON array of dossier-category slugs the thread should surface in
        # ("weaknesses", "strategy", etc.).
        sa.Column("category_tags_json", sa.Text, nullable=False, default="[]"),
        # "high" | "medium" | "low" — synthesizer-assigned confidence.
        sa.Column("confidence", sa.String, nullable=False, default="medium"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index(
        "ix_competitor_threads_unique_slug",
        "competitor_threads",
        ["competitor_id", "slug"],
        unique=True,
    )

    # ── competitor_timeline_events ───────────────────────────────────
    # Chronological events for the Timeline tab. Each event MAY be linked
    # to multiple evidence rows (a single M&A close is often documented in
    # an 8-K and a press release).
    op.create_table(
        "competitor_timeline_events",
        sa.Column("id", sa.Integer, primary_key=True, index=True),
        sa.Column("competitor_id", sa.Integer,
                  sa.ForeignKey("competitors.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        # ISO date — events without a date are dropped from the timeline.
        sa.Column("event_date", sa.String, nullable=False, index=True),
        # "m_and_a" | "contract_award" | "contract_loss" | "lawsuit_filed" |
        # "lawsuit_resolved" | "regulatory_action" | "leadership_change" |
        # "breach" | "financial" | "press" | "other"
        sa.Column("event_type", sa.String, nullable=False, index=True),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("jurisdiction", sa.String, nullable=True),
        sa.Column("amount_usd", sa.Float, nullable=True),
        # JSON array of evidence ids.
        sa.Column("evidence_ids_json", sa.Text, nullable=False, default="[]"),
        # Optional thread id this event belongs to (a "Acquire-the-winner"
        # thread groups its events together).
        sa.Column("thread_id", sa.Integer,
                  sa.ForeignKey("competitor_threads.id", ondelete="SET NULL"),
                  nullable=True, index=True),
        sa.Column("confidence", sa.String, nullable=False, default="reported"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("competitor_timeline_events")
    op.drop_index("ix_competitor_threads_unique_slug",
                  table_name="competitor_threads")
    op.drop_table("competitor_threads")
    op.drop_index("ix_competitor_evidence_class_date",
                  table_name="competitor_evidence")
    op.drop_table("competitor_evidence")
