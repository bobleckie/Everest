"""
Citation resolver for RFP requirements.

Given a RfpRequirement row, extract internal document cross-references from its
`source_text` + `description` (e.g. "see Section 3.2", "per Appendix B",
"Attachment 4", "Exhibit 1", "Schedule A", "Paragraph 5(b)") and resolve each
one against other data in the same proposal:

  - Other RfpRequirement rows with a matching `section_id` (exact, then prefix)
  - DocumentChunk rows whose text contains a heading matching the token

The resolver is read-only and fast (no LLM calls). Designed to be called
on-demand from the UI when a user opens a requirement.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from ..models import RfpRequirement, DocumentChunk, IngestedDocument


# ── Citation extraction ──────────────────────────────────────────────
#
# These patterns are intentionally permissive on the leading verb ("see",
# "per", "refer to", or nothing) and strict on the payload. Ordering matters:
# more specific kinds come first so that "Attachment 2" isn't also swallowed
# by the generic "Section" pattern.

# A section number looks like 3, 3.2, 3.2.1, 3.2(a), 4.11(b)(2), etc.
# We keep the trailing sub-part because section_ids in the DB often include them.
_SECTION_NUM = r"\d+(?:\.\d+)*(?:\([a-zA-Z0-9]+\))*"

# Each pattern returns group("ref") = the human-readable reference text
# (e.g. "3.2.1", "Attachment 2"). `kind` tells the UI which icon/label to use.
_PATTERNS: List[Tuple[str, re.Pattern]] = [
    # Appendix A / Appendix 3
    ("appendix", re.compile(
        r"\bAppendix\s+(?P<ref>[A-Z]\d*|\d+)\b",
        re.IGNORECASE,
    )),
    # Attachment 4 / Attachment A
    ("attachment", re.compile(
        r"\bAttachment\s+(?P<ref>[A-Z]\d*|\d+)\b",
        re.IGNORECASE,
    )),
    # Exhibit B / Exhibit 2
    ("exhibit", re.compile(
        r"\bExhibit\s+(?P<ref>[A-Z]\d*|\d+)\b",
        re.IGNORECASE,
    )),
    # Schedule A
    ("schedule", re.compile(
        r"\bSchedule\s+(?P<ref>[A-Z]\d*|\d+)\b",
        re.IGNORECASE,
    )),
    # Section 3.2 / Section 3.2.1(a) / § 3.2
    ("section", re.compile(
        r"(?:\bSection\s+|§\s*)(?P<ref>" + _SECTION_NUM + r")",
        re.IGNORECASE,
    )),
    # "Paragraph 5(b)"
    ("paragraph", re.compile(
        r"\bParagraph\s+(?P<ref>" + _SECTION_NUM + r")",
        re.IGNORECASE,
    )),
    # "Article 7"
    ("article", re.compile(
        r"\bArticle\s+(?P<ref>" + _SECTION_NUM + r")",
        re.IGNORECASE,
    )),
    # "Clause 4.11"
    ("clause", re.compile(
        r"\bClause\s+(?P<ref>" + _SECTION_NUM + r")",
        re.IGNORECASE,
    )),
]


@dataclass
class ExtractedCitation:
    """A raw citation token lifted from text, before resolution."""
    kind: str                  # section | attachment | appendix | ...
    ref: str                   # e.g. "3.2.1" or "A"
    display_text: str          # the exact substring as it appeared in the source
    start: int                 # character offset within the scanned text


def extract_citations(text: Optional[str]) -> List[ExtractedCitation]:
    """Scan `text` and return one entry per unique citation found.

    Uniqueness is on (kind, ref) — if the same reference appears three times
    we only return it once. The first match's display_text/offset is kept.
    """
    if not text:
        return []
    out: List[ExtractedCitation] = []
    seen: set = set()
    for kind, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            ref = (m.group("ref") or "").strip()
            if not ref:
                continue
            key = (kind, ref.lower())
            if key in seen:
                continue
            seen.add(key)
            out.append(ExtractedCitation(
                kind=kind,
                ref=ref,
                display_text=m.group(0),
                start=m.start(),
            ))
    out.sort(key=lambda c: c.start)
    return out


# ── Resolution ───────────────────────────────────────────────────────

@dataclass
class ResolvedMatch:
    """One place where a citation was found."""
    source: str                # "requirement" | "chunk"
    document_id: int
    document_name: str
    page: Optional[int] = None
    snippet: str = ""          # short excerpt with the hit highlighted
    # requirement-specific
    requirement_id: Optional[int] = None
    requirement_ref: Optional[str] = None  # the "REQ-001" style id
    requirement_title: Optional[str] = None
    requirement_section: Optional[str] = None


@dataclass
class ResolvedCitation:
    kind: str
    ref: str
    display_text: str
    matches: List[ResolvedMatch] = field(default_factory=list)


# A short context window for snippet generation — enough to show the heading
# plus a little of the paragraph underneath.
_SNIPPET_BEFORE = 40
_SNIPPET_AFTER = 320


def _make_snippet(content: str, match_start: int, match_end: int) -> str:
    lo = max(0, match_start - _SNIPPET_BEFORE)
    hi = min(len(content), match_end + _SNIPPET_AFTER)
    prefix = "…" if lo > 0 else ""
    suffix = "…" if hi < len(content) else ""
    return (prefix + content[lo:hi] + suffix).strip()


def _section_matches(section_id: Optional[str], ref: str) -> bool:
    """Return True if `section_id` matches `ref` exactly or as a prefix path.

    "3.2" matches "3.2", "3.2.1", "3.2(a)", but NOT "3.20".
    """
    if not section_id:
        return False
    a = section_id.strip().lower()
    b = ref.strip().lower()
    if a == b:
        return True
    if a.startswith(b + ".") or a.startswith(b + "("):
        return True
    return False


def _build_chunk_patterns(citation: ExtractedCitation) -> List[re.Pattern]:
    """Build the regex(es) we'll use to find this citation inside chunk text.

    We look for the citation as a heading-like occurrence so we don't match
    stray inline mentions.
    """
    ref_esc = re.escape(citation.ref)
    if citation.kind == "section":
        return [
            re.compile(r"(?m)^\s*" + ref_esc + r"[\s\.\)\-:]"),
            re.compile(r"\bSection\s+" + ref_esc + r"\b", re.IGNORECASE),
        ]
    if citation.kind == "paragraph":
        return [re.compile(r"\bParagraph\s+" + ref_esc + r"\b", re.IGNORECASE)]
    if citation.kind == "article":
        return [re.compile(r"\bArticle\s+" + ref_esc + r"\b", re.IGNORECASE)]
    if citation.kind == "clause":
        return [re.compile(r"\bClause\s+" + ref_esc + r"\b", re.IGNORECASE)]
    # attachment/appendix/exhibit/schedule — these usually show as headings
    label = citation.kind.capitalize()
    return [re.compile(r"\b" + label + r"\s+" + ref_esc + r"\b", re.IGNORECASE)]


# Hard caps so a single requirement on a giant proposal doesn't scan 10k chunks.
_MAX_CHUNKS_SCANNED = 2000
_MAX_MATCHES_PER_CITATION = 5


def resolve_citations(
    db: Session,
    requirement: RfpRequirement,
) -> List[ResolvedCitation]:
    """Extract citations from the requirement's text and resolve each one.

    Resolution order:
      1. Match against other RfpRequirement rows in the same proposal whose
         section_id matches (exact, then prefix). Cheap.
      2. Scan DocumentChunk rows in the same proposal (or the same document
         when proposal_id is null) for heading-like occurrences. Capped at
         _MAX_CHUNKS_SCANNED rows to stay fast.
    """
    haystack = " ".join(filter(None, [
        requirement.source_text or "",
        requirement.description or "",
    ]))
    raw = extract_citations(haystack)
    if not raw:
        return []

    # Scope for lookups — same proposal when we have one, otherwise same doc.
    proposal_id = requirement.proposal_id
    document_id = requirement.document_id

    # Pre-load siblings once.
    req_q = db.query(RfpRequirement).filter(RfpRequirement.id != requirement.id)
    if proposal_id is not None:
        req_q = req_q.filter(RfpRequirement.proposal_id == proposal_id)
    elif document_id is not None:
        req_q = req_q.filter(RfpRequirement.document_id == document_id)
    else:
        req_q = req_q.filter(False)  # nothing to scope against
    sibling_reqs = req_q.all()

    chunk_q = db.query(DocumentChunk).join(
        IngestedDocument, DocumentChunk.document_id == IngestedDocument.id
    )
    if proposal_id is not None:
        chunk_q = chunk_q.filter(IngestedDocument.proposal_id == proposal_id)
    elif document_id is not None:
        chunk_q = chunk_q.filter(DocumentChunk.document_id == document_id)
    else:
        chunk_q = chunk_q.filter(False)
    chunks = (
        chunk_q.order_by(DocumentChunk.document_id, DocumentChunk.chunk_index)
        .limit(_MAX_CHUNKS_SCANNED)
        .all()
    )

    # Small cache so we don't hit the DB once per chunk for the document name.
    doc_ids_needed = {c.document_id for c in chunks}
    doc_ids_needed |= {r.document_id for r in sibling_reqs if r.document_id is not None}
    docs = (
        db.query(IngestedDocument)
        .filter(IngestedDocument.id.in_(doc_ids_needed))
        .all()
    ) if doc_ids_needed else []
    doc_name = {d.id: (d.original_filename or d.filename) for d in docs}

    results: List[ResolvedCitation] = []
    for cit in raw:
        resolved = ResolvedCitation(
            kind=cit.kind,
            ref=cit.ref,
            display_text=cit.display_text,
            matches=[],
        )

        # ── (1) Requirement match by section_id (only makes sense for
        #         section-like kinds) ──────────────────────────────────
        if cit.kind in {"section", "paragraph", "clause", "article"}:
            # Exact first, then prefix.
            exact = [
                r for r in sibling_reqs
                if (r.section_id or "").strip().lower() == cit.ref.strip().lower()
            ]
            prefix = [
                r for r in sibling_reqs
                if r not in exact and _section_matches(r.section_id, cit.ref)
            ]
            for r in (exact + prefix)[:_MAX_MATCHES_PER_CITATION]:
                resolved.matches.append(ResolvedMatch(
                    source="requirement",
                    document_id=r.document_id or 0,
                    document_name=doc_name.get(r.document_id, "") or "",
                    page=r.source_page,
                    snippet=(r.source_text or r.description or "")[:480].strip(),
                    requirement_id=r.id,
                    requirement_ref=r.requirement_id,
                    requirement_title=r.title,
                    requirement_section=r.section_id,
                ))

        # ── (2) Chunk scan — always run so we catch headings that aren't
        #         tied to an extracted requirement (attachments, exhibits,
        #         and sections whose heading exists but which weren't
        #         extracted as a requirement themselves) ───────────────
        if len(resolved.matches) < _MAX_MATCHES_PER_CITATION:
            patterns = _build_chunk_patterns(cit)
            for ch in chunks:
                if len(resolved.matches) >= _MAX_MATCHES_PER_CITATION:
                    break
                content = ch.content or ""
                if not content:
                    continue
                hit = None
                for pat in patterns:
                    m = pat.search(content)
                    if m:
                        hit = m
                        break
                if not hit:
                    continue
                # Skip duplicate (document_id, page) we already recorded from a
                # requirement match on this same citation.
                existing = {
                    (m.document_id, m.page) for m in resolved.matches
                }
                if (ch.document_id, ch.page_number) in existing:
                    continue
                resolved.matches.append(ResolvedMatch(
                    source="chunk",
                    document_id=ch.document_id,
                    document_name=doc_name.get(ch.document_id, "") or "",
                    page=ch.page_number,
                    snippet=_make_snippet(content, hit.start(), hit.end()),
                ))

        results.append(resolved)

    return results


def resolve_citations_as_dict(
    db: Session,
    requirement: RfpRequirement,
) -> List[dict]:
    """API-friendly view (JSON-serializable)."""
    out = []
    for rc in resolve_citations(db, requirement):
        out.append({
            "kind": rc.kind,
            "ref": rc.ref,
            "display_text": rc.display_text,
            "matches": [asdict(m) for m in rc.matches],
        })
    return out
