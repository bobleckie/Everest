"""
Deep vendor research service.

This module upgrades the old single-shot `ai_agent.competitive_research()` call into
an iterative, evidence-grounded research pipeline driven by Claude Opus 4.7.

Pipeline (serial passes, each one narrows the question):

  Pass 1 — Entity resolution
    • Expand the vendor name into its corporate family: legal entity, DBAs,
      parent company, ultimate parent, material subsidiaries, ticker symbol.
    • We never want to analyze only the surface brand — e.g. "Opus" today might
      be owned by PE firm A, but last year it was owned by PE firm B, and the
      news that matters is usually filed under the owner's name.

  Pass 2 — Ownership history (deterministic sources first, then LLM)
    • SEC EDGAR: 10-K / 10-Q / 8-K / DEF 14A filings for any ticker we resolved.
    • OpenCorporates: corporate registry records (free tier, global).
    • GLEIF: LEI → parent / ultimate-parent relationships.
    • Wikidata: acquisitions, subsidiaries, CEO/founder history.
    • The structured data from these APIs is fed into Pass 3.

  Pass 3 — Deep web research (Anthropic native `web_search` tool)
    • Claude Opus 4.7 is given the structured data from Pass 2 plus a set of
      targeted questions (PE owners over time, financial distress, M&A chatter,
      leadership turnover, regulatory actions, lawsuits, recompetes, losses).
    • Claude autonomously runs up to `max_searches` web_search calls per turn
      and cites every finding with a URL.

  Pass 4 — Synthesis
    • Final Opus 4.7 call merges the Pass-2 structured data, the Pass-3 cited
      findings, and any already-ingested documents/news from the DB into a
      decision-useful capture brief with explicit evidence tags and a gaps
      list.

The result is returned as a structured dict so the UI can render each section
independently rather than dumping one giant markdown blob.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from ..models import AppSetting, Competitor, CompetitorNewsItem, IngestedDocument, DocumentChunk
from .ssl_utils import make_sync_httpx_client

logger = logging.getLogger(__name__)

# The research loop is expensive; we cap tool-use turns so a single request
# can't spiral. Claude decides when it has enough — it usually converges in
# 4-8 web_search calls per pass.
MAX_TOOL_TURNS = 12
MAX_SEARCHES_PER_TURN = 5

DEFAULT_MODEL = os.getenv("CLAUDE_RESEARCH_MODEL", "claude-opus-4-7")


# ─────────────────────────────────────────────────────────────────────
# Settings / credentials
# ─────────────────────────────────────────────────────────────────────

def _load_ai_settings() -> Dict[str, str]:
    """Resolve provider keys the same way the rest of the app does (DB first, env fallback).

    We also surface ``google_places_api_key`` here so the Places lookup can be
    configured from Settings → AI without needing an env var or a restart.
    """
    from ..database import SessionLocal

    result = {
        "llm_provider": os.getenv("LLM_PROVIDER", "anthropic"),
        "openai_api_key": os.getenv("OPENAI_API_KEY", ""),
        "anthropic_api_key": os.getenv("ANTHROPIC_API_KEY", ""),
        "google_places_api_key": os.getenv("GOOGLE_PLACES_API_KEY", ""),
    }
    try:
        s = SessionLocal()
        try:
            for row in s.query(AppSetting).all():
                try:
                    val = json.loads(row.value_json) if row.value_json else ""
                except (json.JSONDecodeError, TypeError):
                    val = row.value_json or ""
                if row.key in result and val:
                    result[row.key] = val
        finally:
            s.close()
    except Exception as e:
        logger.debug(f"Could not load AI settings from DB: {e}")
    return result


# ─────────────────────────────────────────────────────────────────────
# Free / deterministic structured-data sources
#
# These are intentionally tiny wrappers. We do not boil the ocean — each one
# returns "best-effort, possibly empty" structured data and failures are
# swallowed so the LLM step never blocks on an external outage.
# ─────────────────────────────────────────────────────────────────────

def _http_get_json(url: str, headers: Optional[dict] = None, timeout: float = 15.0) -> Optional[dict]:
    """Tiny JSON HTTP GET with sensible defaults and silent failure on error."""
    try:
        req = urllib.request.Request(url, headers=headers or {"User-Agent": "EverestRFP/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))
    except Exception as e:  # noqa: BLE001 — each source is best-effort
        logger.info(f"structured source fetch failed ({url[:80]}…): {type(e).__name__}")
        return None


def fetch_sec_edgar(vendor_name: str) -> Dict[str, Any]:
    """Best-effort EDGAR ticker + latest-filings lookup.

    Uses the free EDGAR JSON endpoints. Returns a dict with `ticker`, `cik`,
    and up to 10 recent filings so the LLM can ask follow-up questions about
    specific 10-Ks / 8-Ks / DEF 14As.
    """
    # Step 1 — ticker lookup from the public ticker list
    tickers = _http_get_json("https://www.sec.gov/files/company_tickers.json",
                              headers={"User-Agent": "EverestRFP research@parsons.com"})
    if not tickers:
        return {"source": "sec_edgar", "available": False, "reason": "ticker list unavailable"}

    needle = vendor_name.lower().strip()
    match = None
    for _, row in tickers.items():
        title = (row.get("title") or "").lower()
        if needle == title or needle in title or title in needle:
            match = row
            break

    if not match:
        return {"source": "sec_edgar", "available": False, "reason": "no ticker match"}

    cik = str(match.get("cik_str", "")).zfill(10)
    ticker = match.get("ticker")

    # Step 2 — pull the most recent submissions
    submissions = _http_get_json(
        f"https://data.sec.gov/submissions/CIK{cik}.json",
        headers={"User-Agent": "EverestRFP research@parsons.com"},
    )
    filings: List[dict] = []
    if submissions:
        recent = (submissions.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        dates = recent.get("filingDate") or []
        accns = recent.get("accessionNumber") or []
        for i in range(min(len(forms), 10)):
            accn = (accns[i] or "").replace("-", "")
            filings.append({
                "form": forms[i],
                "filed": dates[i] if i < len(dates) else None,
                "url": (
                    f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
                    f"&CIK={cik}&type={forms[i]}&dateb=&owner=include&count=10"
                ) if not accn else (
                    f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn}/"
                ),
            })

    return {
        "source": "sec_edgar",
        "available": True,
        "ticker": ticker,
        "cik": cik,
        "legal_name": submissions.get("name") if submissions else match.get("title"),
        "former_names": [fn.get("name") for fn in (submissions.get("formerNames") or [])] if submissions else [],
        "sic": submissions.get("sic") if submissions else None,
        "recent_filings": filings,
    }


def fetch_opencorporates(vendor_name: str) -> Dict[str, Any]:
    """Best-effort OpenCorporates search (free tier, rate-limited)."""
    q = urllib.parse.quote_plus(vendor_name)
    api_key = os.getenv("OPENCORPORATES_API_TOKEN", "")
    url = f"https://api.opencorporates.com/v0.4/companies/search?q={q}&per_page=5"
    if api_key:
        url += f"&api_token={api_key}"
    data = _http_get_json(url)
    if not data:
        return {"source": "opencorporates", "available": False}
    try:
        companies = [
            {
                "name": c["company"].get("name"),
                "jurisdiction": c["company"].get("jurisdiction_code"),
                "number": c["company"].get("company_number"),
                "status": c["company"].get("current_status"),
                "incorporated": c["company"].get("incorporation_date"),
                "type": c["company"].get("company_type"),
                "url": c["company"].get("opencorporates_url"),
            }
            for c in ((data.get("results") or {}).get("companies") or [])
        ]
    except Exception:
        companies = []
    return {"source": "opencorporates", "available": True, "companies": companies}


def fetch_gleif(vendor_name: str) -> Dict[str, Any]:
    """Best-effort GLEIF Legal Entity Identifier (LEI) + parent chain lookup (free)."""
    q = urllib.parse.quote_plus(vendor_name)
    data = _http_get_json(
        f"https://api.gleif.org/api/v1/lei-records?filter[entity.legalName]={q}&page[size]=5"
    )
    if not data:
        return {"source": "gleif", "available": False}
    records = []
    for item in (data.get("data") or []):
        attrs = item.get("attributes") or {}
        ent = attrs.get("entity") or {}
        records.append({
            "lei": attrs.get("lei"),
            "legal_name": (ent.get("legalName") or {}).get("name"),
            "jurisdiction": ent.get("jurisdiction"),
            "category": ent.get("category"),
            "status": ent.get("status"),
            "parent_lei": ((ent.get("associatedEntity") or {}).get("lei")),
        })
    return {"source": "gleif", "available": True, "records": records}


def fetch_google_places_reviews(
    vendor_name: str,
    *,
    max_places: int = 10,
    max_reviews_per_place: int = 5,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Best-effort Google Places reviews lookup.

    Many vendors (e.g. correctional facility operators, transit contractors, school-
    bus operators) run public-facing sites whose reputation is captured in Google
    reviews. We enumerate up to `max_places` locations via Text Search then pull
    Place Details for each to get rating + up to 5 top reviews.

    Gated on `GOOGLE_PLACES_API_KEY`. Returns `available=False` when absent, so the
    downstream LLM prompt can simply note the gap instead of erroring.

    NOTE: We intentionally use only the official API — Google TOS forbids scraping
    reviews beyond what the API returns, and the API caps at ~5 reviews per place.
    """
    # Caller may pass the key explicitly (resolved from AppSetting by the pipeline);
    # fall back to env var so one-off callers still work.
    api_key = (api_key or "").strip() or os.getenv("GOOGLE_PLACES_API_KEY", "")
    if not api_key:
        return {"source": "google_places", "available": False, "reason": "GOOGLE_PLACES_API_KEY not configured"}

    q = urllib.parse.quote_plus(vendor_name)
    search_url = (
        f"https://maps.googleapis.com/maps/api/place/textsearch/json?query={q}&key={api_key}"
    )
    search = _http_get_json(search_url, timeout=20.0)
    if not search or search.get("status") not in ("OK", "ZERO_RESULTS"):
        return {
            "source": "google_places",
            "available": False,
            "reason": f"textsearch status={search.get('status') if search else 'unreachable'}",
        }

    places: List[dict] = []
    for row in (search.get("results") or [])[:max_places]:
        place_id = row.get("place_id")
        if not place_id:
            continue
        details_url = (
            "https://maps.googleapis.com/maps/api/place/details/json"
            f"?place_id={place_id}"
            "&fields=name,formatted_address,rating,user_ratings_total,reviews,business_status,types,url"
            f"&key={api_key}"
        )
        details = _http_get_json(details_url, timeout=20.0)
        if not details or details.get("status") != "OK":
            continue
        d = details.get("result") or {}
        reviews_out = []
        for rv in (d.get("reviews") or [])[:max_reviews_per_place]:
            reviews_out.append({
                "author": rv.get("author_name"),
                "rating": rv.get("rating"),
                "relative_time": rv.get("relative_time_description"),
                "text": (rv.get("text") or "")[:800],  # cap to keep prompt size sane
                "time": rv.get("time"),
            })
        places.append({
            "name": d.get("name"),
            "address": d.get("formatted_address"),
            "rating": d.get("rating"),
            "total_ratings": d.get("user_ratings_total"),
            "business_status": d.get("business_status"),
            "types": d.get("types"),
            "maps_url": d.get("url"),
            "reviews": reviews_out,
        })

    # Roll-up stats help the LLM spot patterns (e.g. "all Texas locations <3.0 stars")
    if places:
        ratings = [p["rating"] for p in places if p.get("rating") is not None]
        total_counts = [p["total_ratings"] for p in places if p.get("total_ratings") is not None]
        avg_rating = round(sum(ratings) / len(ratings), 2) if ratings else None
        total_reviews = sum(total_counts) if total_counts else 0
    else:
        avg_rating = None
        total_reviews = 0

    return {
        "source": "google_places",
        "available": True,
        "query": vendor_name,
        "locations_found": len(places),
        "avg_rating": avg_rating,
        "total_reviews_across_locations": total_reviews,
        "places": places,
    }


def fetch_internal_context(db: Session, competitor_id: Optional[int]) -> Dict[str, Any]:
    """Pull already-ingested docs + recent news from our own DB so the LLM can reference them."""
    if competitor_id is None:
        return {"source": "internal", "available": False, "reason": "no competitor_id"}

    comp = db.query(Competitor).filter(Competitor.id == competitor_id).first()
    if not comp:
        return {"source": "internal", "available": False, "reason": "competitor not found"}

    # Lightweight document summary — titles + chunk count, not full content (keeps prompt small)
    docs = db.query(IngestedDocument).filter(IngestedDocument.competitor_id == competitor_id).all()
    doc_summary = []
    for d in docs:
        n_chunks = db.query(DocumentChunk).filter(DocumentChunk.document_id == d.id).count()
        doc_summary.append({
            "id": d.id,
            "filename": d.original_filename,
            "source_type": d.source_type,
            "chunks": n_chunks,
            "uploaded": d.created_at.isoformat() if d.created_at else None,
        })

    news = (
        db.query(CompetitorNewsItem)
        .filter(CompetitorNewsItem.competitor_id == competitor_id)
        .order_by(CompetitorNewsItem.fetched_at.desc())
        .limit(20)
        .all()
    )
    news_items = [
        {
            "title": n.title,
            "source": n.source,
            "url": n.url,
            "published": n.published_at.isoformat() if n.published_at else None,
        }
        for n in news
    ]

    return {
        "source": "internal",
        "available": True,
        "competitor_name": comp.name,
        "competitor_id": comp.id,
        "documents": doc_summary,
        "news": news_items,
    }


# ─────────────────────────────────────────────────────────────────────
# Claude Opus 4.7 research driver
# ─────────────────────────────────────────────────────────────────────

@dataclass
class ResearchTurn:
    """One Claude turn in the research loop, captured for the audit trail."""
    role: str
    summary: str
    tool_calls: List[dict] = field(default_factory=list)


@dataclass
class DeepResearchResult:
    vendor_name: str
    model: str
    started_at: str
    completed_at: Optional[str] = None
    entity_profile: Dict[str, Any] = field(default_factory=dict)
    structured_sources: Dict[str, Any] = field(default_factory=dict)
    findings: Dict[str, Any] = field(default_factory=dict)
    citations: List[dict] = field(default_factory=list)
    synthesis_markdown: str = ""
    audit_trail: List[ResearchTurn] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "vendor_name": self.vendor_name,
            "model": self.model,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "entity_profile": self.entity_profile,
            "structured_sources": self.structured_sources,
            "findings": self.findings,
            "citations": self.citations,
            "synthesis_markdown": self.synthesis_markdown,
            "audit_trail": [
                {"role": t.role, "summary": t.summary, "tool_calls": t.tool_calls}
                for t in self.audit_trail
            ],
            "errors": self.errors,
        }


def _extract_text_from_content(blocks: Any) -> str:
    """Anthropic response content is a list of typed blocks; stitch together text-type blocks."""
    parts: List[str] = []
    for b in (blocks or []):
        btype = getattr(b, "type", None)
        if btype == "text":
            t = getattr(b, "text", "") or ""
            if t:
                parts.append(t)
    return "\n".join(parts).strip()


def _extract_citations(blocks: Any) -> List[dict]:
    """Pull URL citations out of web_search_tool_result / text blocks."""
    cites: List[dict] = []
    for b in (blocks or []):
        btype = getattr(b, "type", None)
        if btype == "text":
            for c in (getattr(b, "citations", None) or []):
                url = getattr(c, "url", None)
                if not url:
                    continue
                cites.append({
                    "url": url,
                    "title": getattr(c, "title", None),
                    "cited_text": getattr(c, "cited_text", None),
                })
        elif btype == "web_search_tool_result":
            content = getattr(b, "content", None) or []
            for item in content:
                url = getattr(item, "url", None)
                if not url:
                    continue
                cites.append({
                    "url": url,
                    "title": getattr(item, "title", None),
                    "cited_text": None,
                })
    # dedupe by URL, preserving order
    seen = set()
    uniq: List[dict] = []
    for c in cites:
        if c["url"] in seen:
            continue
        seen.add(c["url"])
        uniq.append(c)
    return uniq


def _run_opus_with_search(
    anthropic_key: str,
    model: str,
    system: str,
    user_content: str,
    *,
    max_searches: int = MAX_SEARCHES_PER_TURN,
    max_tokens: int = 4000,
    timeout: float = 600.0,
) -> Dict[str, Any]:
    """Run a single Claude Opus turn with the native `web_search` tool enabled.

    Returns a dict: `{text, citations, stop_reason, raw}`. The Anthropic SDK handles
    the tool-use loop server-side when `web_search_20250305` is the only tool, so one
    SDK call yields one synthesized text answer plus all citations used along the way.
    """
    import anthropic

    client = anthropic.Anthropic(
        api_key=anthropic_key,
        http_client=make_sync_httpx_client(timeout=timeout),
        timeout=timeout,
        max_retries=1,
    )

    tools = [
        {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": max_searches,
        }
    ]

    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        tools=tools,
        messages=[{"role": "user", "content": user_content}],
    )
    return {
        "text": _extract_text_from_content(resp.content),
        "citations": _extract_citations(resp.content),
        "stop_reason": getattr(resp, "stop_reason", None),
        "model": getattr(resp, "model", model),
    }


# ─────────────────────────────────────────────────────────────────────
# Prompts — kept here so they're easy to iterate on as we refine the agent
# ─────────────────────────────────────────────────────────────────────

SYSTEM_SENIOR_ANALYST = (
    "You are a senior competitive intelligence analyst supporting Parsons Corporation on federal, "
    "state, and municipal RFP captures. Your job is decision-useful, evidence-tagged research — "
    "never plausible-sounding narrative. Every factual claim must be tied to a source (URL, SEC "
    "filing, registry record, or the structured data block provided to you). If evidence is absent, "
    "say so explicitly and add it to a 'Collection Gaps' list instead of guessing. "
    "Follow the ownership chain aggressively: when a company is PE-owned, the useful news often "
    "appears under the parent, the fund, or former entity names — expand searches to cover them."
)


def _entity_resolution_prompt(vendor_name: str, structured: Dict[str, Any]) -> str:
    return f"""# Entity resolution — {vendor_name}

Using the structured registry data below AND targeted web searches, return ONLY a JSON object
(no prose, no markdown fences) with this exact schema:

{{
  "canonical_legal_name": "string",
  "aliases": ["string", ...],
  "former_names": ["string", ...],
  "ticker": "string or null",
  "cik": "string or null",
  "lei": "string or null",
  "parent_company": "string or null",
  "ultimate_parent": "string or null",
  "ownership_type": "public | private | pe_backed | subsidiary | nonprofit | government | unknown",
  "known_owners_current": ["string", ...],
  "known_owners_historical": [{{"owner": "string", "period": "string", "source_hint": "string"}}],
  "material_subsidiaries": ["string", ...],
  "hq_location": "string or null",
  "industry_sic_or_naics": "string or null",
  "entity_resolution_confidence": "high | medium | low",
  "notes": "string — anything that matters but doesn't fit above"
}}

Structured data from our deterministic sources:
```json
{json.dumps(structured, indent=2, default=str)}
```

Run up to {MAX_SEARCHES_PER_TURN} web searches to fill gaps — especially ownership history,
parent / ultimate-parent, and former names. If you truly cannot confirm a field, set it to
null (or an empty array) — do not fabricate."""


def _deep_research_prompt(
    vendor_name: str,
    entity: Dict[str, Any],
    internal: Dict[str, Any],
    google_places: Dict[str, Any],
) -> str:
    aliases = entity.get("aliases") or []
    former = entity.get("former_names") or []
    owners_hist = entity.get("known_owners_historical") or []
    parent = entity.get("parent_company") or ""
    ultimate = entity.get("ultimate_parent") or ""

    search_targets = [vendor_name] + aliases + former + [parent, ultimate]
    search_targets += [o.get("owner", "") for o in owners_hist if isinstance(o, dict)]
    search_targets = [t for t in search_targets if t]

    return f"""# Deep competitive research — {vendor_name}

You have confirmed entity context:
```json
{json.dumps(entity, indent=2, default=str)}
```

Internal context already on file (ingested documents + news already captured by Parsons):
```json
{json.dumps(internal, indent=2, default=str)}
```

Public reputation data (Google Places — operating locations + reviews across states).
If `available=false`, skip the `public_reputation` block in your output; otherwise, look for
state/region-level patterns, repeated complaint themes, and material sentiment shifts:
```json
{json.dumps(google_places, indent=2, default=str)}
```

## Your research assignment

Run an aggressive, multi-angle web investigation. Search under ALL of these entity names
(current, former, parents, PE owners) — news about a company's financials or strategic
direction often lives under the owner's name, not the brand we know:

{json.dumps(search_targets, indent=2)}

Cover these angles and return a single JSON object (no prose before or after) with this schema:

{{
  "ownership_history": [
    {{"period": "YYYY-YYYY or YYYY-present", "owner": "string", "type": "pe|public|private|subsidiary", "notes": "string", "sources": ["url", ...]}}
  ],
  "financial_signals": [
    {{"date": "YYYY-MM-DD or YYYY", "signal": "string", "category": "debt|revenue|earnings|impairment|ratings|liquidity|ma|ipo|delisting|other", "severity": "high|medium|low", "sources": ["url", ...]}}
  ],
  "leadership_changes": [
    {{"date": "YYYY-MM-DD or YYYY", "person": "string", "role": "string", "change": "appointed|departed|promoted|ousted|deceased|other", "sources": ["url", ...]}}
  ],
  "contracts_won_lost": [
    {{"date": "YYYY or YYYY-MM", "customer": "string", "contract": "string", "outcome": "won|lost|protested|terminated|renewed", "value": "string or null", "sources": ["url", ...]}}
  ],
  "regulatory_or_legal": [
    {{"date": "YYYY or YYYY-MM-DD", "action": "string", "agency_or_court": "string", "severity": "high|medium|low", "sources": ["url", ...]}}
  ],
  "strategic_moves": [
    {{"date": "YYYY or YYYY-MM", "move": "string", "category": "acquisition|divestiture|partnership|product|geography|other", "sources": ["url", ...]}}
  ],
  "risks_for_parsons": [
    {{"risk": "string", "likelihood": "high|medium|low", "impact_on_bid": "string", "sources": ["url", ...]}}
  ],
  "opportunities_for_parsons": [
    {{"opportunity": "string", "rationale": "string", "sources": ["url", ...]}}
  ],
  "public_reputation": {{
    "overall_avg_rating": "number or null",
    "total_reviews_observed": "number or null",
    "state_or_region_patterns": [
      {{"region": "string (state or region)", "avg_rating": "number", "sample_size": "number", "dominant_themes": ["string", ...], "sources": ["url", ...]}}
    ],
    "recurring_complaints": [
      {{"theme": "string", "frequency": "high|medium|low", "operational_implication": "string", "sources": ["url", ...]}}
    ],
    "recurring_praise": [
      {{"theme": "string", "frequency": "high|medium|low", "sources": ["url", ...]}}
    ],
    "notes": "string"
  }},
  "collection_gaps": ["string — an evidence gap that materially affects our read", ...]
}}

Rules:
1. Every entry MUST have at least one source URL. If you cannot source it, omit it.
2. Prefer primary sources: SEC filings, federal-register notices, court dockets, press
   releases, earnings transcripts. Secondary sources (trade press, Reuters, Bloomberg,
   regional business journals) are acceptable when primaries are not public.
3. Do not repeat internal-context news items verbatim — only include them if you
   independently corroborated or updated them.
4. Use up to {MAX_SEARCHES_PER_TURN} web searches. Be surgical: each search should
   target a specific entity + topic."""


def _synthesis_prompt(vendor_name: str, entity: Dict[str, Any], findings: Dict[str, Any]) -> str:
    return f"""# Capture brief — {vendor_name}

Write a senior-analyst capture brief in Markdown using the evidence below. This brief will be
read by a capture manager deciding bid strategy — be dense, cited, and honest.

Entity context:
```json
{json.dumps(entity, indent=2, default=str)}
```

Evidence from deep research:
```json
{json.dumps(findings, indent=2, default=str)}
```

## Structure

1. **TL;DR** — 4-6 bullets of the most decision-useful findings. Each bullet must cite a source.
2. **Corporate family & ownership timeline** — narrative of who has owned this company when,
   with inline URL citations like `[source](url)`.
3. **Financial health signals** — what the numbers + market say, most recent first.
4. **Leadership & strategic direction** — turnover, announced plays, public statements.
5. **Public reputation & operating footprint** — if Google Places data was available in the
   evidence JSON, summarize state/region-level rating patterns, the 2-3 most common complaint
   themes, and how those themes map to the services Parsons would need to deliver under the
   same contracts. Cite specific `maps.google.com` or review URLs where possible. If no public
   reputation data was captured, write "No public reputation data captured (no `GOOGLE_PLACES_API_KEY` "
   "configured or no locations matched)." and move on.
6. **Competitive posture vs. Parsons** — where they win, where they lose, what Parsons should
   emphasize / counter in this specific competitive context.
7. **Risks & opportunities** — explicit lists, each item with a source.
8. **Collection gaps** — what we still need to know and the exact source to get it from (FOIA
   target, specific SEC filing, specific state registry, etc.)

Rules:
- Every factual sentence ends with an inline Markdown citation `[source](url)`. No exceptions.
- Tag confidence where it isn't obvious: `(high)`, `(medium)`, `(low)`.
- If a section has no evidence, write "No evidence in provided sources." and move on. Do not
  fabricate to fill space.
- Do not include the raw JSON evidence — this is a written brief."""


# ─────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────

def _safe_parse_json(text: str) -> Optional[dict]:
    """Parse JSON out of an LLM response — tolerant of ```json fences and surrounding prose."""
    if not text:
        return None
    # Strip markdown code fences
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    # Find the first { and the matching last }
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    candidate = t[start : end + 1]
    try:
        return json.loads(candidate)
    except Exception:
        return None


def run_deep_vendor_research(
    db: Session,
    vendor_name: str,
    *,
    competitor_id: Optional[int] = None,
    model: Optional[str] = None,
) -> DeepResearchResult:
    """Full pipeline. Returns a DeepResearchResult dataclass; caller may persist `to_dict()`."""
    cfg = _load_ai_settings()
    anthropic_key = cfg.get("anthropic_api_key") or ""
    resolved_model = model or DEFAULT_MODEL

    result = DeepResearchResult(
        vendor_name=vendor_name,
        model=resolved_model,
        started_at=datetime.utcnow().isoformat() + "Z",
    )

    if not anthropic_key:
        result.errors.append(
            "No Anthropic API key configured. Deep research requires Claude Opus 4.7 — "
            "add ANTHROPIC_API_KEY in Settings → AI or as an environment variable."
        )
        result.completed_at = datetime.utcnow().isoformat() + "Z"
        return result

    # ── Pass 0 — Structured deterministic sources ────────────────────
    google_places_key = cfg.get("google_places_api_key") or ""
    structured = {
        "sec_edgar": fetch_sec_edgar(vendor_name),
        "opencorporates": fetch_opencorporates(vendor_name),
        "gleif": fetch_gleif(vendor_name),
        "google_places": fetch_google_places_reviews(vendor_name, api_key=google_places_key),
        "internal": fetch_internal_context(db, competitor_id),
    }
    result.structured_sources = structured
    result.audit_trail.append(ResearchTurn(
        role="system",
        summary=(
            f"Fetched deterministic sources: "
            f"SEC={structured['sec_edgar'].get('available')}, "
            f"OC={structured['opencorporates'].get('available')}, "
            f"GLEIF={structured['gleif'].get('available')}, "
            f"GPlaces={structured['google_places'].get('available')} "
            f"(locations={structured['google_places'].get('locations_found', 0)}), "
            f"Internal={structured['internal'].get('available')}"
        ),
    ))

    # ── Pass 1 — Entity resolution ───────────────────────────────────
    try:
        turn1 = _run_opus_with_search(
            anthropic_key=anthropic_key,
            model=resolved_model,
            system=SYSTEM_SENIOR_ANALYST,
            user_content=_entity_resolution_prompt(vendor_name, structured),
            max_searches=MAX_SEARCHES_PER_TURN,
            max_tokens=2000,
        )
        entity = _safe_parse_json(turn1["text"]) or {}
        result.entity_profile = entity
        result.citations.extend(turn1["citations"])
        result.audit_trail.append(ResearchTurn(
            role="assistant",
            summary=f"Entity resolution — confidence={entity.get('entity_resolution_confidence', 'unknown')}, "
                    f"parent={entity.get('parent_company') or 'n/a'}, "
                    f"ultimate_parent={entity.get('ultimate_parent') or 'n/a'}, "
                    f"citations={len(turn1['citations'])}",
            tool_calls=[{"tool": "web_search", "stop_reason": turn1["stop_reason"]}],
        ))
    except Exception as e:
        logger.exception("Pass 1 (entity resolution) failed")
        result.errors.append(f"Pass 1 failed: {type(e).__name__}: {e}")
        result.completed_at = datetime.utcnow().isoformat() + "Z"
        return result

    # ── Pass 2 — Deep research on ownership, finance, etc. ───────────
    try:
        turn2 = _run_opus_with_search(
            anthropic_key=anthropic_key,
            model=resolved_model,
            system=SYSTEM_SENIOR_ANALYST,
            user_content=_deep_research_prompt(
                vendor_name,
                result.entity_profile,
                structured["internal"],
                structured["google_places"],
            ),
            max_searches=MAX_SEARCHES_PER_TURN * 2,  # deeper pass gets more budget
            max_tokens=6000,
            timeout=900.0,
        )
        findings = _safe_parse_json(turn2["text"]) or {}
        result.findings = findings
        result.citations.extend(turn2["citations"])
        result.audit_trail.append(ResearchTurn(
            role="assistant",
            summary=(
                f"Deep research — ownership_history={len(findings.get('ownership_history') or [])}, "
                f"financial_signals={len(findings.get('financial_signals') or [])}, "
                f"leadership_changes={len(findings.get('leadership_changes') or [])}, "
                f"citations={len(turn2['citations'])}"
            ),
            tool_calls=[{"tool": "web_search", "stop_reason": turn2["stop_reason"]}],
        ))
    except Exception as e:
        logger.exception("Pass 2 (deep research) failed")
        result.errors.append(f"Pass 2 failed: {type(e).__name__}: {e}")

    # ── Pass 3 — Synthesis into a capture brief ──────────────────────
    try:
        turn3 = _run_opus_with_search(
            anthropic_key=anthropic_key,
            model=resolved_model,
            system=SYSTEM_SENIOR_ANALYST,
            user_content=_synthesis_prompt(vendor_name, result.entity_profile, result.findings),
            max_searches=2,  # synthesis pass is mostly writing, not searching
            max_tokens=4000,
        )
        result.synthesis_markdown = turn3["text"]
        result.citations.extend(turn3["citations"])
        result.audit_trail.append(ResearchTurn(
            role="assistant",
            summary=f"Synthesis — {len(turn3['text'])} chars, citations={len(turn3['citations'])}",
            tool_calls=[{"tool": "web_search", "stop_reason": turn3["stop_reason"]}],
        ))
    except Exception as e:
        logger.exception("Pass 3 (synthesis) failed")
        result.errors.append(f"Pass 3 failed: {type(e).__name__}: {e}")

    # Dedupe citations across all passes
    seen = set()
    uniq = []
    for c in result.citations:
        u = c.get("url")
        if not u or u in seen:
            continue
        seen.add(u)
        uniq.append(c)
    result.citations = uniq

    result.completed_at = datetime.utcnow().isoformat() + "Z"
    return result
