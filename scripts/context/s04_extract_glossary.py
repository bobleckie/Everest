"""Stage 4 — Glossary / defined-terms extraction.

Output:
  glossary_terms(
    id, document_id, chunk_id, section_code, term, term_normalized,
    definition, scope, ord
  )

Strategy
--------
Two passes, both deterministic.

Pass 1 — pattern matching anywhere in chunk content:
  '<TERM> shall mean <DEFINITION>.'
  '<TERM> means <DEFINITION>.'
  '<Term> is defined as <DEFINITION>.'
  '"<TERM>" means <DEFINITION>.'   (quoted form, very common in T&Cs)
  '<TERM> ("<ALIAS>") means ...'   (acronym alias)

Pass 2 — sections whose title matches /defin(ition|ed terms)/i. Walk every
chunk in those sections and parse list items / paragraphs as
"<HEAD>: <BODY>" pairs.

A single normalized form (lowercased, alphanumerized) is used to dedupe
across docs and to build requirement ↔ glossary linkage in stage 6.

Re-runnable: drops + recreates table.
"""
from __future__ import annotations

import re
import sys
from typing import List, Optional, Tuple

from .common import banner, connect, norm_alnum, norm_ws, stat


# Patterns. Order matters: more specific first.
PAT_QUOTED_MEANS = re.compile(
    r'["“]([A-Z][A-Za-z0-9 ,/&\-]{1,60})["”]\s+(?:shall\s+)?means?\s+([^."”;]{8,400})[.;]',
)
PAT_PAREN_ALIAS = re.compile(
    r'([A-Z][A-Za-z0-9 ]{2,60})\s+\(["“]?([A-Z][A-Za-z0-9 ]{1,30})["”]?\)\s+(?:shall\s+)?means?\s+([^."”;]{8,400})[.;]',
)
PAT_PLAIN_MEANS = re.compile(
    r'\b([A-Z][A-Za-z0-9 ,/&\-]{2,60})\s+(?:shall\s+mean|means)\s+([^.;]{8,400})[.;]',
)
PAT_IS_DEFINED_AS = re.compile(
    r'\b([A-Z][A-Za-z0-9 ,/&\-]{2,60})\s+(?:is|are)\s+defined\s+as\s+([^.;]{8,400})[.;]',
    re.I,
)


def collect_pattern_hits(text: str) -> List[Tuple[str, str, str]]:
    """Yield (term, definition, source) tuples found in ``text``."""
    out: List[Tuple[str, str, str]] = []
    for m in PAT_PAREN_ALIAS.finditer(text):
        head, alias, definition = m.group(1).strip(), m.group(2).strip(), norm_ws(m.group(3))
        out.append((head, definition, "alias"))
        out.append((alias, definition, "alias"))
    for m in PAT_QUOTED_MEANS.finditer(text):
        out.append((m.group(1).strip(), norm_ws(m.group(2)), "quoted"))
    for m in PAT_PLAIN_MEANS.finditer(text):
        out.append((m.group(1).strip(), norm_ws(m.group(2)), "plain"))
    for m in PAT_IS_DEFINED_AS.finditer(text):
        out.append((m.group(1).strip(), norm_ws(m.group(2)), "is-defined"))
    return out


def is_definition_section(title: Optional[str]) -> bool:
    if not title:
        return False
    return bool(re.search(r"\b(definition|defined\s+terms?|glossary)\b", title, re.I))


def parse_definition_list(text: str) -> List[Tuple[str, str]]:
    """Parse 'TERM: definition.' / 'TERM — definition.' style lines."""
    out: List[Tuple[str, str]] = []
    for m in re.finditer(
        r"^(?:\s*[\-•▪]\s*)?([A-Z][A-Za-z0-9 ,/&\-\(\)]{1,80})\s*[:\-—–]\s*([^\n]{15,500})\.?\s*$",
        text,
        re.MULTILINE,
    ):
        head = m.group(1).strip()
        body = norm_ws(m.group(2))
        # Filter junk: pure numeric heads, very long heads, all-caps short words
        if len(head) < 3:
            continue
        if re.fullmatch(r"\d[\d.]*", head):
            continue
        out.append((head, body))
    return out


def main() -> int:
    con = connect()
    cur = con.cursor()
    banner("STAGE 4 — Glossary / defined-terms")

    cur.executescript(
        """
        DROP TABLE IF EXISTS glossary_terms;
        CREATE TABLE glossary_terms (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id     INTEGER NOT NULL REFERENCES ingested_documents(id),
            chunk_id        INTEGER REFERENCES document_chunks(id),
            section_code    TEXT,
            term            TEXT NOT NULL,
            term_normalized TEXT NOT NULL,
            definition      TEXT NOT NULL,
            scope           TEXT,    -- 'pattern' | 'definitions-section' | 'alias'
            ord             INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX idx_gt_doc  ON glossary_terms(document_id);
        CREATE INDEX idx_gt_norm ON glossary_terms(term_normalized);
        CREATE INDEX idx_gt_chunk ON glossary_terms(chunk_id);
        """
    )

    # Pass 1 — pattern matching everywhere.
    chunks = cur.execute(
        "SELECT id, document_id, section_id, content FROM document_chunks WHERE content IS NOT NULL"
    ).fetchall()
    stat("Chunks scanned for definition patterns", len(chunks))

    inserted = 0
    seen: set = set()  # (doc_id, term_norm) — first hit wins per doc
    ord_counter = 0

    for cid, did, section, content in chunks:
        for term, definition, kind in collect_pattern_hits(content or ""):
            term = term.strip().rstrip(":,;")
            if len(term) < 3 or len(term) > 100:
                continue
            tn = norm_alnum(term)
            if not tn:
                continue
            key = (did, tn)
            if key in seen:
                continue
            seen.add(key)
            ord_counter += 1
            cur.execute(
                """INSERT INTO glossary_terms
                   (document_id, chunk_id, section_code, term, term_normalized,
                    definition, scope, ord)
                   VALUES (?, ?, ?, ?, ?, ?, 'pattern', ?)""",
                (did, cid, section, term, tn, definition[:2000], ord_counter),
            )
            inserted += 1

    pass1 = inserted

    # Pass 2 — sections titled "Definitions"/"Defined Terms"/"Glossary".
    pass2_section_hits = 0
    pass2_term_hits = 0
    sections = cur.execute(
        """SELECT id, document_id, section_code, title, first_chunk_id, last_chunk_id
           FROM section_hierarchy"""
    ).fetchall()
    for _, did, code, title, first_cid, last_cid in sections:
        if not is_definition_section(title):
            continue
        pass2_section_hits += 1
        chunk_rows = cur.execute(
            """SELECT id, content FROM document_chunks
               WHERE document_id=? AND id BETWEEN ? AND ?
               ORDER BY chunk_index""",
            (did, first_cid or 0, last_cid or 1 << 30),
        ).fetchall()
        for cid, content in chunk_rows:
            for term, definition in parse_definition_list(content or ""):
                tn = norm_alnum(term)
                if not tn:
                    continue
                key = (did, tn)
                if key in seen:
                    continue
                seen.add(key)
                ord_counter += 1
                cur.execute(
                    """INSERT INTO glossary_terms
                       (document_id, chunk_id, section_code, term, term_normalized,
                        definition, scope, ord)
                       VALUES (?, ?, ?, ?, ?, ?, 'definitions-section', ?)""",
                    (did, cid, code, term, tn, definition[:2000], ord_counter),
                )
                inserted += 1
                pass2_term_hits += 1

    con.commit()

    print()
    stat("Pattern-matched terms", pass1)
    stat("Definition-section hits", pass2_section_hits)
    stat("Section-walk terms", pass2_term_hits)
    stat("Total terms persisted", inserted)

    print()
    print("  Sample terms:")
    for term, definition, doc_id in cur.execute(
        """SELECT term, substr(definition,1,160), document_id
           FROM glossary_terms ORDER BY RANDOM() LIMIT 6"""
    ):
        print(f"    [{doc_id}] {term} — {definition}...")

    return 0


if __name__ == "__main__":
    sys.exit(main())
