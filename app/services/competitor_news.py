"""
Competitor news-fetching service.

Tries (in order):
  1. Google News RSS feed (no API key needed)
  2. Bing News Search API (if BING_NEWS_KEY env is set)
  3. Deterministic mock data (offline fallback)

Called by the daily APScheduler job and the manual /refresh-news endpoint.
"""
import os
import logging
from datetime import datetime, timedelta
from typing import List
import xml.etree.ElementTree as ET

from sqlalchemy.orm import Session

from ..models import Competitor, CompetitorNewsItem

logger = logging.getLogger(__name__)

# ── Google News RSS ──────────────────────────────────────────────────

def _fetch_google_news_rss(query: str, max_items: int = 10) -> List[dict]:
    """Fetch from Google News RSS. Returns list of {title, url, published_at, source}.

    Uses ``make_sync_httpx_client`` so the corporate proxy's SSL bundle is
    honored — plain ``urllib.request`` previously failed silently with
    "CERTIFICATE_VERIFY_FAILED" in environments behind a corporate proxy.
    """
    import urllib.parse
    from .ssl_utils import make_sync_httpx_client

    encoded = urllib.parse.quote_plus(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"

    try:
        with make_sync_httpx_client(timeout=15.0) as client:
            r = client.get(url, headers={"User-Agent": "EverestRFP/1.0"})
        if r.status_code != 200:
            logger.warning(f"Google News RSS HTTP {r.status_code} for '{query}'")
            return []
        data = r.content
        root = ET.fromstring(data)
        items = []
        for item in root.iter("item"):
            title_el = item.find("title")
            link_el = item.find("link")
            pub_el = item.find("pubDate")
            source_el = item.find("source")
            pub_dt = None
            if pub_el is not None and pub_el.text:
                try:
                    from email.utils import parsedate_to_datetime
                    pub_dt = parsedate_to_datetime(pub_el.text)
                except Exception:
                    pass
            items.append({
                "title": title_el.text if title_el is not None else "No title",
                "url": link_el.text if link_el is not None else None,
                "published_at": pub_dt,
                "source": source_el.text if source_el is not None else "Google News",
            })
            if len(items) >= max_items:
                break
        return items
    except Exception as e:
        logger.warning(f"Google News RSS failed for '{query}': {e}")
        return []


# ── Bing News API ────────────────────────────────────────────────────

def _fetch_bing_news(query: str, max_items: int = 10) -> List[dict]:
    """Fetch from Bing News Search API. Requires BING_NEWS_KEY env."""
    api_key = os.getenv("BING_NEWS_KEY")
    if not api_key:
        return []
    import urllib.request
    import urllib.parse
    import json

    encoded = urllib.parse.quote_plus(query)
    url = f"https://api.bing.microsoft.com/v7.0/news/search?q={encoded}&count={max_items}&mkt=en-US"
    try:
        req = urllib.request.Request(url, headers={
            "Ocp-Apim-Subscription-Key": api_key,
            "User-Agent": "EverestRFP/1.0",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        items = []
        for article in data.get("value", []):
            pub_dt = None
            if article.get("datePublished"):
                try:
                    pub_dt = datetime.fromisoformat(article["datePublished"].replace("Z", "+00:00"))
                except Exception:
                    pass
            items.append({
                "title": article.get("name", "No title"),
                "url": article.get("url"),
                "published_at": pub_dt,
                "source": "Bing News",
            })
            if len(items) >= max_items:
                break
        return items
    except Exception as e:
        logger.warning(f"Bing News API failed for '{query}': {e}")
        return []



# NOTE: Mock/fake news fallback was removed. If real news sources fail,
# we log a warning and return zero items. Displaying fabricated data to
# users is unacceptable — the feed must only show verified, real content.


# ── Main entry point ────────────────────────────────────────────────

# Generic words that, when used as a competitor alias, pull massive amounts
# of unrelated noise from Google News (e.g. "Opus" matches Claude Opus, the
# OPUS Switch 2 game, etc.). Filter them out of the search-term list and
# from the post-fetch relevance check.
_ALIAS_STOPWORDS = {
    "opus", "ace", "ats", "iss", "tss", "vis", "asi", "tii",
    "aria", "atlas", "echo", "envoy", "flux", "nova", "orbit",
    "prism", "spark", "spring", "summit", "turbo", "verve", "zephyr",
}

# Industry-domain keywords. If a returned headline contains the company
# name OR one of these terms, we keep it; otherwise we drop it as noise.
# Tunable per-competitor via competitor.description (free-text capture
# from the user) — we extract obvious words from there too.
_DEFAULT_INDUSTRY_KEYWORDS = {
    "inspection", "emissions", "vehicle", "dmv", "mvc", "motor vehicle",
    "smog", "obd", "obd-ii", "viid", "i/m", "i&m",
    "rfp", "contract", "award", "procurement", "solicitation",
    "transportation", "compliance", "audit", "fleet",
    "litigation", "lawsuit", "settlement", "consent decree",
    "breach", "data breach", "ransomware",
    "merger", "acquisition", "acquired", "ipo",
    "ceo", "cfo", "president", "appointed", "resigned",
}


def _build_competitor_keywords(competitor: Competitor) -> tuple[list[str], set[str]]:
    """Return (search_terms, relevance_keywords).

    search_terms — phrases we OR into the Google News query. We DROP bare
    aliases that are in the stopword list (e.g. "Opus") because they pull
    massive amounts of noise. A heuristic also drops single-word aliases
    that are also dictionary words.

    relevance_keywords — every term that, when present in a returned
    headline, marks the article as relevant. Includes the full company
    name, every alias (regardless of stopword status — they're still
    legit when they appear with industry context), and the industry
    discriminators below.
    """
    import json as _json
    aliases = []
    if competitor.aliases:
        try:
            v = _json.loads(competitor.aliases)
            if isinstance(v, list):
                aliases = [str(x).strip() for x in v if str(x).strip()]
        except Exception:
            pass

    # Search terms: full name (always) + multi-word aliases + non-stopword
    # single-word aliases.
    search_terms = [competitor.name.strip()]
    for a in aliases[:5]:
        words = a.split()
        if len(words) >= 2 or a.lower() not in _ALIAS_STOPWORDS:
            search_terms.append(a)

    # Relevance keywords (lowercase, used as substring checks against headlines)
    relevance = set()
    relevance.add(competitor.name.strip().lower())
    for a in aliases:
        relevance.add(a.lower())
    relevance |= _DEFAULT_INDUSTRY_KEYWORDS
    # Pull obvious capitalized words from the description if present —
    # gives the user a knob to add discriminators without code changes.
    if competitor.description:
        import re
        for m in re.findall(r"\b[A-Za-z][A-Za-z &-]{3,}\b", competitor.description):
            relevance.add(m.strip().lower())
    return search_terms, relevance


def _is_relevant_headline(title: str, relevance_keywords: set[str]) -> bool:
    """A headline is relevant if it contains the company name OR (an alias
    AND any industry discriminator). The simple "any keyword wins" rule
    here is intentional — false negatives are cheap (user can rerun) but
    false positives erode user trust in the feed."""
    if not title:
        return False
    t = title.lower()
    return any(kw in t for kw in relevance_keywords if len(kw) >= 4)


def fetch_news_for_competitor(db: Session, competitor: Competitor, max_items: int = 10) -> int:
    """
    Fetch latest news for a competitor and store new items.
    Returns the number of new items stored.
    """
    search_terms, relevance = _build_competitor_keywords(competitor)
    query = " OR ".join(f'"{t}"' for t in search_terms)

    # Try providers in order
    items = _fetch_google_news_rss(query, max_items * 3)  # over-fetch then filter
    source_used = "google_news"

    if not items:
        items = _fetch_bing_news(query, max_items * 3)
        source_used = "bing_news"

    # Drop noise: keep only headlines that contain a relevance keyword.
    if items:
        before = len(items)
        items = [it for it in items if _is_relevant_headline(it.get("title", ""), relevance)]
        dropped = before - len(items)
        if dropped:
            logger.info(
                f"Dropped {dropped} noise hit(s) from {source_used} for "
                f"'{competitor.name}' (kept {len(items)})"
            )
        items = items[:max_items]

    if not items:
        logger.warning(
            f"No real news found for '{competitor.name}' from any provider. "
            f"Feed will remain empty until real articles are available."
        )
        return 0

    # Dedup: skip items whose title already exists for this competitor
    existing_titles = set(
        row.title for row in
        db.query(CompetitorNewsItem.title)
        .filter(CompetitorNewsItem.competitor_id == competitor.id)
        .all()
    )

    new_count = 0
    for item in items:
        if item["title"] in existing_titles:
            continue
        db.add(CompetitorNewsItem(
            competitor_id=competitor.id,
            title=item["title"],
            url=item.get("url"),
            source=item.get("source", source_used),
            summary=None,
            published_at=item.get("published_at"),
        ))
        new_count += 1

    if new_count > 0:
        db.commit()

    logger.info(f"Fetched {new_count} new news items for '{competitor.name}' via {source_used}")
    return new_count


def refresh_all_watchlisted(db: Session) -> dict:
    """Refresh news for all watchlisted competitors. Called by scheduler."""
    competitors = db.query(Competitor).filter(Competitor.watchlist == True).all()
    results = {}
    for comp in competitors:
        try:
            count = fetch_news_for_competitor(db, comp)
            results[comp.name] = count
        except Exception as e:
            logger.error(f"Error fetching news for {comp.name}: {e}")
            results[comp.name] = -1
    return results

# ── RFP-specific news ────────────────────────────────────────────────

def _build_rfp_query(proposal) -> str:
    """Compose a focused news search string from the proposal's identifiers.

    Strategy: prefer (a) the solicitation number when present (very high
    precision); fall back to (b) issuing agency + key terms from the title.
    Putting the solicitation number in quotes drives Google News to phrase-
    match exactly, which is what we want — RFP numbers are unique strings.
    """
    bits = []
    if getattr(proposal, "solicitation_number", None):
        bits.append(f'"{proposal.solicitation_number}"')
    # Title can be long ("NJ MVC Vehicle Inspection Services – T1628 (2026 Rebid)").
    # Strip out parentheticals + punctuation; keep the substantive phrase.
    title = (getattr(proposal, "title", "") or "").strip()
    if title:
        import re
        cleaned = re.sub(r"\([^)]*\)", "", title)
        cleaned = re.sub(r"[—–\-]+", " ", cleaned).strip()
        if cleaned:
            bits.append(f'"{cleaned}"')
    if getattr(proposal, "issuing_agency", None):
        bits.append(f'"{proposal.issuing_agency}"')
    # Combine with OR so any of these phrases triggers a hit.
    return " OR ".join(bits) if bits else (title or "RFP")


def fetch_news_for_proposal(db: Session, proposal, max_items: int = 15) -> int:
    """Fetch RFP-specific news (solicitation number, agency, title) and store
    new items tagged to this proposal. Returns the number of new rows added."""
    query = _build_rfp_query(proposal)

    items = _fetch_google_news_rss(query, max_items)
    source_used = "google_news"
    if not items:
        items = _fetch_bing_news(query, max_items)
        source_used = "bing_news"

    if not items:
        return 0

    # Dedupe within proposal scope using (title, url) so re-running the
    # refresh button is idempotent without nuking competitor-scoped rows.
    existing = {
        (row.title, row.url) for row in
        db.query(CompetitorNewsItem.title, CompetitorNewsItem.url)
        .filter(CompetitorNewsItem.proposal_id == proposal.id)
        .all()
    }

    new_count = 0
    for item in items:
        key = (item["title"], item.get("url"))
        if key in existing:
            continue
        db.add(CompetitorNewsItem(
            competitor_id=None,
            proposal_id=proposal.id,
            title=item["title"],
            url=item.get("url"),
            source=item.get("source", source_used),
            query_used=query,
            summary=None,
            published_at=item.get("published_at"),
        ))
        new_count += 1
        existing.add(key)

    if new_count > 0:
        db.commit()
    logger.info(
        f"RFP news refresh for proposal {proposal.id} ('{proposal.title}'): "
        f"{new_count} new items via {source_used} (query={query!r})"
    )
    return new_count


def refresh_news_for_proposal(db: Session, proposal_id: int) -> dict:
    """Refresh both the RFP-specific feed and every targeted-competitor
    feed for a proposal. Returns a per-source breakdown so the UI can show
    a useful 'we got N new items' toast."""
    from ..models import Proposal, ProposalCompetitor
    proposal = db.query(Proposal).filter(Proposal.id == proposal_id).first()
    if not proposal:
        return {"error": "proposal not found"}

    breakdown: dict = {"rfp": 0, "competitors": {}}
    breakdown["rfp"] = fetch_news_for_proposal(db, proposal)

    # Fan out to every competitor the capture team is tracking on this RFP.
    pcs = (db.query(ProposalCompetitor)
           .filter(ProposalCompetitor.proposal_id == proposal_id).all())
    for pc in pcs:
        comp = db.query(Competitor).filter(Competitor.id == pc.competitor_id).first()
        if not comp:
            continue
        try:
            n = fetch_news_for_competitor(db, comp)
            breakdown["competitors"][comp.name] = n
        except Exception as e:
            logger.warning(f"Competitor news refresh failed for {comp.name}: {e}")
            breakdown["competitors"][comp.name] = -1
    return breakdown