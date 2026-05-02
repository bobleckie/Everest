"""
Knowledge Base service — smart ingestion pipeline.

After raw document parsing, this service:
1. Generates embeddings for each chunk
2. Classifies each chunk by content type and RFP section relevance
3. Extracts key facts (metrics, certifications, names, dates, dollar amounts)
4. Updates the chunk records with enriched metadata

Also provides semantic search across the knowledge base.
"""
import json
import logging
import re
from typing import List, Optional, Dict
from datetime import datetime

from sqlalchemy.orm import Session

from ..models import (
    DocumentChunk, IngestedDocument, ExtractedFact, RfpRequirement,
    ScoringRubric, ScoringRubricSection,
)
from .embedding_service import embed_texts, embed_text, find_similar_chunks, cosine_similarity

logger = logging.getLogger(__name__)

# ── Section definitions for classification ───────────────────────────

SECTION_KEYWORDS = {
    "vendorLegal": ["legal entity", "registration", "tax id", "federal tax", "business registration", "incorporation", "liability"],
    "certifications": ["eeo", "affirmative action", "minority business", "certification", "compliance officer", "mbe", "wbe", "dbe"],
    "technicalProposal": ["technical approach", "system architecture", "software", "hardware", "integration", "viis", "obd", "emissions", "diagnostic", "technology platform"],
    "projectManagement": ["project management", "schedule", "gantt", "milestone", "risk management", "pmp", "agile", "implementation plan", "timeline"],
    "operations": ["operations", "facility", "location", "capacity", "throughput", "maintenance", "operating hours", "station"],
    "technology": ["viis", "information system", "cloud", "data center", "network", "cybersecurity", "it infrastructure", "api", "database"],
    "staffing": ["staffing", "personnel", "training", "organizational chart", "org chart", "workforce", "employee", "recruitment", "retention", "union", "cba"],
    "customerService": ["customer service", "wait time", "satisfaction", "complaint", "public information", "accessibility", "outreach", "call center"],
    "security": ["security", "audit", "compliance", "monitoring", "breach", "incident response", "access control", "background check", "clearance"],
    "smallBusiness": ["subcontract", "small business", "minority", "disadvantaged", "diversity", "sbe", "mwbe", "supplier diversity"],
    "experience": ["past performance", "experience", "reference", "contract history", "award", "similar project", "years of experience", "track record"],
    "costProposal": ["cost", "price", "budget", "labor rate", "overhead", "profit", "fee", "pricing", "financial", "burden rate", "fringe"],
    "transition": ["transition", "close-out", "handover", "knowledge transfer", "incumbent", "phase-in", "phase-out"],
}

CONTENT_CLASSIFICATIONS = {
    "requirement": ["shall", "must", "required to", "mandatory", "the contractor shall", "the vendor must", "requirement"],
    "past_performance": ["past performance", "contract history", "successfully completed", "reference", "award", "recognition"],
    "technical": ["system", "architecture", "software", "hardware", "integration", "platform", "technology", "infrastructure"],
    "staffing": ["staff", "personnel", "employee", "workforce", "training", "organizational", "labor", "union"],
    "cost": ["cost", "price", "rate", "budget", "financial", "billing", "invoice", "overhead", "profit"],
    "compliance": ["compliance", "regulation", "law", "statute", "requirement", "certification", "license", "permit"],
    "general": [],
}

# ── Fact extraction patterns ─────────────────────────────────────────

FACT_PATTERNS = [
    ("metric", r'(\d+\.?\d*)\s*%\s*(uptime|availability|satisfaction|accuracy|compliance|reduction|improvement)', r'\1% \2'),
    ("contract_value", r'\$\s*([\d,]+(?:\.\d+)?)\s*(million|billion|M|B|k|K)?', r'$\1\2'),
    ("staff_count", r'(\d{2,})\s+(employees?|staff|personnel|inspectors?|technicians?|workers?)', r'\1 \2'),
    ("certification", r'(ISO\s*\d+|CMMI|PMP|ITIL|SOC\s*[12]|FedRAMP|REAL\s*ID|CDL)', r'\1'),
    ("date", r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}', None),
    ("location", r'(Secaucus|Rahway|Wayne|Paramus|Newark|Trenton|Freehold|Camden|Cherry Hill|Eatontown|Bakers Basin|Lodi|Flemington|Kilmer|Randolph|Salem|South Brunswick|Washington|West Deptford)', None),
]


# ── Main enrichment pipeline ────────────────────────────────────────

def enrich_chunks(db: Session, document_id: int) -> dict:
    """
    Run the full enrichment pipeline on all chunks of a document:
    1. Generate embeddings
    2. Classify by content type
    3. Tag with relevant RFP sections
    4. Extract key facts
    """
    doc = db.query(IngestedDocument).filter(IngestedDocument.id == document_id).first()
    if not doc:
        return {"error": "Document not found"}

    chunks = (
        db.query(DocumentChunk)
        .filter(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.chunk_index)
        .all()
    )
    if not chunks:
        return {"error": "No chunks found"}

    texts = [c.content for c in chunks]

    # 1. Generate embeddings
    logger.info(f"Generating embeddings for {len(texts)} chunks of doc {document_id}")
    embeddings = embed_texts(texts)

    # 2-4. Classify, tag, extract for each chunk
    total_facts = 0
    for chunk, embedding in zip(chunks, embeddings):
        chunk.embedding = json.dumps(embedding)

        # Classify content type
        chunk.classification = classify_content(chunk.content)

        # Tag relevant sections
        chunk.section_tags = json.dumps(tag_sections(chunk.content))

        # Extract facts
        facts = extract_facts(chunk.content, doc.source_type, doc.competitor_id)
        chunk.key_facts = json.dumps([f["fact_key"] + ": " + f["fact_value"] for f in facts])

        # Store facts in dedicated table
        for fact in facts:
            existing = (
                db.query(ExtractedFact)
                .filter(
                    ExtractedFact.chunk_id == chunk.id,
                    ExtractedFact.fact_key == fact["fact_key"],
                    ExtractedFact.fact_value == fact["fact_value"],
                )
                .first()
            )
            if not existing:
                db.add(ExtractedFact(
                    chunk_id=chunk.id,
                    document_id=document_id,
                    owner_type="competitor" if doc.competitor_id else ("parsons" if doc.source_type == "parsons" else "reference"),
                    owner_id=doc.competitor_id,
                    fact_type=fact["fact_type"],
                    fact_key=fact["fact_key"],
                    fact_value=fact["fact_value"],
                    context=chunk.content[:500],
                    section_relevance=chunk.section_tags,
                    confidence="medium",
                ))
                total_facts += 1

    db.commit()
    logger.info(f"Enriched doc {document_id}: {len(chunks)} chunks, {total_facts} facts extracted")

    return {
        "document_id": document_id,
        "chunks_enriched": len(chunks),
        "facts_extracted": total_facts,
    }


def classify_content(text: str) -> str:
    """Classify a chunk by its primary content type."""
    text_lower = text.lower()
    scores = {}
    for cls, keywords in CONTENT_CLASSIFICATIONS.items():
        if cls == "general":
            continue
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[cls] = score
    if scores:
        return max(scores, key=scores.get)
    return "general"


def tag_sections(text: str) -> List[str]:
    """Tag a chunk with the RFP sections it's relevant to."""
    text_lower = text.lower()
    tags = []
    for section_id, keywords in SECTION_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score >= 2:  # need at least 2 keyword matches
            tags.append(section_id)
    return tags


def extract_facts(text: str, source_type: str = "reference", competitor_id: Optional[int] = None) -> List[dict]:
    """Extract structured facts from text using regex patterns."""
    facts = []
    for fact_type, pattern, value_fmt in FACT_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            if value_fmt:
                try:
                    value = re.sub(pattern, value_fmt, match.group(0), flags=re.IGNORECASE)
                except Exception:
                    value = match.group(0)
            else:
                value = match.group(0)

            fact_key = f"{fact_type}_{value.lower().replace(' ', '_')[:50]}"
            facts.append({
                "fact_type": fact_type,
                "fact_key": fact_key,
                "fact_value": value,
            })
    return facts


# ── Semantic search ──────────────────────────────────────────────────

def semantic_search(
    db: Session,
    query: str,
    source_type: Optional[str] = None,
    competitor_id: Optional[int] = None,
    section_id: Optional[str] = None,
    top_k: int = 10,
    document_ids: Optional[List[int]] = None,
) -> List[dict]:
    """Search the knowledge base using semantic similarity.

    If `document_ids` is supplied the search is restricted to chunks belonging
    to those documents. This lets the UI scope a search to any subset of the
    knowledge base (e.g. only the active RFP, or a specific competitor's FOIA
    proposal, or an arbitrary multi-select).
    """
    query_emb = embed_text(query)

    # Build query — exclude superseded docs by default so cross-corpus
    # search reflects the current truth set.
    q = db.query(DocumentChunk).join(
        IngestedDocument, DocumentChunk.document_id == IngestedDocument.id
    ).filter(IngestedDocument.superseded_by_document_id.is_(None))
    if source_type:
        q = q.filter(IngestedDocument.source_type == source_type)
    if competitor_id:
        q = q.filter(IngestedDocument.competitor_id == competitor_id)
    if document_ids:
        q = q.filter(DocumentChunk.document_id.in_(document_ids))

    chunks = q.filter(DocumentChunk.embedding.isnot(None)).all()

    # Batch lookup of document metadata so we can surface filename + source_type
    doc_ids = {c.document_id for c in chunks if c.document_id is not None}
    docs = (
        db.query(IngestedDocument).filter(IngestedDocument.id.in_(doc_ids)).all()
        if doc_ids else []
    )
    doc_meta = {
        d.id: {
            "filename": d.original_filename or d.filename,
            "source_type": d.source_type,
            "proposal_id": d.proposal_id,
            "competitor_id": d.competitor_id,
            "document_type": d.document_type,
        }
        for d in docs
    }

    # Score and rank
    results = []
    for chunk in chunks:
        try:
            chunk_emb = json.loads(chunk.embedding)
        except (json.JSONDecodeError, TypeError):
            continue

        sim = cosine_similarity(query_emb, chunk_emb)

        # Boost if section matches
        if section_id and chunk.section_tags:
            tags = json.loads(chunk.section_tags)
            if section_id in tags:
                sim += 0.1  # boost

        meta = doc_meta.get(chunk.document_id, {})
        results.append({
            "chunk_id": chunk.id,
            "document_id": chunk.document_id,
            "document_name": meta.get("filename"),
            "source_type": meta.get("source_type"),
            "proposal_id": meta.get("proposal_id"),
            "competitor_id": meta.get("competitor_id"),
            "document_type": meta.get("document_type"),
            "content": chunk.content,
            "page_number": chunk.page_number,
            "classification": chunk.classification,
            "section_tags": json.loads(chunk.section_tags) if chunk.section_tags else [],
            "key_facts": json.loads(chunk.key_facts) if chunk.key_facts else [],
            "similarity": round(sim, 4),
        })

    results.sort(key=lambda x: x["similarity"], reverse=True)
    return results[:top_k]


# ── Knowledge Base readiness assessment ──────────────────────────────

def assess_readiness(db: Session) -> dict:
    """
    Assess knowledge base coverage per RFP section.
    Returns coverage metrics for Parsons and each competitor.
    """
    rubric = db.query(ScoringRubric).filter(ScoringRubric.is_default == True).first()
    if not rubric:
        return {"error": "No rubric found"}

    sections = (
        db.query(ScoringRubricSection)
        .filter(ScoringRubricSection.rubric_id == rubric.id)
        .order_by(ScoringRubricSection.sort_order)
        .all()
    )

    # Count chunks tagged to each section, by source
    parsons_chunks = (
        db.query(DocumentChunk)
        .join(IngestedDocument, DocumentChunk.document_id == IngestedDocument.id)
        .filter(IngestedDocument.source_type == "parsons")
        .filter(DocumentChunk.section_tags.isnot(None))
        .all()
    )

    competitor_chunks = (
        db.query(DocumentChunk)
        .join(IngestedDocument, DocumentChunk.document_id == IngestedDocument.id)
        .filter(IngestedDocument.source_type.in_(["competitor_foia", "competitor_proposal"]))
        .filter(DocumentChunk.section_tags.isnot(None))
        .all()
    )

    rfp_chunks = (
        db.query(DocumentChunk)
        .join(IngestedDocument, DocumentChunk.document_id == IngestedDocument.id)
        .filter(IngestedDocument.source_type == "rfp")
        .filter(DocumentChunk.section_tags.isnot(None))
        .all()
    )

    # Count requirements per section
    requirements = db.query(RfpRequirement).all()

    readiness = []
    for section in sections:
        sid = section.section_id

        parsons_count = sum(1 for c in parsons_chunks if sid in (json.loads(c.section_tags) if c.section_tags else []))
        competitor_count = sum(1 for c in competitor_chunks if sid in (json.loads(c.section_tags) if c.section_tags else []))
        rfp_count = sum(1 for c in rfp_chunks if sid in (json.loads(c.section_tags) if c.section_tags else []))
        req_count = sum(1 for r in requirements if r.section_id == sid)
        req_compliant = sum(1 for r in requirements if r.section_id == sid and r.compliance_status == "compliant")

        readiness.append({
            "section_id": sid,
            "title": section.title,
            "weight": section.weight_points,
            "pass_fail": section.pass_fail,
            "parsons_chunks": parsons_count,
            "competitor_chunks": competitor_count,
            "rfp_chunks": rfp_count,
            "requirements_total": req_count,
            "requirements_compliant": req_compliant,
            "readiness_score": _calc_readiness(parsons_count, rfp_count, req_count, req_compliant),
        })

    # Facts summary
    parsons_facts = db.query(ExtractedFact).filter(ExtractedFact.owner_type == "parsons").count()
    competitor_facts = db.query(ExtractedFact).filter(ExtractedFact.owner_type == "competitor").count()

    return {
        "sections": readiness,
        "totals": {
            "parsons_chunks": sum(r["parsons_chunks"] for r in readiness),
            "competitor_chunks": sum(r["competitor_chunks"] for r in readiness),
            "rfp_chunks": sum(r["rfp_chunks"] for r in readiness),
            "total_requirements": len(requirements),
            "compliant_requirements": sum(1 for r in requirements if r.compliance_status == "compliant"),
            "parsons_facts": parsons_facts,
            "competitor_facts": competitor_facts,
        },
    }


def _calc_readiness(parsons_chunks: int, rfp_chunks: int, req_total: int, req_compliant: int) -> int:
    """Calculate a 0-100 readiness score for a section."""
    score = 0
    # Has Parsons content (40 points)
    if parsons_chunks > 0:
        score += min(40, parsons_chunks * 10)
    # Has RFP requirements mapped (30 points)
    if req_total > 0:
        score += int(30 * req_compliant / req_total)
    elif rfp_chunks > 0:
        score += 15  # at least RFP content exists
    # Has supporting intel (30 points)
    if rfp_chunks > 0:
        score += 15
    if parsons_chunks >= 3:
        score += 15
    return min(100, score)
