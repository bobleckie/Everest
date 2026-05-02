"""Stage 2 — Build per-document section hierarchy.

Outputs:
  section_hierarchy(
    id INTEGER PRIMARY KEY,
    document_id INTEGER,
    section_code TEXT,        -- "3.2.1", "Appendix C", "Attachment 2"
    parent_section_code TEXT, -- "3.2" for "3.2.1"; NULL for top
    title TEXT,               -- the heading text
    depth INTEGER,            -- 0 for top, 1 for "3", 2 for "3.2", ...
    ord INTEGER,              -- in-doc ordinal (chunk_index of first chunk)
    first_chunk_id INTEGER,
    last_chunk_id INTEGER,
    char_count INTEGER,
    UNIQUE (document_id, section_code)
  )

Strategy
--------
1. Walk a document's chunks in chunk_index order.
2. Detect heading anchors at the start of any chunk's content. We use
   conservative patterns that match the way the chunker preserved them
   (most preserve a heading line at the top of a chunk that starts a
   section).
3. Maintain a stack of (depth, section_code) so children inherit the
   nearest open ancestor with shallower depth.
4. After walking, post-process to compute parent_section_code from the
   dotted-notation parent of each numeric code, falling back to the
   stack-based parent for non-numeric codes.

Also populates ``document_chunks.section_id`` for chunks that didn't
have one. Existing non-NULL section_id values are NOT overwritten.

Re-runnable: drops + recreates ``section_hierarchy``.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from typing import List, Optional, Tuple

from .common import banner, connect, stat

# Patterns ordered by specificity. Each captures the section code and (optionally)
# a title. Anchored to the start of a non-empty line.

PATTERNS = [
    # "Appendix 3.2H: Maintenance and Operations of Buildings and Grounds"
    # "Appendix C — Pricing"
    re.compile(
        r"^\s*(Appendix\s+[A-Z0-9][A-Za-z0-9.\-]*)\s*[:\-—–]\s*(.{2,160}?)\s*$",
        re.MULTILINE,
    ),
    # "Attachment 2 - Standard Procurement Forms Packet"
    re.compile(
        r"^\s*(Attachment\s+[A-Z0-9][A-Za-z0-9.\-]*)\s*[:\-—–]\s*(.{2,160}?)\s*$",
        re.MULTILINE,
    ),
    # "Exhibit B: Insurance Requirements"
    re.compile(
        r"^\s*(Exhibit\s+[A-Z0-9][A-Za-z0-9.\-]*)\s*[:\-—–]\s*(.{2,160}?)\s*$",
        re.MULTILINE,
    ),
    # "Schedule 1: Service Levels"
    re.compile(
        r"^\s*(Schedule\s+[A-Z0-9][A-Za-z0-9.\-]*)\s*[:\-—–]\s*(.{2,160}?)\s*$",
        re.MULTILINE,
    ),
    # "3.2.1 SOFTWARE REQUIREMENTS" or "3 SCOPE"
    re.compile(
        r"^\s*(\d+(?:\.\d+)*)\s+([A-Z][A-Z0-9 ,/&\-:'\(\)]{3,160})\s*$",
        re.MULTILINE,
    ),
    # "Section 4.2 — Hours of Operation"
    re.compile(
        r"^\s*Section\s+(\d+(?:\.\d+)*)\s*[:\-—–]?\s*(.{2,160}?)\s*$",
        re.MULTILINE,
    ),
]

# Bare-code header (no title on same line): "3.2.1" alone, or followed by content
PATTERN_BARE_CODE = re.compile(r"^\s*(\d+(?:\.\d+)*)\s*$", re.MULTILINE)


def parent_dotted(code: str) -> Optional[str]:
    """For "3.2.1" -> "3.2". Returns None for top-level numeric or non-numeric."""
    if not re.fullmatch(r"\d+(?:\.\d+)*", code):
        return None
    parts = code.split(".")
    if len(parts) <= 1:
        return None
    return ".".join(parts[:-1])


def depth_of(code: str) -> int:
    if re.fullmatch(r"\d+(?:\.\d+)*", code):
        return code.count(".") + 1
    # Appendix/Attachment/Exhibit/Schedule/Article/etc. count as depth 1.
    return 1


def detect_headings(content: str) -> List[Tuple[str, str, int]]:
    """Return [(code, title, char_offset_in_chunk), ...] in order."""
    found: List[Tuple[str, str, int]] = []
    seen_at: dict[int, str] = {}
    for pat in PATTERNS:
        for m in pat.finditer(content):
            code = m.group(1).strip()
            title = (m.group(2) or "").strip().rstrip(":-—–.").strip()
            offset = m.start()
            # Don't double-count the same offset
            if offset in seen_at:
                continue
            seen_at[offset] = code
            found.append((code, title, offset))
    found.sort(key=lambda t: t[2])
    return found


def main() -> int:
    con = connect()
    cur = con.cursor()

    banner("STAGE 2 — Section hierarchy")

    cur.executescript(
        """
        DROP TABLE IF EXISTS section_hierarchy;
        CREATE TABLE section_hierarchy (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id         INTEGER NOT NULL REFERENCES ingested_documents(id),
            section_code        TEXT NOT NULL,
            parent_section_code TEXT,
            title               TEXT,
            depth               INTEGER NOT NULL,
            ord                 INTEGER NOT NULL,
            first_chunk_id      INTEGER REFERENCES document_chunks(id),
            last_chunk_id       INTEGER REFERENCES document_chunks(id),
            char_count          INTEGER DEFAULT 0,
            UNIQUE (document_id, section_code)
        );
        CREATE INDEX idx_sh_doc ON section_hierarchy(document_id);
        CREATE INDEX idx_sh_code ON section_hierarchy(section_code);
        """
    )

    docs = cur.execute(
        """SELECT id, original_filename FROM ingested_documents
           WHERE EXISTS (SELECT 1 FROM document_chunks WHERE document_id=ingested_documents.id)
           ORDER BY id"""
    ).fetchall()
    stat("Documents to scan", len(docs))

    total_sections = 0
    chunks_assigned = 0
    section_records = []

    # Backfill prep: hydrate existing section_id values
    chunk_section = dict(
        cur.execute(
            "SELECT id, section_id FROM document_chunks WHERE section_id IS NOT NULL"
        )
    )

    for doc_id, fname in docs:
        chunks = cur.execute(
            """SELECT id, chunk_index, content, section_id
               FROM document_chunks WHERE document_id=? ORDER BY chunk_index""",
            (doc_id,),
        ).fetchall()

        # Walk chunks; track current section per detected heading.
        current: Optional[str] = None
        current_first_chunk: Optional[int] = None
        current_first_idx: Optional[int] = None
        current_chars = 0
        per_section: dict[str, dict] = {}
        ord_counter = 0

        for chunk_id, ci, content, existing in chunks:
            content = content or ""
            headings = detect_headings(content)

            # If chunk starts with a heading, transition.
            if headings and headings[0][2] < 200:  # heading near top
                code, title, _ = headings[0]
                if current and current_first_chunk:
                    # Close out the prior section
                    rec = per_section.setdefault(current, {})
                    rec["last_chunk_id"] = chunk_id - 1 if False else rec.get("last_chunk_id", chunk_id)
                    rec["last_chunk_id"] = max(rec.get("last_chunk_id", 0), prior_last_chunk)
                current = code
                current_first_chunk = chunk_id
                current_first_idx = ci
                if code not in per_section:
                    ord_counter += 1
                    per_section[code] = {
                        "title": title,
                        "depth": depth_of(code),
                        "ord": ord_counter,
                        "first_chunk_id": chunk_id,
                        "last_chunk_id": chunk_id,
                        "char_count": len(content),
                    }
                else:
                    # Reopened — update last_chunk
                    per_section[code]["last_chunk_id"] = chunk_id
                    per_section[code]["char_count"] += len(content)

                # Sub-headings within the same chunk
                for sub_code, sub_title, _ in headings[1:]:
                    if sub_code not in per_section:
                        ord_counter += 1
                        per_section[sub_code] = {
                            "title": sub_title,
                            "depth": depth_of(sub_code),
                            "ord": ord_counter,
                            "first_chunk_id": chunk_id,
                            "last_chunk_id": chunk_id,
                            "char_count": 0,
                        }
                        # Keep current pointer on outermost; sub-headings are
                        # registered but the chunk is attributed to the first.
            else:
                # No heading anchor — extends current section if any
                if current and current in per_section:
                    per_section[current]["last_chunk_id"] = chunk_id
                    per_section[current]["char_count"] += len(content)

            # Backfill section_id on chunks that don't have one
            if existing is None and current:
                chunk_section[chunk_id] = current
                cur.execute(
                    "UPDATE document_chunks SET section_id=? WHERE id=?",
                    (current, chunk_id),
                )
                chunks_assigned += 1

            prior_last_chunk = chunk_id  # used by close-out above

        # Persist sections for this doc
        for code, rec in per_section.items():
            section_records.append(
                (
                    doc_id,
                    code,
                    parent_dotted(code),
                    rec["title"][:300] if rec["title"] else None,
                    rec["depth"],
                    rec["ord"],
                    rec["first_chunk_id"],
                    rec["last_chunk_id"],
                    rec["char_count"],
                )
            )
        total_sections += len(per_section)

    cur.executemany(
        """INSERT OR REPLACE INTO section_hierarchy
           (document_id, section_code, parent_section_code, title,
            depth, ord, first_chunk_id, last_chunk_id, char_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        section_records,
    )
    con.commit()

    # Second pass: for non-numeric codes whose parent_dotted is NULL, try to
    # set a parent based on the section that opens immediately before in the
    # same document (stack semantics by ord).
    by_doc = defaultdict(list)
    for r in cur.execute(
        """SELECT id, document_id, section_code, parent_section_code, depth, ord
           FROM section_hierarchy ORDER BY document_id, ord"""
    ):
        by_doc[r[1]].append(r)

    fixups = 0
    for doc_id, rows in by_doc.items():
        stack: List[tuple] = []  # (depth, section_code)
        for sid, did, code, parent, depth, ord_ in rows:
            while stack and stack[-1][0] >= depth:
                stack.pop()
            stack_parent = stack[-1][1] if stack else None
            new_parent = parent or stack_parent
            if new_parent != parent:
                cur.execute(
                    "UPDATE section_hierarchy SET parent_section_code=? WHERE id=?",
                    (new_parent, sid),
                )
                fixups += 1
            stack.append((depth, code))
    con.commit()

    print()
    stat("Sections detected", total_sections)
    stat("Chunks newly assigned section_id", chunks_assigned)
    stat("Stack-based parent fixups", fixups)

    print()
    print("  Per-doc section counts (top 8):")
    for did, fname, n in cur.execute(
        """SELECT sh.document_id, ind.original_filename, COUNT(*) AS n
           FROM section_hierarchy sh
           JOIN ingested_documents ind ON ind.id = sh.document_id
           GROUP BY sh.document_id ORDER BY n DESC LIMIT 8"""
    ):
        print(f"    doc {did:3d}: {n:5d} sections — {fname}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
