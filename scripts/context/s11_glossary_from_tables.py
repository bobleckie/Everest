"""Stage 11 — Glossary expansion from definition tables.

Many RFPs (especially Standard T&Cs) layout definitions as a two-column
table: ``[Term, Definition]`` or ``[Defined Term, Meaning]``. The s04
regex-based extractor misses these because the cells are pipe-separated
across multiple lines.

This stage scans ``extracted_tables`` for table shapes that look like a
glossary and inserts the rows into ``glossary_terms``.

Heuristics:
  * Header has exactly 2 columns (or first 2 cols match definition shape).
  * Header text in either column matches /term|definition|meaning/i.
  * Each data row's first cell is short (≤ 60 chars) and the second is
    longer (≥ 30 chars).

Re-runnable: removes prior 'definition-table' scope rows before inserting.
"""
from __future__ import annotations

import json
import re
import sys
from typing import List, Optional

from .common import banner, connect, norm_alnum, norm_ws, stat


HEADER_TERMS = ("term", "defined term", "definition", "meaning", "abbreviation", "acronym")


def header_has_glossary_signal(header: List[str]) -> bool:
    if not header:
        return False
    norm = [(c or "").strip().lower() for c in header]
    return any(any(t in n for t in HEADER_TERMS) for n in norm[:3])


def main() -> int:
    con = connect()
    cur = con.cursor()
    banner("STAGE 11 — Glossary from definition tables")

    # Remove prior runs of this stage
    cur.execute("DELETE FROM glossary_terms WHERE scope='definition-table'")
    con.commit()

    tables = cur.execute(
        """SELECT et.id, et.document_id, et.section_code, et.title,
                  et.header_row_json, et.n_rows, et.n_cols
           FROM extracted_tables et"""
    ).fetchall()
    stat("Tables to scan", len(tables))

    candidate_count = 0
    inserted = 0
    seen_norm = set()
    # Pre-load existing terms to avoid duplicates
    for tn, in cur.execute("SELECT term_normalized FROM glossary_terms"):
        seen_norm.add(tn)

    next_ord = (cur.execute("SELECT COALESCE(MAX(ord),0) FROM glossary_terms").fetchone()[0] or 0)

    for tid, did, sec, title, header_json, n_rows, n_cols in tables:
        if not header_json:
            continue
        try:
            header = json.loads(header_json)
        except (json.JSONDecodeError, TypeError):
            continue
        if not header_has_glossary_signal(header):
            continue
        candidate_count += 1
        rows = cur.execute(
            "SELECT row_index, cells_json FROM extracted_table_rows WHERE table_id=? ORDER BY row_index",
            (tid,),
        ).fetchall()
        for ridx, cells_json in rows:
            try:
                cells = json.loads(cells_json)
            except (json.JSONDecodeError, TypeError):
                continue
            if len(cells) < 2:
                continue
            term = (cells[0] or "").strip()
            defn = (cells[1] or "").strip()
            # Some glossaries have 3 cols (Term, Definition, Reference)
            if len(cells) >= 3 and len(defn) < 10 and len(cells[2] or "") > len(defn):
                defn = cells[2].strip()
            if len(term) < 3 or len(term) > 100:
                continue
            if len(defn) < 15:
                continue
            tn = norm_alnum(term)
            if not tn or tn in seen_norm:
                continue
            seen_norm.add(tn)
            next_ord += 1
            cur.execute(
                """INSERT INTO glossary_terms
                   (document_id, chunk_id, section_code, term, term_normalized,
                    definition, scope, ord)
                   VALUES (?, NULL, ?, ?, ?, ?, 'definition-table', ?)""",
                (did, sec, term, tn, defn[:2000], next_ord),
            )
            inserted += 1
    con.commit()

    print()
    stat("Glossary-shaped tables found", candidate_count)
    stat("Terms added from tables", inserted)

    print()
    print("  Sample new terms:")
    for r in cur.execute(
        """SELECT term, substr(definition,1,140) FROM glossary_terms
           WHERE scope='definition-table' ORDER BY RANDOM() LIMIT 6"""
    ):
        print(f"    {r[0]!r}: {r[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
