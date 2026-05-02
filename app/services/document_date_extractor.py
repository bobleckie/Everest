"""Extract a document's own *issuance / effective date* — from inside the
document text, not from filesystem metadata.

Why this exists
---------------
The baseline consolidator must apply amendments in the order they were
ISSUED by the agency. The order a user types in a textbox is best-guess.
File-creation timestamps are unreliable (a doc created on disk in 2026
may carry an issuance date of "March 1, 2021"). The right source of
truth is the date stamped on the document itself — and most procurement
amendments put that date prominently in their first page or two.

Strategy
--------
1. **Regex pass over the first N chunks**: catches the obvious patterns
   like ``"Date Issued: 03/01/2021"``, ``"Effective Date: 04/26/2021"``,
   ``"Amendment dated June 23, 2021"``.
2. **Filename hint fallback**: if the body extraction fails, try parsing
   a date out of the filename — many of the user's amendments have
   dates in their names (``T1628 Bid Amendment 18 06.04.2021.docx``).
3. **LLM fallback**: if both deterministic passes fail, ask the LLM to
   read the first few chunks and return ``{"date": "YYYY-MM-DD"|null,
   "confidence": ..., "rationale": ...}``.

Each result carries a ``source`` so the UI can show whether the date
came from regex / filename / LLM and what its confidence is.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..models import DocumentChunk, IngestedDocument
from .orchestrator_agent import _call_ai

logger = logging.getLogger(__name__)


# How many leading chunks to scan for a date. Procurement amendments put
# the date in the header / first paragraph, so 5 is plenty.
MAX_LEADING_CHUNKS = 5
# How much body text to feed the LLM fallback.
MAX_LLM_BODY_CHARS = 8000


# ─────────────────────────────────────────────────────────────────────
# Regex patterns — ordered by specificity
# ─────────────────────────────────────────────────────────────────────

# Full month names (English). Captures groups: month / day / year.
_MONTH_NAMES = (
    r"January|February|March|April|May|June|July|August|September|"
    r"October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)

# Looks for a date that's preceded by an issuance-cue word like
# "Date Issued", "Issued", "Effective", "Dated", "Date", "as of"
_CUE = (
    r"(?:Date\s+Issued|Issued\s+on|Issued|Effective\s+Date|Effective|"
    r"Amended|Amendment\s+Date|Date\s+of\s+Amendment|Dated|"
    r"Date|As\s+of)"
)

_DATE_PATTERNS: List[Tuple[str, str]] = [
    # "Date Issued: 03/01/2021" / "Effective: 04-26-2021"
    (r"\b" + _CUE + r"\s*[:\-]?\s*(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\b",
     "cue_numeric"),
    # "Date Issued: March 1, 2021"
    (r"\b" + _CUE + r"\s*[:\-]?\s*(" + _MONTH_NAMES + r")\.?\s+(\d{1,2}),?\s+(\d{4})\b",
     "cue_month_name"),
    # bare date 03/01/2021 in early text (no cue word)
    (r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\b",
     "bare_numeric"),
    # bare "March 1, 2021"
    (r"\b(" + _MONTH_NAMES + r")\.?\s+(\d{1,2}),?\s+(\d{4})\b",
     "bare_month_name"),
]

_MONTH_LOOKUP = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sept": 9, "sep": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}


def _normalize_year(y: int) -> int:
    """2-digit year heuristic — treat 00–69 as 2000s, 70–99 as 1900s."""
    if y < 100:
        return 2000 + y if y < 70 else 1900 + y
    return y


def _try_make_date(year: int, month: int, day: int) -> Optional[date]:
    try:
        return date(_normalize_year(year), month, day)
    except ValueError:
        return None


def _scan_text_for_date(
    text: str,
    *,
    cue_only: bool = False,
) -> Optional[Tuple[date, str, str]]:
    """Return (parsed_date, source_kind, raw_match) or None.

    When ``cue_only=True``, only matches preceded by a cue word
    ("Date Issued", "Effective", etc.) are accepted — used by the
    primary pass so we don't pick up embedded statute citations.
    """
    if not text:
        return None
    patterns = _DATE_PATTERNS
    if cue_only:
        patterns = [(p, k) for p, k in _DATE_PATTERNS if k.startswith("cue_")]
    for pattern, kind in patterns:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            groups = m.groups()
            if not groups:
                continue
            try:
                if kind in ("cue_numeric", "bare_numeric"):
                    a, b, c = (int(g) for g in groups[-3:])
                    parsed = _try_make_date(c, a, b)
                    if parsed and 2000 <= parsed.year <= datetime.utcnow().year + 1:
                        return parsed, kind, m.group(0)[:120]
                elif kind in ("cue_month_name", "bare_month_name"):
                    name, day_str, year_str = groups[-3:]
                    month = _MONTH_LOOKUP.get(name.lower().strip("."))
                    if not month:
                        continue
                    parsed = _try_make_date(int(year_str), month, int(day_str))
                    if parsed and 2000 <= parsed.year <= datetime.utcnow().year + 1:
                        return parsed, kind, m.group(0)[:120]
            except (ValueError, IndexError):
                continue
    return None


# ─────────────────────────────────────────────────────────────────────
# Filename hint extractor — many docs encode date in their name
# ─────────────────────────────────────────────────────────────────────

def _scan_filename_for_date(name: str) -> Optional[Tuple[date, str, str]]:
    if not name:
        return None
    # Common patterns: "06.04.2021", "082819" (mmddyy), "2021-04-26"
    patterns = [
        # YYYY-MM-DD
        r"(20\d{2})[\-_.](\d{1,2})[\-_.](\d{1,2})",
        # MM.DD.YYYY / MM-DD-YYYY / MM/DD/YYYY
        r"(\d{1,2})[\-_./](\d{1,2})[\-_./](20\d{2})",
        # MMDDYY (e.g. 082819)
        r"(?:^|[^\d])(\d{2})(\d{2})(\d{2})(?=[^\d]|$)",
    ]
    for i, pat in enumerate(patterns):
        m = re.search(pat, name)
        if not m:
            continue
        gs = m.groups()
        try:
            if i == 0:  # YYYY-MM-DD
                y, mo, d = int(gs[0]), int(gs[1]), int(gs[2])
            elif i == 1:  # MM-DD-YYYY
                mo, d, y = int(gs[0]), int(gs[1]), int(gs[2])
            else:  # MMDDYY
                mo, d, y = int(gs[0]), int(gs[1]), int(gs[2])
                y = _normalize_year(y)
            parsed = _try_make_date(y, mo, d)
            if parsed and 2000 <= parsed.year <= datetime.utcnow().year + 1:
                return parsed, "filename", m.group(0)
        except (ValueError, IndexError):
            continue
    return None


# ─────────────────────────────────────────────────────────────────────
# LLM fallback — concise prompt, JSON envelope
# ─────────────────────────────────────────────────────────────────────

_LLM_SYSTEM = (
    "You read the first few pages of a procurement amendment / RFP "
    "document and extract its *issuance date* — the date the agency "
    "issued the document. You output VALID JSON ONLY. No preamble. "
    "No code fences."
)


def _llm_extract_date(filename: str, body: str) -> Optional[Tuple[date, str, str]]:
    if not body.strip():
        return None
    prompt = (
        f"Filename: {filename}\n\n"
        f"DOCUMENT TEXT (first ~{len(body)} chars):\n"
        f"{body[:MAX_LLM_BODY_CHARS]}\n\n"
        f"Find the issuance / effective date of this document. Look for "
        f"phrases like 'Date Issued', 'Effective Date', 'Amendment dated', "
        f"or any obvious dated header. Output JSON exactly:\n"
        f"{{\"date\": \"YYYY-MM-DD\" or null, \"rationale\": \"<short>\"}}"
    )
    try:
        raw = _call_ai(prompt, _LLM_SYSTEM, max_tokens=200)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"LLM date extraction failed: {e}")
        return None
    if not raw:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.+?\})\s*```", raw, re.DOTALL)
    cand = (fenced.group(1) if fenced else raw).strip()
    if not cand.startswith("{"):
        m = re.search(r"\{.+\}", cand, re.DOTALL)
        if m:
            cand = m.group(0)
    try:
        env = json.loads(cand)
    except json.JSONDecodeError:
        return None
    d = (env.get("date") or "").strip()
    if not d:
        return None
    try:
        parsed = datetime.strptime(d, "%Y-%m-%d").date()
        if 2000 <= parsed.year <= datetime.utcnow().year + 1:
            return parsed, "llm", (env.get("rationale") or "")[:200]
    except ValueError:
        return None
    return None


# ─────────────────────────────────────────────────────────────────────
# Public entry — extract date for one document
# ─────────────────────────────────────────────────────────────────────

def extract_document_date(
    db: Session,
    document_id: int,
    *,
    use_llm_fallback: bool = True,
) -> Dict[str, Any]:
    """Extract the issuance/effective date for one document. Resolution
    order is tuned to avoid the trap where a doc cites a statute or older
    revision date in early text and the regex grabs that instead of the
    actual issuance date:

      1. **Cue-only body match** — only matches preceded by a cue word
         ("Date Issued", "Effective Date", "Amendment dated").
      2. **Filename date** — many procurement docs encode date in name
         (e.g. ``...03.01.2021.docx``).
      3. **Bare body match** — last-resort plain-date regex on body.
      4. **LLM fallback** — read the body and ask.
    """
    doc = (db.query(IngestedDocument)
           .filter(IngestedDocument.id == document_id).first())
    if not doc:
        return {"document_id": document_id, "error": "Document not found"}

    name = doc.original_filename or doc.filename or f"doc {doc.id}"
    chunks = (db.query(DocumentChunk)
              .filter(DocumentChunk.document_id == document_id)
              .order_by(DocumentChunk.chunk_index)
              .limit(MAX_LEADING_CHUNKS)
              .all())
    body = "\n\n".join((c.content or "") for c in chunks)

    # 1) cue-word body match (highest precision)
    cue_hit = _scan_text_for_date(body[:6000], cue_only=True)
    if cue_hit:
        d, kind, raw = cue_hit
        return {
            "document_id": document_id, "filename": name,
            "date": d.isoformat(), "source": f"body_regex:{kind}",
            "rationale": f"Matched in document body with cue: {raw}",
            "raw_match": raw, "confidence": "high",
        }
    # 2) filename
    fname_hit = _scan_filename_for_date(name)
    if fname_hit:
        d, _, raw = fname_hit
        return {
            "document_id": document_id, "filename": name,
            "date": d.isoformat(), "source": "filename",
            "rationale": f"Matched in filename: {raw}",
            "raw_match": raw, "confidence": "medium",
        }
    # 3) bare body match (lower precision; statute citations risk)
    bare_hit = _scan_text_for_date(body[:6000], cue_only=False)
    if bare_hit:
        d, kind, raw = bare_hit
        return {
            "document_id": document_id, "filename": name,
            "date": d.isoformat(), "source": f"body_regex:{kind}",
            "rationale": f"Matched in document body (bare, no cue word): {raw}",
            "raw_match": raw, "confidence": "low",
        }
    # 4) LLM
    if use_llm_fallback and body.strip():
        llm_hit = _llm_extract_date(name, body)
        if llm_hit:
            d, _, rationale = llm_hit
            return {
                "document_id": document_id, "filename": name,
                "date": d.isoformat(), "source": "llm",
                "rationale": rationale, "raw_match": "",
                "confidence": "low",
            }
    return {
        "document_id": document_id, "filename": name,
        "date": None, "source": "none",
        "rationale": "No date found in body, filename, or LLM scan.",
        "raw_match": "", "confidence": "none",
    }


def suggest_amendment_order(
    db: Session,
    document_ids: List[int],
    *,
    use_llm_fallback: bool = True,
) -> Dict[str, Any]:
    """Extract a date for every document and return them sorted
    chronologically (oldest → newest). Documents with no detected date
    are appended at the end with their original index preserved.
    """
    extracted: List[Dict[str, Any]] = []
    for original_index, did in enumerate(document_ids):
        info = extract_document_date(db, did, use_llm_fallback=use_llm_fallback)
        info["original_index"] = original_index
        extracted.append(info)

    dated = [e for e in extracted if e.get("date")]
    undated = [e for e in extracted if not e.get("date")]
    dated.sort(key=lambda e: (e["date"], e["original_index"]))
    undated.sort(key=lambda e: e["original_index"])
    ordered = dated + undated

    return {
        "input_count": len(document_ids),
        "dated_count": len(dated),
        "undated_count": len(undated),
        "suggested_order": [e["document_id"] for e in ordered],
        "details": ordered,
    }
