"""
Document parsing service — extracts text from PDF, DOCX, TXT, XLSX files
and splits into overlapping chunks for downstream analysis.
"""
import os
import io
import json
import logging
import re
from typing import List, Tuple, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# ── Chunk configuration ──────────────────────────────────────────────
CHUNK_SIZE = 1500       # characters per chunk
CHUNK_OVERLAP = 200     # overlap between consecutive chunks


# ── Section-header detection ─────────────────────────────────────────
# Matches RFP-style section headers like:
#   "4.15 HOURS OF OPERATION"
#   "4.15.3 Some Subsection"
#   "Section 4.15 Hours of Operation"
#   "8.9.1 TECHNICAL EVALUATION CRITERIA"
#   "Appendix 3.2H"
#   "Article XII"
#   "Exhibit A"
# The header must be at the START of a line (or near it) AND followed by a
# title in caps or title-case.
#
# We deliberately err on the side of CONSERVATISM — bad detection is worse
# than no detection because it splits chunks pointlessly and confuses the
# search.
_SECTION_HEADER_PATTERNS = [
    # Numeric: "4.15 TITLE" or "4.15.1 Title" — heading line
    re.compile(
        r"^\s*"
        r"(?P<sid>\d+(?:\.\d+){0,5}[A-Z]?)"
        r"\s+"
        r"(?P<title>[A-Z][A-Z\s\-/&,'()]{3,80})"
        r"\s*$",
        re.MULTILINE,
    ),
    # Numeric with title-case ("4.15 Hours of Operation")
    re.compile(
        r"^\s*"
        r"(?P<sid>\d+(?:\.\d+){0,5}[A-Z]?)"
        r"\s+"
        r"(?P<title>[A-Z][A-Za-z][A-Za-z\s\-/&,'()]{3,80})"
        r"\s*$",
        re.MULTILINE,
    ),
    # "SECTION 4.15" prefix (case-insensitive)
    re.compile(
        r"^\s*"
        r"(?:SECTION|Section)\s+"
        r"(?P<sid>\d+(?:\.\d+){0,5}[A-Z]?)"
        r"(?:\s+(?P<title>[A-Z][A-Za-z\s\-/&,'()]{3,80}))?"
        r"\s*$",
        re.MULTILINE,
    ),
    # "Appendix 3.2H" / "Appendix A"
    re.compile(
        r"^\s*"
        r"(?P<sid>(?:Appendix|APPENDIX|Exhibit|EXHIBIT|Attachment|ATTACHMENT)\s+"
        r"[A-Za-z0-9](?:\.[A-Za-z0-9]+)*)"
        r"(?:\s+(?P<title>[A-Z][A-Za-z\s\-/&,'()]{3,80}))?"
        r"\s*$",
        re.MULTILINE,
    ),
]
# Lines that look like section headers but are clearly TOC dot-leader rows
# (e.g. "4.15 Hours of Operation .................. 35"). Skip those.
_TOC_LINE = re.compile(r"\.{6,}\s*\d+\s*$")


def _find_section_boundaries(text: str) -> List[Tuple[int, str, str]]:
    """Scan a block of text and return a list of (offset, section_id, title)
    tuples for every section header detected, sorted by offset.

    Conservative: only returns matches that are NOT TOC dot-leader rows
    AND are at line-start. Returns empty list when text has no detectable
    sections (e.g. price-sheet xlsx, PowerPoint slides) — caller falls back
    to the legacy chunker."""
    if not text or len(text) < 200:
        return []

    boundaries: dict[int, Tuple[str, str]] = {}
    for pat in _SECTION_HEADER_PATTERNS:
        for m in pat.finditer(text):
            line = m.group(0)
            if _TOC_LINE.search(line):
                continue
            # Reject if the matched line is too long (paragraph picked up
            # by a greedy pattern) or has too much non-header-like content
            if len(line.strip()) > 120:
                continue
            sid = (m.groupdict().get("sid") or "").strip()
            title = (m.groupdict().get("title") or "").strip()
            if not sid:
                continue
            # Heuristic: bare 1-digit section ids ("4 SCOPE OF WORK") are
            # noisier than 2+ level ids. Keep, but they're lower confidence.
            boundaries[m.start()] = (sid, title)
    return sorted([(off, sid, title) for off, (sid, title) in boundaries.items()])


def extract_text_pdf(file_bytes: bytes) -> List[Tuple[int, str]]:
    """Extract text from PDF. Returns list of (page_number, text)."""
    import pdfplumber
    pages = []
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                # Also try to extract tables as text
                tables = page.extract_tables()
                if tables:
                    for table in tables:
                        for row in table:
                            if row:
                                text += "\n" + " | ".join(str(cell or "") for cell in row)
                if text.strip():
                    pages.append((i + 1, text.strip()))
    except Exception as e:
        logger.error(f"PDF extraction failed: {e}")
        raise ValueError(f"Failed to parse PDF: {e}")
    return pages


def _read_docx_page_count(file_bytes: bytes) -> Optional[int]:
    """
    Read the cached page count from a DOCX's extended properties (docProps/app.xml).
    Word writes this when the document is saved. Returns None if not present.
    """
    import zipfile
    import re
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
            if "docProps/app.xml" not in z.namelist():
                return None
            xml = z.read("docProps/app.xml").decode("utf-8", errors="ignore")
        m = re.search(r"<Pages>(\d+)</Pages>", xml)
        if m:
            n = int(m.group(1))
            return n if n > 0 else None
    except Exception as e:
        logger.debug(f"Could not read DOCX page count: {e}")
    return None


def extract_text_docx(file_bytes: bytes) -> List[Tuple[int, str]]:
    """
    Extract text from DOCX. DOCX has no per-page XML (page breaks are rendered by
    Word), so we return a single (page_number=1, text) tuple. The *document's*
    cached page count is surfaced separately via parse_and_chunk using
    _read_docx_page_count().
    """
    from docx import Document
    try:
        doc = Document(io.BytesIO(file_bytes))
        full_text = []
        for para in doc.paragraphs:
            if para.text.strip():
                full_text.append(para.text.strip())
        # Also extract tables
        for table in doc.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                if any(cells):
                    full_text.append(" | ".join(cells))
        if full_text:
            return [(1, "\n".join(full_text))]
        return []
    except Exception as e:
        logger.error(f"DOCX extraction failed: {e}")
        raise ValueError(f"Failed to parse DOCX: {e}")


def extract_text_txt(file_bytes: bytes) -> List[Tuple[int, str]]:
    """Extract text from plain text file."""
    try:
        text = file_bytes.decode("utf-8", errors="replace")
        if text.strip():
            return [(1, text.strip())]
        return []
    except Exception as e:
        logger.error(f"TXT extraction failed: {e}")
        raise ValueError(f"Failed to parse text file: {e}")


def extract_text_xlsx(file_bytes: bytes) -> List[Tuple[int, str]]:
    """Extract text from XLSX — each sheet becomes a 'page'."""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
        pages = []
        for i, sheet_name in enumerate(wb.sheetnames):
            ws = wb[sheet_name]
            rows = []
            for row in ws.iter_rows(values_only=True):
                cells = [str(c) if c is not None else "" for c in row]
                if any(cells):
                    rows.append(" | ".join(cells))
            if rows:
                pages.append((i + 1, f"[Sheet: {sheet_name}]\n" + "\n".join(rows)))
        return pages
    except ImportError:
        logger.warning("openpyxl not installed — XLSX parsing unavailable")
        raise ValueError("XLSX parsing requires openpyxl. Install with: pip install openpyxl")
    except Exception as e:
        logger.error(f"XLSX extraction failed: {e}")
        raise ValueError(f"Failed to parse XLSX: {e}")


def extract_text_pptx(file_bytes: bytes) -> List[Tuple[int, str]]:
    """Extract text from PPTX — each slide becomes a 'page'.

    Pulls text from shape text frames, table cells, and slide notes so
    speaker notes (often where dates and procedural details live in
    pre-bid conference decks) are included.
    """
    try:
        from pptx import Presentation
        prs = Presentation(io.BytesIO(file_bytes))
        pages: List[Tuple[int, str]] = []
        for i, slide in enumerate(prs.slides):
            parts: List[str] = []
            for shape in slide.shapes:
                # Plain text shapes
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        text = "".join(run.text for run in para.runs).strip()
                        if text:
                            parts.append(text)
                # Tables
                if shape.has_table:
                    for row in shape.table.rows:
                        cells = [cell.text.strip() for cell in row.cells]
                        if any(cells):
                            parts.append(" | ".join(cells))
            # Speaker notes
            try:
                if slide.has_notes_slide:
                    notes = slide.notes_slide.notes_text_frame.text.strip()
                    if notes:
                        parts.append(f"[Notes]\n{notes}")
            except Exception:
                pass
            if parts:
                pages.append((i + 1, "\n".join(parts)))
        return pages
    except ImportError:
        logger.warning("python-pptx not installed — PPTX parsing unavailable")
        raise ValueError("PPTX parsing requires python-pptx. Install with: pip install python-pptx")
    except Exception as e:
        logger.error(f"PPTX extraction failed: {e}")
        raise ValueError(f"Failed to parse PPTX: {e}")


# ── Main extraction dispatcher ───────────────────────────────────────

EXTRACTORS = {
    "pdf": extract_text_pdf,
    "docx": extract_text_docx,
    "doc": extract_text_docx,  # best-effort
    "txt": extract_text_txt,
    "text": extract_text_txt,
    "md": extract_text_txt,
    "csv": extract_text_txt,
    "xlsx": extract_text_xlsx,
    "xls": extract_text_xlsx,
    "pptx": extract_text_pptx,
    "ppt": extract_text_pptx,  # best-effort; legacy .ppt won't actually open
}


def detect_file_type(filename: str) -> str:
    """Return normalized file extension."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext


def extract_text(file_bytes: bytes, filename: str) -> List[Tuple[int, str]]:
    """
    Extract text from a file. Returns list of (page_number, text) tuples.
    Raises ValueError if file type unsupported or parsing fails.
    """
    ext = detect_file_type(filename)
    extractor = EXTRACTORS.get(ext)
    if not extractor:
        raise ValueError(f"Unsupported file type: .{ext}")
    return extractor(file_bytes)


# ── Chunking ─────────────────────────────────────────────────────────

def _split_with_size_cap(
    text: str,
    chunk_size: int,
    overlap: int,
) -> List[Tuple[int, str]]:
    """Sliding-window split for long blocks. Returns list of
    (start_offset_in_text, chunk_content_stripped). Same boundary-friendly
    logic as the legacy chunker."""
    out: List[Tuple[int, str]] = []
    if not text:
        return out
    if len(text) <= chunk_size:
        stripped = text.strip()
        if stripped:
            out.append((0, stripped))
        return out

    start = 0
    while start < len(text):
        end = start + chunk_size
        slice_ = text[start:end]
        if end < len(text):
            last_break = max(
                slice_.rfind(". "),
                slice_.rfind(".\n"),
                slice_.rfind("\n\n"),
                slice_.rfind("\n"),
            )
            if last_break > chunk_size // 2:
                slice_ = slice_[:last_break + 1]
        stripped = slice_.strip()
        if stripped:
            out.append((start, stripped))
        advance = len(slice_) - overlap
        if advance <= 0:
            break
        start += advance
        if start >= len(text):
            break
    return out


def chunk_text(
    pages: List[Tuple[int, str]],
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
    section_aware: bool = True,
) -> List[dict]:
    """
    Split extracted pages into overlapping chunks.
    Returns list of {chunk_index, page_number, content, char_count,
                       metadata_json, section_id (optional)}.

    When ``section_aware=True`` (default), the chunker scans each page for
    section headers (e.g. "4.15 HOURS OF OPERATION") and uses them as
    PRIMARY split boundaries. Each resulting chunk inherits the most-recent
    section_id seen, making downstream search section-aware. If no
    headers are detected on a page (e.g. price-sheet rows, slide deck),
    we fall back to the legacy size-only sliding-window split.
    """
    chunks: List[dict] = []
    chunk_idx = 0
    # Carry the most-recent section_id ACROSS pages — section bodies often
    # span page breaks and we don't want a page boundary to lose the anchor.
    current_section_id: Optional[str] = None
    current_section_title: Optional[str] = None

    for page_num, text in pages:
        if not text:
            continue

        if section_aware:
            boundaries = _find_section_boundaries(text)
        else:
            boundaries = []

        if not boundaries:
            # Legacy path: sliding-window split for the whole page.
            for start, content in _split_with_size_cap(text, chunk_size, overlap):
                chunks.append({
                    "chunk_index": chunk_idx,
                    "page_number": page_num,
                    "content": content,
                    "char_count": len(content),
                    "section_id": current_section_id,
                    "metadata_json": json.dumps({
                        "source_page": page_num,
                        "start_char": start,
                        "section_title": current_section_title,
                    }),
                })
                chunk_idx += 1
            continue

        # Section-aware path: split text at section boundaries first, then
        # apply size cap within each section block.
        # Build sections: [(start_offset, end_offset, section_id, title), ...]
        sections: List[Tuple[int, int, Optional[str], Optional[str]]] = []
        # Pre-section preamble (text before the first detected header)
        first = boundaries[0][0]
        if first > 0:
            sections.append((0, first, current_section_id, current_section_title))
        for i, (off, sid, title) in enumerate(boundaries):
            end = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(text)
            sections.append((off, end, sid, title))

        for sect_start, sect_end, sect_id, sect_title in sections:
            block = text[sect_start:sect_end]
            if not block.strip():
                continue
            # Keep the carry-over current_section if this block has no sid
            # (preamble before any header on this page).
            block_sid = sect_id if sect_id else current_section_id
            block_title = sect_title if sect_title else current_section_title
            for start, content in _split_with_size_cap(block, chunk_size, overlap):
                chunks.append({
                    "chunk_index": chunk_idx,
                    "page_number": page_num,
                    "content": content,
                    "char_count": len(content),
                    "section_id": block_sid,
                    "metadata_json": json.dumps({
                        "source_page": page_num,
                        "start_char": sect_start + start,
                        "section_title": block_title,
                    }),
                })
                chunk_idx += 1
            # Update the carry-over for subsequent blocks/pages
            if sect_id:
                current_section_id = sect_id
                current_section_title = sect_title

    return chunks


def parse_and_chunk(file_bytes: bytes, filename: str) -> dict:
    """
    Full pipeline: extract text → chunk → return summary + chunks.
    Returns {total_pages, total_chunks, file_type, chunks: [...]}.

    For DOCX we cannot derive real page boundaries (Word renders them on the fly),
    so the extractor returns a single text unit. We fall back to the cached
    <Pages> value from docProps/app.xml when Word has recorded one.
    """
    pages = extract_text(file_bytes, filename)
    file_type = detect_file_type(filename)
    chunks = chunk_text(pages)

    total_pages = len(pages)
    if file_type in ("docx", "doc"):
        cached = _read_docx_page_count(file_bytes)
        if cached and cached > total_pages:
            total_pages = cached

    return {
        "total_pages": total_pages,
        "total_chunks": len(chunks),
        "file_type": file_type,
        "chunks": chunks,
    }
