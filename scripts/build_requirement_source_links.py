"""Build a precomputed linkage table from rfp_requirements.id to the
document_chunks.id whose content contains the requirement's source_text.

Why this exists
---------------
Each rfp_requirements row stores a short source_text snippet that the
extraction prompt instructs the LLM to keep verbatim. To draft a faithful
response we need the FULL surrounding paragraph, which lives in
document_chunks. Searching every time is expensive; this table makes
the lookup O(1).

How it handles the broken cases
-------------------------------
1. .docx-sourced requirements where every chunk got page_number=1:
   page-level lookup is useless, so we substring-search the chunk content
   instead. Most match.
2. doc_id=40 ("__consolidation_run__") has no chunks of its own, but each
   row has notes 'Materialized from ConsolidatedRequirement N'. We parse
   that, look up consolidated_requirements.source_document_id, and search
   THAT document's chunks instead.
3. Source-text values that are LLM-compressed (forms with checkboxes
   collapsed onto one line, or '...' ellipses) are matched with three
   progressively looser normalizations: exact, whitespace-collapsed,
   alphanumeric-only. The link record records which one matched.

Re-runnable: drops and rebuilds the link table from scratch.
"""
from __future__ import annotations

import re
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "rfp.db"


def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def norm_alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def main() -> int:
    if not DB_PATH.exists():
        print(f"DB not found: {DB_PATH}", file=sys.stderr)
        return 1

    t0 = time.time()
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON")
    cur = con.cursor()

    cur.executescript(
        """
        DROP TABLE IF EXISTS requirement_source_links;
        CREATE TABLE requirement_source_links (
            requirement_id INTEGER PRIMARY KEY
                REFERENCES rfp_requirements(id) ON DELETE CASCADE,
            chunk_id       INTEGER REFERENCES document_chunks(id),
            chunk_doc_id   INTEGER,
            match_quality  TEXT NOT NULL,
            redirected_from_doc_id INTEGER,
            note           TEXT
        );
        CREATE INDEX idx_rsl_chunk ON requirement_source_links(chunk_id);
        CREATE INDEX idx_rsl_quality ON requirement_source_links(match_quality);
        """
    )

    # Index every chunk three ways, grouped by doc.
    chunks_by_doc: dict[int, list[tuple[int, str, str, str]]] = defaultdict(list)
    rows = cur.execute(
        "SELECT id, document_id, content FROM document_chunks"
    ).fetchall()
    for cid, did, content in rows:
        content = content or ""
        chunks_by_doc[did].append(
            (cid, content, norm_ws(content), norm_alnum(content))
        )
    print(f"  indexed {len(rows):,} chunks across {len(chunks_by_doc)} docs")

    # consolidated_requirement.id -> source_document_id (for doc-40 redirects)
    consol_src_doc: dict[int, int] = {}
    for cr_id, src_doc_id in cur.execute(
        "SELECT id, source_document_id FROM consolidated_requirements"
    ):
        if src_doc_id is not None:
            consol_src_doc[cr_id] = src_doc_id

    re_consol = re.compile(r"ConsolidatedRequirement\s+(\d+)")

    counters = defaultdict(int)
    consol_redirect_pairs = defaultdict(int)
    inserts = []

    req_rows = cur.execute(
        "SELECT id, document_id, source_text, notes FROM rfp_requirements"
    ).fetchall()
    print(f"  scanning {len(req_rows):,} requirements")

    for req_id, doc_id, src_text, notes in req_rows:
        src_text = (src_text or "").strip()
        if not src_text:
            counters["no_source_text"] += 1
            inserts.append(
                (req_id, None, None, "no_source_text", None, None)
            )
            continue

        # Pick which doc's chunks to search.
        target_doc = doc_id
        redirected_from = None
        candidate_chunks = chunks_by_doc.get(doc_id) or []
        if not candidate_chunks and notes:
            m = re_consol.search(notes)
            if m:
                cr_id = int(m.group(1))
                src_doc = consol_src_doc.get(cr_id)
                if src_doc and src_doc in chunks_by_doc:
                    candidate_chunks = chunks_by_doc[src_doc]
                    redirected_from = doc_id
                    target_doc = src_doc

        if not candidate_chunks:
            counters["no_chunks_for_doc"] += 1
            inserts.append(
                (req_id, None, None, "no_chunks_for_doc", redirected_from, None)
            )
            continue

        src_ws = norm_ws(src_text)
        src_alnum = norm_alnum(src_text)

        found_cid = None
        quality = None
        for cid, content, _, _ in candidate_chunks:
            if src_text in content:
                found_cid, quality = cid, "verbatim"
                break
        if not found_cid and src_ws:
            for cid, _, content_ws, _ in candidate_chunks:
                if src_ws in content_ws:
                    found_cid, quality = cid, "whitespace"
                    break
        if not found_cid and src_alnum:
            for cid, _, _, content_alnum in candidate_chunks:
                if src_alnum in content_alnum:
                    found_cid, quality = cid, "alnum_compressed"
                    break

        if not found_cid:
            # Span fallback: long quotes often cross chunk boundaries because
            # the chunker split mid-sentence. Slide a 60-char window across
            # the alphanumeric-normalized source_text in 30-char steps; the
            # first chunk that contains any window wins. The UI can fan out
            # to neighboring chunks when it needs the full quote.
            window = 60
            step = 30
            anchor_quality = None
            for offset in range(0, max(1, len(src_alnum) - window + 1), step):
                probe = src_alnum[offset:offset + window]
                if len(probe) < 40:
                    continue
                for cid, _, _, content_alnum in candidate_chunks:
                    if probe in content_alnum:
                        found_cid = cid
                        if offset == 0:
                            anchor_quality = "span_head"
                        elif offset + window >= len(src_alnum):
                            anchor_quality = "span_tail"
                        else:
                            anchor_quality = "span_middle"
                        break
                if found_cid:
                    break
            if anchor_quality:
                quality = anchor_quality

        if not found_cid:
            # Last-ditch: scan every other doc's chunks for an exact substring.
            # Catches cases where the requirement was attributed to the wrong
            # doc (e.g. a duplicate ingest) but the source still exists.
            for did, lst in chunks_by_doc.items():
                if did == target_doc:
                    continue
                for cid, content, _, _ in lst:
                    if src_text in content:
                        found_cid, quality = cid, "cross_doc_verbatim"
                        target_doc = did
                        redirected_from = doc_id
                        break
                if found_cid:
                    break

        if not found_cid:
            counters["unfindable"] += 1
            inserts.append(
                (req_id, None, None, "unfindable", redirected_from, None)
            )
            continue

        counters[quality] += 1
        if redirected_from is not None:
            consol_redirect_pairs[(redirected_from, target_doc)] += 1
        inserts.append(
            (
                req_id,
                found_cid,
                target_doc,
                quality,
                redirected_from,
                None,
            )
        )

    cur.executemany(
        """INSERT INTO requirement_source_links
           (requirement_id, chunk_id, chunk_doc_id, match_quality,
            redirected_from_doc_id, note)
           VALUES (?, ?, ?, ?, ?, ?)""",
        inserts,
    )
    con.commit()

    print()
    print("=== match-quality breakdown ===")
    total = sum(counters.values())
    for k in (
        "verbatim",
        "whitespace",
        "alnum_compressed",
        "span_head",
        "span_middle",
        "span_tail",
        "cross_doc_verbatim",
        "unfindable",
        "no_chunks_for_doc",
        "no_source_text",
    ):
        n = counters.get(k, 0)
        pct = 100 * n / total if total else 0
        print(f"  {k:24s} {n:6d}  ({pct:5.1f}%)")
    print(f"  {'TOTAL':24s} {total:6d}")

    if consol_redirect_pairs:
        print()
        print("=== consolidated/cross-doc redirects ===")
        for (frm, to), n in sorted(consol_redirect_pairs.items()):
            print(f"  doc {frm} -> doc {to}: {n} requirements")

    print()
    print(f"built in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
