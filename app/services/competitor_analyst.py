"""
Competitor Analyst Agent — builds intelligence dossiers from ingested documents and news.

This agent:
1. Reads all ingested document chunks tagged to a competitor
2. Reads all news items for that competitor  
3. Synthesizes structured intelligence across categories
4. Generates a competitor-writer persona system prompt
"""
import json
import logging
import re
from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from ..models import (
    Competitor, CompetitorDossier, CompetitorNewsItem, CompetitorPrediction,
    IngestedDocument, DocumentChunk, Persona, ScoringRubricSection, ScoringRubric,
)

logger = logging.getLogger(__name__)

DOSSIER_CATEGORIES = [
    "leadership",
    "technology",
    "customer_service",
    "contracts",
    "financials",
    "strengths",
    "weaknesses",
    "strategy",
]

# ── Gather raw intelligence ──────────────────────────────────────────

def gather_competitor_context(db: Session, competitor_id: int) -> dict:
    """Collect all available intelligence for a competitor."""
    comp = db.query(Competitor).filter(Competitor.id == competitor_id).first()
    if not comp:
        return {}

    # Get all document chunks tagged to this competitor
    docs = (
        db.query(IngestedDocument)
        .filter(IngestedDocument.competitor_id == competitor_id)
        .all()
    )
    doc_chunks = []
    for doc in docs:
        chunks = (
            db.query(DocumentChunk)
            .filter(DocumentChunk.document_id == doc.id)
            .order_by(DocumentChunk.chunk_index)
            .all()
        )
        for c in chunks:
            doc_chunks.append({
                "source_doc": doc.original_filename,
                "source_type": doc.source_type,
                "page": c.page_number,
                "content": c.content,
            })

    # Get news items
    news = (
        db.query(CompetitorNewsItem)
        .filter(CompetitorNewsItem.competitor_id == competitor_id)
        .order_by(CompetitorNewsItem.fetched_at.desc())
        .limit(50)
        .all()
    )
    news_items = [
        {"title": n.title, "source": n.source, "date": n.published_at.isoformat() if n.published_at else None}
        for n in news
    ]

    # Get existing dossier entries
    existing_dossier = (
        db.query(CompetitorDossier)
        .filter(CompetitorDossier.competitor_id == competitor_id)
        .all()
    )
    existing = {d.category: d.content for d in existing_dossier}

    aliases = []
    if comp.aliases:
        try:
            aliases = json.loads(comp.aliases)
        except Exception:
            pass

    return {
        "name": comp.name,
        "website": comp.website,
        "aliases": aliases,
        "description": comp.description,
        "document_chunks": doc_chunks,
        "news_items": news_items,
        "existing_dossier": existing,
    }


# ── Build analyst prompt ─────────────────────────────────────────────

# Category-specific deep-research playbooks. Each entry tells the agent
# exactly what to dig for, which public sources to consult, and what
# a useful answer looks like.
#
# GLOBAL FRAMING: These competitors are multi-state and in some cases
# multinational. We care about their ENTIRE footprint — every jurisdiction
# they operate in, how they operate, how many vehicles / inspections / sites
# they handle, and the division that owns the line of business. NJ MVC is
# only the eventual bid target; research must be global-to-the-company.
CATEGORY_PLAYBOOK = {
    "leadership": {
        "title": "Division Leadership & Key Personnel (Global)",
        "objective": (
            "Identify the executives, program managers, and country/region leads who actually run "
            "the business unit that delivers vehicle inspection / motor-vehicle services — wherever "
            "in the world it operates. Do NOT list unrelated corporate C-suite."
        ),
        "must_answer": [
            "President / GM / SVP of the inspection / motor-vehicle-services division (global).",
            "Regional leads by country or region (North America, EMEA, LATAM, APAC) if multinational.",
            "Program managers / contract directors on each major state or national contract — name the contract.",
            "Board members or executives with publicly stated responsibility for this segment.",
            "Recent hires, departures, reorganizations in this division (last 24 months) — anywhere.",
            "Publicly linked individuals in LinkedIn / press / conference programs tied to specific contracts.",
        ],
        "sources_to_check": [
            "Division-specific press releases (all geographies)",
            "10-K / 10-Q / proxy (DEF 14A) segment disclosures and executive bios",
            "International equivalents: UK annual reports (Companies House), EU financial filings, ASX/TSX disclosures",
            "LinkedIn titles and tenure",
            "State DMV / national ministry press releases and award announcements",
            "Industry conference speaker lists (AAMVA, CITA, NACAA, CITE, SEMA)",
        ],
    },
    "technology": {
        "title": "Vehicle Inspection Technology Stack (Global)",
        "objective": (
            "Describe the actual inspection platform, OBD-II / emissions hardware, station software, "
            "data-transmission back-end, and customer-facing tech used across their contracts — and note "
            "where the stack differs by jurisdiction. Skip generic IT."
        ),
        "must_answer": [
            "Name and version of the inspection management system / VIID they deploy (per jurisdiction if it varies).",
            "OBD-II analyzers, emissions benches, dyno or tailpipe equipment (vendor, model).",
            "How station data is transmitted to each state/country (VPN, API, SFTP).",
            "Kiosks, scheduling apps, driver-facing portals, mobile apps.",
            "Cybersecurity posture: StateRAMP, FedRAMP, SOC 2, ISO 27001, GDPR posture, public breach history.",
            "Remote sensing, roadside inspection, heavy-duty or periodic-MOT technology where applicable.",
            "Patents, whitepapers, and R&D investments tied to this division.",
            "AI / computer-vision / telematics pilots or deployments.",
        ],
        "sources_to_check": [
            "FOIA'd technical proposals (US)",
            "EU / UK / AU procurement portals (OJEU, Contracts Finder, AusTender)",
            "USPTO, EPO, WIPO patent search",
            "GitHub / vendor-partnership press releases",
            "Trade-publication product coverage (Automotive Testing Technology International)",
        ],
    },
    "customer_service": {
        "title": "Customer Service & Operational Performance (All Jurisdictions)",
        "objective": (
            "Assess how well they serve motorists today with objective, sourced metrics — across every "
            "jurisdiction. Include volume: # stations, # inspections/year, motorist populations served."
        ),
        "must_answer": [
            "Number of inspection stations / lanes operated per jurisdiction.",
            "Inspections completed per year, per jurisdiction, most recent 3 years.",
            "Average wait times and how they compare to contract SLAs.",
            "Customer-satisfaction survey results (state/country, year, score).",
            "Complaint volume and type filed with DMVs, BBB, state AGs, or international equivalents.",
            "Station uptime / SLA compliance history and liquidated-damages history.",
            "Call-center metrics if they run one; languages supported.",
            "Accessibility / ADA or equivalent compliance record.",
            "Publicized lawsuits, regulatory findings, audit negatives, press scandals.",
        ],
        "sources_to_check": [
            "State audit reports and performance audits",
            "BBB / Google / Yelp / Trustpilot aggregated review patterns",
            "Local / regional news coverage of long lines or service failures",
            "State legislative hearings and oversight testimony",
            "International regulator reports (DVSA in UK, DEKRA/TÜV peers in EU, etc.)",
        ],
    },
    "contracts": {
        "title": "Active & Historical Inspection Contracts — Global Footprint",
        "objective": (
            "Build a contract table covering EVERY vehicle-inspection or adjacent motor-vehicle-services "
            "contract currently held or recently lost, anywhere in the world. Include dollars/local currency, "
            "duration, scope, and volume. Make it easy to count states/countries and total vehicles/year."
        ),
        "must_answer": [
            "Jurisdiction (state or country), agency, start date, end date, option years, total contract value, currency.",
            "Scope (safety, emissions, enhanced I/M, remote sensing, periodic MOT, heavy-duty, roadside).",
            "Annual inspection volume per contract.",
            "Subcontractors and teaming partners.",
            "Performance history — renewed, re-compete outcomes, terminated for cause?",
            "Contracts lost in the last 5 years and why.",
            "Active bids or RFP responses in flight.",
            "Competitors they typically beat or lose to in each jurisdiction.",
            "Any exclusivity or private-monopoly concessions (common outside the US).",
        ],
        "sources_to_check": [
            "USA Spending / SAM.gov",
            "State procurement portals and award notices",
            "EU TED (Tenders Electronic Daily), UK Contracts Finder, AusTender, CanadaBuys, ProZorro",
            "Bloomberg Government / GovTribe / Tussell",
            "Corporate investor-presentation appendix slides (they often list contracts)",
        ],
    },
    "financials": {
        "title": "Division Financials & Financial Health (Global)",
        "objective": (
            "If the parent (or any holding entity) is publicly traded or files statutory accounts anywhere, "
            "pull SEGMENT-level financials for the business unit — do not stop at consolidated revenue. "
            "Flag financial stress signals specifically in this division."
        ),
        "must_answer": [
            "Parent entity, ticker(s), exchange(s), most recent annual filing date(s).",
            "Segment revenue, operating income, and margin for the division — last 3 years.",
            "Revenue split by geography within the division if disclosed.",
            "Goodwill impairments, restructuring charges, write-downs tied to this segment.",
            "Management commentary on the division in MD&A, earnings calls, investor days.",
            "Analyst commentary specifically on this segment.",
            "Debt load, covenant status, credit rating actions at parent and division level.",
            "Private-equity ownership, sale processes, spin-off or divestiture rumors.",
            "SEC / FCA / ASIC disclosures flagging contract losses, underperformance, going-concern issues.",
        ],
        "sources_to_check": [
            "SEC EDGAR (10-K, 10-Q, 8-K, DEF 14A)",
            "UK Companies House, EU business registers, SEDAR (Canada), ASX announcements, SGX, JPX",
            "Earnings call transcripts (Seeking Alpha, Motley Fool, TIKR)",
            "Analyst research notes (Morningstar, S&P Capital IQ, Bloomberg)",
            "D&B / Moody's / S&P / Fitch credit reports",
        ],
    },
    "strengths": {
        "title": "Competitive Strengths — Across the Portfolio",
        "objective": (
            "Rank the 5 strongest advantages this competitor could press in ANY bid, with particular "
            "attention to scale (# states/countries, vehicles/year, workforce) and past-performance evidence."
        ),
        "must_answer": [
            "Scale advantages: how many jurisdictions, how many lanes, how many vehicles/year.",
            "Specific past-performance wins they will highlight and where.",
            "Technology or operational capability that discriminates them.",
            "Staffing / workforce depth evidence (headcount, certifications, training programs).",
            "Incumbency or adjacency advantages in neighboring markets or parent-company lines.",
            "Certifications / accreditations that earn evaluation points (ISO, SOC, StateRAMP, ITAR, etc.).",
        ],
        "sources_to_check": [
            "Dossier sections above",
            "Uploaded proposal documents",
            "Their marketing collateral, case studies, investor decks (verify claims independently)",
        ],
    },
    "weaknesses": {
        "title": "Competitive Weaknesses & Attack Vectors (Global)",
        "objective": (
            "Identify the 5 weakest or riskiest elements an evaluation committee would penalize — "
            "drawing from any jurisdiction they operate in. Prioritize DOCUMENTED issues, not speculation."
        ),
        "must_answer": [
            "Documented service failures, audits, lawsuits, penalty assessments — any jurisdiction.",
            "Financial stress at the division level (link to 'financials' findings).",
            "Contract losses or non-renewals and the stated reasons.",
            "Staffing / turnover signals: job postings, WARN / equivalent redundancy notices, Glassdoor patterns.",
            "Technology gaps revealed in prior RFP responses.",
            "Reputation issues in local / trade / international press.",
            "Concentration risk (too dependent on one contract, one client, one geography).",
        ],
        "sources_to_check": [
            "PACER and international equivalents (BAILII UK, CanLII, AustLII, EU Curia)",
            "OSHA / DOL citation databases and international H&S regulators",
            "State AG / ombudsman complaint archives, international consumer regulators",
            "WARN Act notices and EU collective-redundancy filings",
            "Glassdoor / Indeed / kununu sentiment and leadership-approval trends",
        ],
    },
    "strategy": {
        "title": "Likely Bid Strategy — With Portfolio Context",
        "objective": (
            "Predict how they will position on price, technical approach, transition, teaming, and themes "
            "for the NJ MVC bid. Tie every prediction to concrete evidence from their portfolio and filings."
        ),
        "must_answer": [
            "Pricing posture (aggressive low-ball vs. premium-quality) based on their historical bids globally.",
            "Technical differentiators they will emphasize and where they have already proven them.",
            "Teaming partners / small-business pairings likely in NJ.",
            "Transition plan tone (safe-hands incumbent-style vs. disruptive modernization).",
            "Top 3 win themes written in their voice, with the portfolio proof points they will cite.",
            "Risks they will have to overcome in evaluators' minds (tie to 'weaknesses').",
        ],
        "sources_to_check": [
            "Prior state / national proposals",
            "Recent press releases, thought leadership, analyst-day presentations",
            "Trade-show presentations, conference keynotes",
        ],
    },
}


def build_analyst_prompt(context: dict, category: str) -> str:
    """Build the prompt for the analyst to generate a dossier entry."""
    # Pack as many document chunks as we can afford — prefer recent / longer ones.
    docs = context.get("document_chunks") or []
    if docs:
        # Keep payload moderate to stay well under provider latency limits.
        # 40k chars ~ 10k tokens of evidence; enough for the analyst to cite with,
        # without tipping long requests into timeout territory.
        max_chars = 40_000
        buf = []
        used = 0
        for c in docs:
            piece = (
                f"[Source: {c['source_doc']} | Type: {c['source_type']} | Page {c['page']}]\n"
                f"{c['content']}"
            )
            if used + len(piece) > max_chars:
                break
            buf.append(piece)
            used += len(piece)
        doc_text = "\n\n".join(buf)
        doc_count_note = f"({len(buf)} of {len(docs)} document chunks included)"
    else:
        doc_text = "(No documents have been uploaded for this competitor. Use public-record research only and flag gaps.)"
        doc_count_note = "(no uploaded documents)"

    news_text = ""
    if context["news_items"]:
        news_text = "\n".join(
            f"- {n['title']} ({n['source']}, {n['date']})"
            for n in context["news_items"][:40]
        )
    else:
        news_text = "(No news items collected yet)"

    play = CATEGORY_PLAYBOOK.get(category, {})
    title = play.get("title") or category.replace("_", " ").title()
    objective = play.get("objective") or f"Produce a deep intelligence brief for the '{category}' category."
    must_answer = play.get("must_answer") or []
    sources = play.get("sources_to_check") or []

    must_answer_md = "\n".join(f"- {q}" for q in must_answer) if must_answer else "- (none specified — be exhaustive)"
    sources_md = "\n".join(f"- {s}" for s in sources) if sources else "- (general public research)"

    research_focus = (context.get("description") or "").strip() or (
        "No research focus recorded. Assume we care about the business unit / subsidiary that delivers "
        "vehicle inspection, emissions testing, and DMV-adjacent services (NOT the unrelated divisions)."
    )

    return f"""You are an elite competitive-intelligence analyst preparing a bid-capture intelligence brief.
The EVENTUAL bid target is the **NJ Motor Vehicle Commission (MVC) Vehicle Inspection Program**, but
that is only context for why we care about this company. Your research scope is **global**, covering
the company's **entire multi-state and (where applicable) multinational footprint**. Do NOT limit
findings to New Jersey or to the US — we need to understand how they operate everywhere they operate.

Target company: **{context['name']}**
Category: **{title}** ({category})

## What we care about (RESEARCH FOCUS — read carefully)
{research_focus}

IMPORTANT — two non-negotiable framing rules:

1. **Narrow to the right division.** These competitors are usually large, diversified (often publicly
   traded) corporations with many business units. Focus on the **specific division, subsidiary, or
   business segment** that delivers vehicle inspection / emissions testing / motor-vehicle services.
   Do NOT pad the brief with unrelated corporate overview. If the parent is publicly traded, dig into
   **segment-level disclosures** in annual/interim filings and transcripts.

2. **Go wide across jurisdictions.** Map their footprint across every state, country, and region where
   they run these services. How many locations, how many vehicles per year, what the contract scope
   looks like, how local operations differ, and which jurisdictions they have lost or exited. Scale and
   performance in one place tells us what to expect in NJ.

## Objective for this section
{objective}

## Questions you MUST try to answer
{must_answer_md}

## Where real answers live (cite when you use them)
{sources_md}

## Competitor profile
- Name: {context['name']}
- Website: {context.get('website') or 'Unknown'}
- Aliases / DBAs: {', '.join(context.get('aliases') or []) or 'None'}

## Uploaded documents {doc_count_note}
Treat these as your HIGHEST-CONFIDENCE source. Quote and cite them explicitly.

{doc_text}

## Recent news & signals
{news_text}

## TRUTHFULNESS RULES — READ BEFORE WRITING ANYTHING

You have **NO live internet access** and **NO ability to browse, search, or query APIs**. Your ONLY
trustworthy evidence sources are:

  (a) the **Uploaded documents** section below (quote with `[uploaded: filename p.N]`), and
  (b) the **Recent news & signals** section below (cite with `[news: <headline>]`).

Everything else — including things that "feel common-knowledge" about this company, its contracts,
its executives, its history, or its financials — is **NOT verified** from your perspective and must
be treated as such. Specifically, you MUST follow these rules:

1. **Never invent specific facts.** Do NOT produce any of the following unless they appear in the
   Uploaded documents or Recent news sections above:
     - Named executives, job titles, tenure, hire/departure dates
     - Contract awards, losses, dollar values, start/end dates, jurisdictions
     - Revenue figures, margins, impairments, ticker symbols, filing dates
     - Lawsuits, regulatory actions, audit findings, station counts, inspection volumes
   If the evidence is not in your provided context, say so explicitly. Do not fill the gap.

2. **No plausible-sounding narratives.** If you are tempted to write a sentence like "X lost the
   Y contract in YYYY to Z", stop. Unless the exact event is cited in your provided evidence, write
   instead: "No evidence in provided sources confirms the state of the Y contract. `[needs research]`".

3. **Tag every factual sentence.** Each claim must end with one of:
     - `[uploaded: filename p.N]` — cited from an uploaded document
     - `[news: headline]` — cited from the news block
     - `[INFERENCE]` — a logical inference from the above two sources; must be clearly derivable
     - `[UNVERIFIED]` — general-knowledge claim you cannot substantiate from provided sources
   Any sentence without a tag will be treated as `[UNVERIFIED]` and **removed** by the fact-checker.

4. **Prefer "unknown" over "probable".** It is far better to write an honest gap list than to
   fabricate a narrative. The reader will act on what you write — false specifics cause lost bids.

5. **Confidence tags reflect evidence, not confidence in your reasoning.** Use `(high)` only when
   the claim is directly quoted/paraphrased from an uploaded document. `(medium)` for news. `(low)`
   for `[INFERENCE]`. Never use `(high)` on an `[UNVERIFIED]` or `[INFERENCE]` claim.

## Output rules
1. Structure with Markdown headings (`##`, `###`), bullet lists, and — where it fits — a Markdown table.
2. Lead with a **TL;DR** block (3–5 bullets) of the most decision-useful findings, each with source tags.
3. Every factual sentence carries an inline source tag per rule 3 above.
4. Tag confidence per finding: `(high)`, `(medium)`, `(low)`.
5. End with a **Collection Gaps** subsection listing the top 3 missing pieces of evidence and the
   exact source / FOIA / filing to request next.
6. Be specific ONLY when you have evidence. Name names, dollars, dates, contract numbers, tickers —
   but ONLY if they are present in the provided sources.
7. If the Uploaded documents and news blocks contain no relevant content for this category, say so
   plainly: "No provided sources contain evidence for this category." and produce only the
   Collection Gaps section.
8. Write in the tone of a senior capture analyst — dense, cited, direct, and honest about gaps.

Produce the intelligence brief for **{title}** now."""


# ── Generate dossier (with AI or mock) ───────────────────────────────

def _load_ai_settings() -> dict:
    """Load AI settings from the app_settings DB table, falling back to env vars."""
    import os
    from ..database import SessionLocal
    from ..models import AppSetting

    result = {
        "llm_provider": os.getenv("LLM_PROVIDER", "openai"),
        "openai_api_key": os.getenv("OPENAI_API_KEY", ""),
        "anthropic_api_key": os.getenv("ANTHROPIC_API_KEY", ""),
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


# Track the most recent LLM failure reason so callers can surface it honestly
# instead of silently delivering mock output. Reset at the top of every call.
_LAST_AI_ERROR: dict = {"provider": None, "type": None, "message": None}


def get_last_ai_error() -> dict:
    """Return a copy of the most recent LLM failure recorded by _call_ai (or empty if none)."""
    return dict(_LAST_AI_ERROR)


def _call_ai(prompt: str, system: str = "You are a competitive intelligence analyst.") -> str:
    """Call AI using DB-configured provider/key with env var fallback; returns empty string on total failure.

    On failure, _LAST_AI_ERROR is populated so callers can distinguish "AI returned
    content" from "AI was unavailable" and respond accordingly (e.g., write an honest
    evidence-pending stub instead of a misleading mock).
    """
    global _LAST_AI_ERROR
    _LAST_AI_ERROR = {"provider": None, "type": None, "message": None}
    from .ssl_utils import make_sync_httpx_client

    cfg = _load_ai_settings()
    provider = (cfg.get("llm_provider") or "openai").lower()
    openai_key = cfg.get("openai_api_key") or ""
    anthropic_key = cfg.get("anthropic_api_key") or ""

    order = []
    if provider == "anthropic":
        if anthropic_key:
            order.append("anthropic")
        if openai_key:
            order.append("openai")
    else:
        if openai_key:
            order.append("openai")
        if anthropic_key:
            order.append("anthropic")

    from . import llm_usage as _usage
    import time as _time
    for p in order:
        if p == "anthropic":
            try:
                import anthropic
                client = anthropic.Anthropic(
                    api_key=anthropic_key,
                    http_client=make_sync_httpx_client(timeout=300.0),
                    timeout=300.0,
                    max_retries=1,
                )
                _t0 = _time.time()
                response = client.messages.create(
                    model="claude-sonnet-4-5-20250929",
                    max_tokens=4000,
                    system=system,
                    messages=[{"role": "user", "content": prompt}],
                )
                in_tok, out_tok = _usage.extract_anthropic_usage(response)
                _usage.record(
                    provider="anthropic", model="claude-sonnet-4-5-20250929",
                    input_tokens=in_tok, output_tokens=out_tok,
                    latency_ms=_usage.time_block_ms(_t0),
                )
                return response.content[0].text
            except Exception as e:
                logger.warning(f"Anthropic call failed ({type(e).__name__}): {e}")
                _LAST_AI_ERROR = {"provider": "anthropic", "type": type(e).__name__, "message": str(e)[:400]}
        elif p == "openai":
            try:
                import openai
                client = openai.OpenAI(
                    api_key=openai_key,
                    http_client=make_sync_httpx_client(timeout=300.0),
                    timeout=300.0,
                    max_retries=1,
                )
                _t0 = _time.time()
                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=4000,
                )
                in_tok, out_tok = _usage.extract_openai_usage(response)
                _usage.record(
                    provider="openai", model="gpt-4o",
                    input_tokens=in_tok, output_tokens=out_tok,
                    latency_ms=_usage.time_block_ms(_t0),
                )
                return response.choices[0].message.content
            except Exception as e:
                logger.warning(f"OpenAI call failed ({type(e).__name__}): {e}")
                _LAST_AI_ERROR = {"provider": "openai", "type": type(e).__name__, "message": str(e)[:400]}

    if not order:
        _LAST_AI_ERROR = {"provider": None, "type": "NoProviderConfigured",
                          "message": "No LLM API key is configured in settings. Add an Anthropic or OpenAI key in Settings → AI."}

    logger.warning("No AI provider succeeded for dossier generation; returning empty string so caller can emit an honest evidence-pending stub.")
    return ""


def _evidence_pending_stub(name: str, category: str, play: dict, has_docs: bool, ai_error: dict) -> str:
    """Honest placeholder entry written when the LLM was unavailable.

    This replaces the old "mock" fallback which was misleading and invited callers to treat
    fabricated-sounding placeholder text as real intelligence. The new stub explicitly states
    the LLM failed, what evidence exists locally, and what the analyst would have asked.
    """
    title = play.get("title") or category.replace("_", " ").title()
    must_answer = play.get("must_answer") or []
    questions_md = "\n".join(f"- {q}" for q in must_answer[:6]) if must_answer else "- (none specified)"
    err_bit = ""
    if ai_error and (ai_error.get("type") or ai_error.get("message")):
        provider = ai_error.get("provider") or "configured provider"
        err_bit = (
            f"\n\n> **LLM failure detail:** `{provider}` raised `{ai_error.get('type') or 'error'}` — "
            f"{ai_error.get('message') or 'no additional detail'}"
        )

    doc_state = (
        "Uploaded documents are present and can be mined once the LLM is reachable again."
        if has_docs
        else "No documents have been uploaded yet for this competitor."
    )

    return (
        f"## {title} — **Evidence pending (LLM unavailable)**\n\n"
        f"The analyst and fact-checker could not run for this category because the LLM provider did "
        f"not respond successfully on this attempt. No intelligence content has been generated. "
        f"This entry intentionally contains **no inferred or placeholder facts** about {name}.\n\n"
        f"**State of local evidence for this category:** {doc_state}{err_bit}\n\n"
        f"### What the analyst would have asked\n{questions_md}\n\n"
        f"### Next step\n"
        f"Retry **Build Dossier** once the LLM is reachable, and/or upload supporting documents "
        f"(FOIA'd proposals, filings, state audits) so the analyst has evidence to cite."
    )


# ── Fact-checker (second pass) ───────────────────────────────────────

FACT_CHECKER_SYSTEM = (
    "You are a rigorous fact-checking editor for competitive-intelligence briefs. Your ONE job is "
    "to prevent false information from reaching a capture executive. You read a draft brief and the "
    "exact provided sources, and you remove or flag every sentence the sources do not substantiate. "
    "You do NOT add new facts. You do NOT guess. You are conservative and skeptical."
)


def build_factcheck_prompt(context: dict, category: str, draft: str) -> str:
    """Build the fact-checker prompt that audits a draft against the provided evidence."""
    docs = context.get("document_chunks") or []
    if docs:
        # Same moderate ceiling as the analyst to keep this second pass fast.
        max_chars = 40_000
        buf = []
        used = 0
        for c in docs:
            piece = (
                f"[Source: {c['source_doc']} | Type: {c['source_type']} | Page {c['page']}]\n"
                f"{c['content']}"
            )
            if used + len(piece) > max_chars:
                break
            buf.append(piece)
            used += len(piece)
        doc_text = "\n\n".join(buf)
    else:
        doc_text = "(No uploaded documents.)"

    news_text = ""
    if context.get("news_items"):
        news_text = "\n".join(
            f"- {n['title']} ({n['source']}, {n['date']})"
            for n in context["news_items"][:40]
        )
    else:
        news_text = "(No news items.)"

    return f"""You are fact-checking a competitive-intelligence draft. The analyst who wrote the draft
has NO internet access — their only trustworthy sources are the Uploaded documents and news items
shown below. Your job: audit the draft and remove or demote anything not substantiated by those
sources. Be aggressive. False specifics are worse than gaps.

Target company: **{context['name']}**
Category: **{category}**

## Provided sources (the ONLY trustworthy evidence)

### Uploaded documents
{doc_text}

### News items
{news_text}

## Draft to audit
---
{draft}
---

## Your audit procedure
For every factual sentence in the draft, classify it into exactly one bucket:

  **VERIFIED**   — directly supported by an uploaded document or news item. Keep as-is, ensure the
                   inline source tag matches a real source above.
  **INFERENCE**  — a logical inference clearly derivable from the verified sources. Keep, but ensure
                   it is tagged `[INFERENCE]` and confidence is `(low)` or `(medium)`.
  **UNVERIFIED** — not substantiated by the provided sources (including "common-knowledge" claims,
                   invented dates, invented contracts, invented names, invented dollar amounts).
                   You MUST either:
                     (a) rewrite the sentence to remove the specific unverifiable facts and
                         replace with an explicit gap note, OR
                     (b) delete the sentence entirely.
                   Do NOT leave unverified specifics in the output.

Special cases to watch for:
- Named executives without a source → DELETE or mark "No provided source names current leadership."
- Contract wins/losses with specific years, counterparties, or dollars that are not in the sources → DELETE.
- Revenue / margin / impairment figures not in the sources → DELETE.
- Station counts, inspection volumes, wait-time numbers not in the sources → DELETE.
- Vague "industry knows..." / "it is widely reported..." claims → DELETE.

## Output format — TWO parts, in this exact order

**Part 1 — a small JSON object.** Must be on its own, wrapped in a ```json fenced block. Do NOT put
any markdown or multi-line text inside this JSON. Keep `quote` and `problem` fields short and on a
single line each (truncate long quotes with "…" if needed). No trailing commas.

```json
{{
  "verdict": "pass" | "revised" | "insufficient_evidence",
  "overall_confidence": "high" | "medium" | "low" | "unverified",
  "issues_found": [
    {{"severity": "critical" | "major" | "minor",
      "quote": "<short single-line excerpt>",
      "problem": "<one-line reason>",
      "action": "deleted" | "rewritten" | "demoted"}}
  ]
}}
```

**Part 2 — the revised brief in Markdown**, wrapped between these exact delimiters on their own lines:

<<<REVISED_MARKDOWN
(your revised markdown goes here — same structure as the draft, with unverified claims removed or
 rewritten. Include the original TL;DR and Collection Gaps sections, revised. Append a final
 `## Fact-Check Notes` section summarizing what was changed and why. Use normal Markdown freely —
 headings, lists, backticks, line breaks — none of it needs to be escaped because it is NOT JSON.)
REVISED_MARKDOWN>>>

Guidelines for `overall_confidence`:
- `high`     — every substantive claim cites an uploaded document.
- `medium`   — primarily news-based with a few inferences; no unverified specifics remain.
- `low`      — mostly inference; thin evidence; heavy Collection Gaps section.
- `unverified` — no provided sources supported the draft; return a near-empty brief that only
                 lists what is known and what must be collected.

Output the JSON block first, then the REVISED_MARKDOWN block. Nothing else.
"""


def _extract_json_object(text: str) -> Optional[dict]:
    """Extract and parse the first JSON object from a string.

    Tries progressively more aggressive recovery strategies:
      1. Strip ```json / ``` fences if present.
      2. Locate the first `{` and the matching closing `}` (brace-balanced, string-aware).
      3. Try json.loads.
      4. On failure, repair common LLM mistakes:
         - trailing commas before `}` or `]`
         - unescaped newlines / tabs inside string literals
         - smart quotes swapped for ASCII quotes
      5. Re-try json.loads on the repaired string.

    Returns the parsed dict on success, or None on total failure.
    """
    if not text:
        return None

    # 1. Strip ```json ... ``` fence if this looks like fenced JSON
    fence_match = re.search(r"```(?:json|JSON)?\s*\n(.*?)\n```", text, re.DOTALL)
    candidate = fence_match.group(1) if fence_match else text

    # 2. Brace-balanced, string-aware extraction of the first top-level {...}
    start = candidate.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    end = -1
    for i in range(start, len(candidate)):
        ch = candidate[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
    if end == -1:
        # Fall back to last `}` (LLM may have been cut off)
        end = candidate.rfind("}")
        if end <= start:
            return None

    blob = candidate[start : end + 1]

    # 3. Fast path
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        pass

    # 4. Repair common LLM JSON mistakes and retry.
    repaired = _repair_json_string(blob)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse failed even after repair: {e}")
        return None


def _repair_json_string(s: str) -> str:
    """Best-effort repair of common JSON mistakes LLMs make.

    - Replaces smart quotes with ASCII double-quotes (outside of already-escaped content).
    - Removes trailing commas before } or ].
    - Escapes raw newlines / tabs / carriage returns that appear inside string literals.
    """
    # Smart quotes → ASCII
    s = s.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")

    # Remove trailing commas:  ", }"  or  ", ]"
    s = re.sub(r",(\s*[}\]])", r"\1", s)

    # Escape raw control characters inside string literals.
    # State machine: walk the text, when inside a JSON string, replace raw \n, \r, \t with \\n, \\r, \\t.
    out = []
    in_string = False
    escape = False
    for ch in s:
        if in_string:
            if escape:
                out.append(ch)
                escape = False
                continue
            if ch == "\\":
                out.append(ch)
                escape = True
                continue
            if ch == '"':
                out.append(ch)
                in_string = False
                continue
            if ch == "\n":
                out.append("\\n")
                continue
            if ch == "\r":
                out.append("\\r")
                continue
            if ch == "\t":
                out.append("\\t")
                continue
            out.append(ch)
        else:
            if ch == '"':
                in_string = True
            out.append(ch)
    return "".join(out)


def _parse_factcheck_response(raw: str) -> dict:
    """Parse the fact-checker's two-part response: a small JSON block + a delimited markdown block.

    Returns a dict with:
      - 'json':             parsed JSON dict (or {} if missing/unparseable)
      - 'revised_markdown': extracted markdown string (or '' if missing)

    This is deliberately tolerant — we want the fact-checker's work to survive minor format
    deviations rather than silently falling back to the unrevised draft.
    """
    if not raw:
        return {"json": {}, "revised_markdown": ""}

    # --- 1. Extract revised markdown (delimited block) ---
    revised = ""
    # Accept the documented delimiter pair and a few reasonable alternates.
    patterns = [
        r"<<<REVISED_MARKDOWN\s*\n?(.*?)\n?REVISED_MARKDOWN>>>",
        r"<<<REVISED_MARKDOWN\s*\n?(.*?)\n?<<<END_REVISED_MARKDOWN>>>",
        r"<<<REVISED\s*\n?(.*?)\n?REVISED>>>",
    ]
    for pat in patterns:
        m = re.search(pat, raw, re.DOTALL)
        if m:
            revised = m.group(1).strip()
            break

    # --- 2. Extract the JSON object ---
    # Consider the text BEFORE the revised-markdown block so we don't scan the markdown itself.
    json_region = raw
    if revised:
        md_start = raw.find("<<<REVISED")
        if md_start != -1:
            json_region = raw[:md_start]

    parsed = _extract_json_object(json_region) or {}

    return {"json": parsed, "revised_markdown": revised}


def fact_check_entry(context: dict, category: str, draft: str) -> dict:
    """Run the fact-checker second pass on a draft dossier entry.

    Returns a dict with keys:
      - content:            final markdown (revised if fact-checker succeeded, else original draft)
      - confidence:         'high' | 'medium' | 'low' | 'unverified'
      - verdict:            'pass' | 'revised' | 'insufficient_evidence' | 'factcheck_skipped'
      - issues_count:       number of issues the fact-checker flagged
      - issues_summary:     short human-readable summary, or '' if none
    """
    prompt = build_factcheck_prompt(context, category, draft)
    raw = _call_ai(prompt, system=FACT_CHECKER_SYSTEM)

    if not raw or not raw.strip():
        # Fact-checker LLM call itself failed (timeout, auth, network).
        return {
            "content": draft,
            "confidence": "low",
            "verdict": "factcheck_skipped",
            "issues_count": 0,
            "issues_summary": "Fact-checker LLM call failed; returned analyst draft unchanged but "
                               "downgraded confidence because it was not independently verified.",
        }

    parsed = _parse_factcheck_response(raw)
    result_json = parsed["json"]
    revised_md = parsed["revised_markdown"]

    # If we have NEITHER a parsed verdict NOR a revised-markdown block, we truly cannot use the
    # fact-checker's output — fall back to the draft.
    if not result_json and not revised_md:
        logger.warning(
            f"Fact-checker response had no recognizable JSON or delimited markdown; "
            f"first 300 chars: {raw[:300]!r}"
        )
        return {
            "content": draft,
            "confidence": "low",
            "verdict": "factcheck_skipped",
            "issues_count": 0,
            "issues_summary": "Fact-checker returned an unparseable response; "
                               "returned analyst draft unchanged and downgraded confidence.",
        }

    # Prefer the delimited revised markdown; fall back to a JSON field if the model shoved
    # markdown into one; finally fall back to the unrevised draft.
    revised = revised_md or result_json.get("revised_markdown") or draft
    confidence = (result_json.get("overall_confidence") or "low").lower()
    if confidence not in {"high", "medium", "low", "unverified"}:
        confidence = "low"
    verdict = (result_json.get("verdict") or "revised").lower()
    if verdict not in {"pass", "revised", "insufficient_evidence"}:
        verdict = "revised"

    # If we got revised markdown but no JSON verdict, we still have useful work — mark as revised.
    if not result_json and revised_md:
        verdict = "revised"
        confidence = "low"  # conservative: no structured audit to raise confidence
        logger.info("Fact-checker returned markdown but no parseable JSON; accepting revised content at low confidence.")

    issues = result_json.get("issues_found") or []
    critical = sum(1 for i in issues if (i.get("severity") or "").lower() == "critical")
    major = sum(1 for i in issues if (i.get("severity") or "").lower() == "major")
    summary_bits = []
    if critical:
        summary_bits.append(f"{critical} critical")
    if major:
        summary_bits.append(f"{major} major")
    if len(issues) > critical + major:
        summary_bits.append(f"{len(issues) - critical - major} minor")
    issues_summary = (
        f"Fact-checker flagged {', '.join(summary_bits)} issue(s)."
        if summary_bits
        else ("Fact-checker found no issues." if verdict == "pass" else "")
    )

    return {
        "content": revised,
        "confidence": confidence,
        "verdict": verdict,
        "issues_count": len(issues),
        "issues_summary": issues_summary,
    }


def generate_dossier(
    db: Session,
    competitor_id: int,
    categories: Optional[List[str]] = None,
    progress_cb: Optional[callable] = None,
) -> dict:
    """Generate or update dossier entries for a competitor.

    progress_cb (optional): callable(event: str, payload: dict). Events:
      - "context_loaded"  payload={"total_categories": N, "categories": [...]}
      - "category_started" payload={"category": str, "index": i, "total": N}
      - "category_completed" payload={"category": str, "index": i, "confidence": str, "verdict": str}
      - "all_done" payload={"results": dict}
    """
    context = gather_competitor_context(db, competitor_id)
    if not context:
        return {"error": "Competitor not found"}

    if not categories:
        categories = DOSSIER_CATEGORIES

    if progress_cb:
        try:
            progress_cb("context_loaded", {"total_categories": len(categories), "categories": list(categories)})
        except Exception:
            pass

    results = {}
    has_docs = len(context["document_chunks"]) > 0
    for idx, category in enumerate(categories):
        if progress_cb:
            try:
                progress_cb("category_started", {"category": category, "index": idx, "total": len(categories)})
            except Exception:
                pass
        # Pass 1 — analyst draft
        prompt = build_analyst_prompt(context, category)
        draft = _call_ai(prompt)

        if not draft or not draft.strip():
            # LLM was unavailable — write an honest evidence-pending stub and skip the fact-checker.
            ai_error = get_last_ai_error()
            play = CATEGORY_PLAYBOOK.get(category, {})
            stub = _evidence_pending_stub(context["name"], category, play, has_docs, ai_error)
            banner = (
                f"> **Fact-Check Verdict: LLM unavailable.** No analyst draft was produced for this "
                f"category on this attempt, so nothing was fact-checked. See retry instructions below.\n\n"
            )
            content = banner + stub
            confidence = "unverified"
            verdict = "llm_unavailable"
            issues_count = 0
            source_tag = "llm_unavailable"
            if not has_docs:
                source_tag += "_no_docs"
        else:
            # Pass 2 — fact-checker audit
            audit = fact_check_entry(context, category, draft)
            content = audit["content"]
            confidence = audit["confidence"]
            verdict = audit["verdict"]
            issues_summary = audit["issues_summary"]
            issues_count = audit["issues_count"]

            # Prepend a fact-check banner so the UI and any reader can see the verdict at a glance.
            if verdict == "insufficient_evidence":
                banner = (
                    f"> **Fact-Check Verdict: Insufficient evidence.** {issues_summary} "
                    f"The analyst draft relied on claims not supported by uploaded documents or news items; "
                    f"specifics were removed. Upload FOIA'd proposals, filings, or audits to raise confidence.\n\n"
                )
            elif verdict == "revised":
                banner = (
                    f"> **Fact-Check Verdict: Revised.** {issues_summary} "
                    f"Unverified specifics in the original draft were removed or rewritten.\n\n"
                )
            elif verdict == "pass":
                banner = f"> **Fact-Check Verdict: Passed.** Every claim traces back to a provided source.\n\n"
            else:  # factcheck_skipped
                banner = (
                    f"> **Fact-Check Verdict: Not verified.** {issues_summary}\n\n"
                )
            content = banner + content

            source_tag = (
                "analyst_generated+factchecked" if verdict in {"pass", "revised", "insufficient_evidence"}
                else "analyst_generated+factcheck_skipped"
            )
            if not has_docs:
                source_tag += "_no_docs"

        # Upsert dossier entry
        existing = (
            db.query(CompetitorDossier)
            .filter(CompetitorDossier.competitor_id == competitor_id, CompetitorDossier.category == category)
            .first()
        )
        if existing:
            existing.content = content
            existing.confidence = confidence
            existing.title = f"{category.replace('_', ' ').title()} - {context['name']}"
            existing.source = source_tag
            existing.updated_at = datetime.utcnow()
        else:
            db.add(CompetitorDossier(
                competitor_id=competitor_id,
                category=category,
                title=f"{category.replace('_', ' ').title()} - {context['name']}",
                content=content,
                source=source_tag,
                confidence=confidence,
            ))

        results[category] = {
            "status": "generated",
            "confidence": confidence,
            "verdict": verdict,
            "issues": issues_count,
        }

        if progress_cb:
            try:
                progress_cb("category_completed", {
                    "category": category,
                    "index": idx,
                    "confidence": confidence,
                    "verdict": verdict,
                })
            except Exception:
                pass

    db.commit()

    if progress_cb:
        try:
            progress_cb("all_done", {"results": results})
        except Exception:
            pass

    return results


# ── Generate competitor-writer persona ───────────────────────────────

def generate_competitor_persona(db: Session, competitor_id: int) -> Optional[Persona]:
    """Create or update a competitor-writer persona based on the dossier."""
    comp = db.query(Competitor).filter(Competitor.id == competitor_id).first()
    if not comp:
        return None

    # Gather dossier
    dossier_entries = (
        db.query(CompetitorDossier)
        .filter(CompetitorDossier.competitor_id == competitor_id)
        .all()
    )
    dossier_text = "\n\n".join(
        f"### {d.category.replace('_', ' ').title()}\n{d.content}"
        for d in dossier_entries
    ) if dossier_entries else "No intelligence collected yet."

    # Count uploaded documents so the persona knows they exist and should be mined.
    doc_count = (
        db.query(IngestedDocument)
        .filter(IngestedDocument.competitor_id == competitor_id)
        .count()
    )
    research_focus = (comp.description or "").strip() or (
        "No explicit research focus recorded. Assume the reader cares about the division that delivers "
        "vehicle inspection / emissions testing / motor-vehicle services."
    )

    # ── Historical bidding posture ────────────────────────────────
    # Pulled from past bids (pricing model Comparison tab) so the persona writes
    # in line with this competitor's actual tactical history.
    from ..models import CompetitorHistoricalBid, CompetitorBidStrategy  # local import to avoid cycles
    historical_bids = (
        db.query(CompetitorHistoricalBid)
        .filter(CompetitorHistoricalBid.competitor_id == competitor_id)
        .order_by(CompetitorHistoricalBid.bid_year.desc().nullslast())
        .all()
    )
    bid_blocks = []
    for hb in historical_bids:
        strategies = (
            db.query(CompetitorBidStrategy)
            .filter(CompetitorBidStrategy.historical_bid_id == hb.id)
            .all()
        )
        header = (
            f"- **{hb.rfp_name}** ({hb.state or '?'} {hb.bid_year or '?'}, "
            f"{hb.contract_term_years or '?'} yr, "
            f"${(hb.total_value or 0)/1_000_000:.1f}M, outcome: {hb.award_status or 'unknown'})"
        )
        lines = [header]
        if hb.summary:
            lines.append(f"  Summary: {hb.summary}")
        for s in strategies:
            piece = f"  • [{s.strategy_label}] {s.description}"
            if s.evidence:
                piece += f" (evidence: {s.evidence})"
            piece += f" — confidence: {s.confidence}"
            lines.append(piece)
        bid_blocks.append("\n".join(lines))

    if bid_blocks:
        bidding_posture_block = (
            "## Historical Bidding Posture\n"
            f"These are documented tactics {comp.name} has used in past bids. "
            "When you write pricing, technical, or management sections, maintain this posture "
            "unless the RFP specifically contradicts it. Mirror their past phrasing and priorities.\n\n"
            + "\n\n".join(bid_blocks)
        )
    else:
        bidding_posture_block = (
            "## Historical Bidding Posture\n"
            f"No historical bids captured for {comp.name} yet. "
            "Write in a manner consistent with their public dossier."
        )

    system_prompt = f"""You are a response writer acting AS IF you are {comp.name}'s proposal team.

Your job is to write RFP section responses exactly as {comp.name} would write them — based on their
known capabilities, portfolio, voice, strengths, and weaknesses — for the **NJ Motor Vehicle Commission
(MVC) Vehicle Inspection Program**.

## Research Focus for this company
{research_focus}

## Scope
{comp.name} is treated as a **multi-state / multinational** operator. Use their **entire global
portfolio** as evidence when writing responses — past performance in any jurisdiction is fair game,
not just New Jersey. Call out specific contracts, volumes, and locations by name when they strengthen
the response.

## Intelligence Dossier for {comp.name}
{dossier_text}

{bidding_posture_block}

## Uploaded source documents
There are currently **{doc_count}** document(s) uploaded and tagged to {comp.name} in this system
(FOIA responses, past proposals, filings, news clippings, etc.). Their content has already been
synthesized into the dossier above. When writing, prefer facts, phrasing, and proof points that are
consistent with those uploaded sources. If more specificity is needed (e.g., a contract number,
a station count, a financial figure), note inline `[needs upload: <what would help>]` so a human can
add the missing document.

## INSTRUCTIONS
1. Write responses in the voice and style of {comp.name}'s proposal team.
2. Emphasize known strengths; do not hide weaknesses but frame them the way they would.
3. Use specific details from the dossier — contract numbers, technology names, staff counts, volumes,
   jurisdictions. Cite the source in brackets, e.g. `[per FY24 10-K, Transportation Solutions segment]`
   or `[per uploaded: 2023_VA_contract.pdf]`.
4. If the dossier lacks information for a section, write what a company of their profile and scale
   would plausibly propose, AND add `[assumed — verify]`.
5. Be realistic — do not make claims they could not support.
6. Target the NJ MVC Vehicle Inspection Program specifically, but prove capability with their
   multi-state / multinational portfolio.
7. Write at a level that would score competitively on a 0–100 rubric."""

    # Upsert persona
    persona_name = f"{comp.name} - Competitor Writer"
    existing = db.query(Persona).filter(Persona.name == persona_name).first()

    if existing:
        existing.system_prompt = system_prompt
        existing.description = f"AI persona that writes RFP responses as if it were {comp.name}'s proposal team"
        existing.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return existing
    else:
        persona = Persona(
            name=persona_name,
            description=f"AI persona that writes RFP responses as if it were {comp.name}'s proposal team",
            persona_type="competitor_writer",
            competitor_id=competitor_id,
            system_prompt=system_prompt,
            role="competitor_writer",
            expertise_areas=json.dumps(["government_contracting", "vehicle_inspection", "rfp_response"]),
            writing_style=json.dumps({"formality": "high", "detail_level": "comprehensive"}),
            tone="professional",
            audience="government_evaluator",
            is_active=True,
        )
        db.add(persona)
        db.commit()
        db.refresh(persona)
        return persona


# ── Generate predicted response for a section ────────────────────────

def predict_competitor_response(
    db: Session, competitor_id: int, section_id: str, proposal_id: Optional[int] = None
) -> Optional[CompetitorPrediction]:
    """Generate a predicted RFP response for one section as this competitor.

    This is the *adversarial bar* Parsons must clear. The prompt is built to
    extract the strongest realistic response the named competitor could
    submit — informed by their dossier, prior FOIA responses, and the actual
    RFP requirements + rubric criteria the section will be scored on.
    """
    comp = db.query(Competitor).filter(Competitor.id == competitor_id).first()
    if not comp:
        return None

    # Get or create the competitor-writer persona
    persona_name = f"{comp.name} - Competitor Writer"
    persona = db.query(Persona).filter(Persona.name == persona_name).first()
    if not persona:
        persona = generate_competitor_persona(db, competitor_id)

    # Get section info from rubric
    rubric = db.query(ScoringRubric).filter(ScoringRubric.is_default == True).first()
    section_info = None
    if rubric:
        section_info = (
            db.query(ScoringRubricSection)
            .filter(ScoringRubricSection.rubric_id == rubric.id, ScoringRubricSection.section_id == section_id)
            .first()
        )

    section_title = section_info.title if section_info else section_id
    section_weight = section_info.weight_points if section_info else "unknown"

    # ── Pull ACTUAL requirements that fall under this section ───────────
    # Match by (a) rubric mapping, (b) prefix on rfp_requirements.section_id.
    from sqlalchemy import or_
    from ..models import RfpSectionRubricMap, RfpRequirement
    mapped_section_ids = set()
    if proposal_id:
        for m in db.query(RfpSectionRubricMap).filter(
            RfpSectionRubricMap.proposal_id == proposal_id,
            RfpSectionRubricMap.rubric_section_id == section_id,
        ).all():
            mapped_section_ids.add(m.rfp_section_id)
    req_q = db.query(RfpRequirement).filter(
        RfpRequirement.superseded_by_requirement_id.is_(None),
    )
    if proposal_id:
        req_q = req_q.filter(or_(RfpRequirement.proposal_id == proposal_id,
                                  RfpRequirement.proposal_id.is_(None)))
    if mapped_section_ids:
        req_q = req_q.filter(RfpRequirement.section_id.in_(mapped_section_ids))
    else:
        req_q = req_q.filter(RfpRequirement.section_id.like(f"{section_id}%"))
    requirements = req_q.limit(40).all()

    # ── Competitor's known capabilities (dossier excerpts) ───────────────
    from ..models import CompetitorDossier
    dossier_excerpts: list[str] = []
    for d in db.query(CompetitorDossier).filter(
        CompetitorDossier.competitor_id == competitor_id
    ).limit(10).all():
        excerpt = (d.summary or d.content or "")[:600]
        if excerpt:
            dossier_excerpts.append(f"[{d.category or 'general'}] {excerpt}")

    # ── Parsons' draft (so the competitor knows what to beat) ────────────
    from ..models import RfpSectionResponse
    parsons_draft_excerpt = None
    if proposal_id:
        pr = db.query(RfpSectionResponse).filter(
            RfpSectionResponse.proposal_id == proposal_id,
            RfpSectionResponse.rfp_section_id == section_id,
            RfpSectionResponse.author_type == "parsons",
        ).first()
        if pr and pr.response_text:
            parsons_draft_excerpt = (pr.response_text or "")[:1500]

    req_block = "\n".join(
        f"- {r.title} ({r.category or 'requirement'})"
        + (f"\n    description: {(r.description or '')[:280]}" if r.description else "")
        for r in requirements[:25]
    ) or "(no requirement detail wired for this section)"
    dossier_block = "\n\n".join(dossier_excerpts) or "(no dossier intelligence on file yet)"
    rubric_block = (
        f"Title: {section_info.title}\n"
        f"Weight: {section_info.weight_points} points\n"
        f"Pass/fail: {section_info.pass_fail}\n"
        f"Evaluation criteria:\n{section_info.criteria or '(none specified)'}\n"
    ) if section_info else "(no rubric metadata)"

    prompt = f"""You are writing the strongest realistic RFP response section that
{comp.name} could plausibly submit, given everything we know about their actual
capabilities. This is for the NJ Motor Vehicle Commission Vehicle Inspection
Program RFP.

DO NOT exceed what {comp.name} can actually deliver — but DO present their
real capabilities in the most evaluator-favorable light. The bar must be
realistic; if you over-claim, the prediction is useless.

Section: **{section_title}** (rubric id: {section_id})

RFP REQUIREMENTS THIS SECTION MUST ADDRESS
{req_block}

RUBRIC THE EVALUATOR WILL APPLY
{rubric_block}

WHAT WE KNOW ABOUT {comp.name.upper()} (dossier excerpts)
{dossier_block}

{"PARSONS' CURRENT DRAFT (FOR YOUR INTELLIGENCE — beat its strongest claims):" if parsons_draft_excerpt else ""}
{parsons_draft_excerpt or ""}

Output a complete response section, 800–1500 words, structured with clear
subsection headings, written in the voice of {comp.name}'s capture team.
Specific technologies, named methodologies, real staff disciplines,
quantified past-performance where the dossier supports it.

After the response, add a "## Reasoning" subsection (1–2 paragraphs) that
explains WHY you wrote it that way and what intelligence informed each move.
"""

    system = persona.system_prompt if persona and persona.system_prompt else f"You are writing RFP responses as {comp.name}."
    full_response = _call_ai(prompt, system=system)

    # Split response and reasoning
    response_text = full_response
    reasoning = None
    if "reasoning" in full_response.lower():
        parts = full_response.rsplit("##", 1)
        if len(parts) == 2:
            response_text = parts[0].strip()
            reasoning = parts[1].strip()

    # Upsert prediction
    existing = (
        db.query(CompetitorPrediction)
        .filter(
            CompetitorPrediction.competitor_id == competitor_id,
            CompetitorPrediction.section_id == section_id,
        )
        .first()
    )

    if existing:
        existing.predicted_response = response_text
        existing.reasoning = reasoning
        existing.generated_by_persona = persona.id if persona else None
        existing.model_used = "ai"
        existing.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        return existing
    else:
        pred = CompetitorPrediction(
            competitor_id=competitor_id,
            proposal_id=proposal_id,
            section_id=section_id,
            predicted_response=response_text,
            reasoning=reasoning,
            confidence_score=50,
            generated_by_persona=persona.id if persona else None,
            model_used="ai",
        )
        db.add(pred)
        db.commit()
        db.refresh(pred)
        return pred
