"""Stage 3 — Structured table extraction.

Tables in document_chunks are stored as flat text with ``|``-separated cells,
one row per line. We parse those into structured rows so the UI can:
  - render the table in cell layout
  - highlight specific rows referenced by a requirement
  - support "rows 3–7 of Appendix 3.2H Table F-1" style scoping

Output schema:
  extracted_tables(
    id, document_id, chunk_id, section_code, ord, title, header_row_json,
    n_rows, n_cols, char_count
  )
  extracted_table_rows(
    id, table_id, row_index, cells_json, raw_line, fingerprint
  )

A "table" is detected as a contiguous run of >= 3 lines where each line
contains >= 3 ``|`` characters. Headers are inferred when the first line of
the run contains a high ratio of words / Title-Case fragments and few
numbers; otherwise we leave the header empty and the UI shows the first row
as data.

Re-runnable: drops + recreates both tables.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import defaultdict
from typing import List, Optional

from .common import banner, connect, stat


PIPE = "|"
MIN_PIPES = 3
MIN_RUN = 3


def split_row(line: str) -> List[str]:
    # Handle leading/trailing pipes from doc_parser's table flattening.
    parts = [p.strip() for p in line.split(PIPE)]
    # Strip empty leading/trailing cells from "| a | b |" -> ['', 'a', 'b', '']
    while parts and parts[0] == "":
        parts.pop(0)
    while parts and parts[-1] == "":
        parts.pop()
    return parts


def is_table_line(line: str) -> bool:
    return line.count(PIPE) >= MIN_PIPES


def looks_like_header(cells: List[str]) -> bool:
    if not cells:
        return False
    # Heuristic: most cells contain alpha and few are pure numbers/dates.
    alpha = sum(1 for c in cells if re.search(r"[A-Za-z]", c))
    digits = sum(1 for c in cells if re.fullmatch(r"\d[\d.,/\- ]*", c.strip()) and c.strip())
    return alpha >= 0.6 * len(cells) and digits <= 0.2 * len(cells)


def fingerprint_row(cells: List[str]) -> str:
    return hashlib.sha1("|".join(c.strip().lower() for c in cells).encode("utf-8")).hexdigest()[:16]


def main() -> int:
    con = connect()
    cur = con.cursor()

    banner("STAGE 3 — Structured table extraction")

    cur.executescript(
        """
        DROP TABLE IF EXISTS extracted_table_rows;
        DROP TABLE IF EXISTS extracted_tables;

        CREATE TABLE extracted_tables (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id     INTEGER NOT NULL REFERENCES ingested_documents(id),
            chunk_id        INTEGER NOT NULL REFERENCES document_chunks(id),
            section_code    TEXT,
            ord             INTEGER NOT NULL,         -- index of the table within its chunk
            title           TEXT,
            header_row_json TEXT,                     -- JSON array or NULL
            n_rows          INTEGER NOT NULL,
            n_cols          INTEGER NOT NULL,
            char_count      INTEGER NOT NULL
        );
        CREATE INDEX idx_etab_doc ON extracted_tables(document_id);
        CREATE INDEX idx_etab_chunk ON extracted_tables(chunk_id);
        CREATE INDEX idx_etab_section ON extracted_tables(section_code);

        CREATE TABLE extracted_table_rows (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            table_id    INTEGER NOT NULL REFERENCES extracted_tables(id) ON DELETE CASCADE,
            row_index   INTEGER NOT NULL,
            cells_json  TEXT NOT NULL,
            raw_line    TEXT,
            fingerprint TEXT
        );
        CREATE INDEX idx_etrow_table ON extracted_table_rows(table_id);
        CREATE INDEX idx_etrow_fp ON extracted_table_rows(fingerprint);
        """
    )

    chunks = cur.execute(
        "SELECT id, document_id, section_id, content FROM document_chunks WHERE content IS NOT NULL"
    ).fetchall()
    stat("Chunks scanned", len(chunks))

    tables_inserted = 0
    rows_inserted = 0

    for cid, did, section, content in chunks:
        lines = content.splitlines()
        i = 0
        ord_in_chunk = 0
        while i < len(lines):
            if not is_table_line(lines[i]):
                i += 1
                continue
            # Found a candidate; consume the run
            run_start = i
            while i < len(lines) and is_table_line(lines[i]):
                i += 1
            run = lines[run_start:i]
            if len(run) < MIN_RUN:
                continue

            parsed = [split_row(l) for l in run]
            # Drop entirely-empty rows
            parsed = [p for p in parsed if any(c for c in p)]
            if len(parsed) < MIN_RUN:
                continue

            # Normalize column count to the most common width.
            widths = [len(p) for p in parsed]
            try:
                n_cols = max(set(widths), key=widths.count)
            except ValueError:
                continue
            normalized = []
            for p in parsed:
                if len(p) < n_cols:
                    p = p + [""] * (n_cols - len(p))
                else:
                    p = p[:n_cols]
                normalized.append(p)

            # Pick a title heuristically: scan back up to 3 non-blank lines
            # before the run for a phrase like "Table X" or a Capitalized phrase.
            title = None
            scan = max(0, run_start - 3)
            for j in range(run_start - 1, scan - 1, -1):
                ln = (lines[j] or "").strip()
                if not ln:
                    continue
                if re.search(r"\bTable\s+[A-Z0-9][A-Z0-9.\-]*", ln, re.I) or re.match(r"[A-Z][A-Za-z0-9 ,/&\-:]{4,80}$", ln):
                    title = ln[:200]
                    break
                break

            header_json: Optional[str] = None
            data_rows = normalized
            if looks_like_header(normalized[0]):
                header_json = json.dumps(normalized[0])
                data_rows = normalized[1:]

            char_count = sum(sum(len(c) for c in row) for row in normalized)

            cur.execute(
                """INSERT INTO extracted_tables
                   (document_id, chunk_id, section_code, ord, title,
                    header_row_json, n_rows, n_cols, char_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (did, cid, section, ord_in_chunk, title, header_json,
                 len(data_rows), n_cols, char_count),
            )
            tid = cur.lastrowid
            tables_inserted += 1
            ord_in_chunk += 1

            for ridx, row in enumerate(data_rows):
                cur.execute(
                    """INSERT INTO extracted_table_rows
                       (table_id, row_index, cells_json, raw_line, fingerprint)
                       VALUES (?, ?, ?, ?, ?)""",
                    (tid, ridx, json.dumps(row), run[ridx + (1 if header_json else 0)] if (ridx + (1 if header_json else 0)) < len(run) else None, fingerprint_row(row)),
                )
                rows_inserted += 1

    con.commit()

    print()
    stat("Tables persisted", tables_inserted)
    stat("Table rows persisted", rows_inserted)

    print()
    print("  Tables per doc (top 8):")
    for did, n, fname in cur.execute(
        """SELECT et.document_id, COUNT(*) n, ind.original_filename
           FROM extracted_tables et JOIN ingested_documents ind ON ind.id = et.document_id
           GROUP BY et.document_id ORDER BY n DESC LIMIT 8"""
    ):
        print(f"    doc {did:3d}: {n:4d} tables — {fname}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
