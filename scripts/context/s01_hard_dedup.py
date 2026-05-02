"""Stage 1 — Hard deduplication of rfp_requirements.

Two collapse keys:

  K1 = (norm_alnum(title), norm_alnum(source_text), section_id)
  K2 = (norm_alnum(source_text), section_id)         # title-insensitive

K1 is the strict "same requirement re-extracted" case (master + revised
master + amendments routinely produce identical title+source text under the
same section).

K2 catches cases where the LLM authored slightly different titles for the
same source text in different docs.

For each duplicate group we pick a canonical row (lowest id, prefer rows
with priority/category set) and mark the rest as superseded by adding a
new column ``superseded_by_requirement_id`` (additive — no destructive
deletes). Downstream queries should filter on ``superseded_by_requirement_id IS NULL``.

Re-runnable: clears prior superseded_by_* values before recomputing.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from typing import Dict, List

from .common import banner, connect, norm_alnum, stat


def ensure_column(cur, table: str, col: str, decl: str) -> None:
    existing = {row[1] for row in cur.execute(f"PRAGMA table_info('{table}')")}
    if col not in existing:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def main() -> int:
    con = connect()
    cur = con.cursor()

    banner("STAGE 1 — Hard deduplication")

    ensure_column(
        cur,
        "rfp_requirements",
        "superseded_by_requirement_id",
        "INTEGER REFERENCES rfp_requirements(id)",
    )
    ensure_column(cur, "rfp_requirements", "duplicate_group_key", "TEXT")
    cur.execute(
        """UPDATE rfp_requirements
           SET superseded_by_requirement_id = NULL,
               duplicate_group_key = NULL"""
    )
    con.commit()

    rows = cur.execute(
        """SELECT id, document_id, section_id, title, source_text,
                  priority, category, requirement_class
           FROM rfp_requirements"""
    ).fetchall()
    stat("Total requirements", len(rows))

    # Build groups by both keys; we'll prefer K1 if both apply.
    k1: Dict[tuple, List] = defaultdict(list)
    k2: Dict[tuple, List] = defaultdict(list)
    for r in rows:
        rid, did, section, title, src, prio, cat, klass = r
        section_n = (section or "").strip().lower()
        title_n = norm_alnum(title)
        src_n = norm_alnum(src)
        if not src_n and not title_n:
            continue
        k1[(title_n, src_n, section_n)].append(r)
        k2[(src_n, section_n)].append(r)

    def score(r) -> tuple:
        """Lower = better canonical candidate.

        Priority order (each lower-is-better tier):
          1. doc 40 = consolidated baseline (amendments already merged) — winner.
          2. The latest revision of the master solicitation (doc 4 here)
             before older revisions (doc 31) and PDF mirrors (doc 5).
          3. Has explicit priority assigned.
          4. Has classifier output.
          5. Lowest id (deterministic tiebreak).
        """
        rid, did, section, title, src, prio, cat, klass = r
        # doc 40 = consolidation_run; treat as canonical post-amendment.
        if did == 40:
            doc_tier = 0
        elif did == 4:
            doc_tier = 1   # newest revised .docx master
        elif did == 5:
            doc_tier = 2   # PDF mirror of master
        elif did == 31:
            doc_tier = 3   # older revised master
        else:
            doc_tier = 4
        score_prio = 0 if prio else 1
        score_class = 0 if klass and klass != "unclassified" else 1
        return (doc_tier, score_prio, score_class, rid)

    super_map: Dict[int, int] = {}
    group_keys: Dict[int, str] = {}

    # K1 first
    k1_collisions = 0
    for key, group in k1.items():
        if len(group) <= 1:
            continue
        canonical = min(group, key=score)
        cid = canonical[0]
        for r in group:
            if r[0] == cid:
                continue
            if r[0] in super_map:
                continue
            super_map[r[0]] = cid
            group_keys[r[0]] = f"k1::{key[2]}::{key[0][:30]}::{key[1][:30]}"
            k1_collisions += 1
        group_keys[cid] = f"k1::{key[2]}::{key[0][:30]}::{key[1][:30]}"

    # K2 — only for rows not already handled
    k2_collisions = 0
    for key, group in k2.items():
        candidates = [r for r in group if r[0] not in super_map]
        if len(candidates) <= 1:
            continue
        canonical = min(candidates, key=score)
        cid = canonical[0]
        for r in candidates:
            if r[0] == cid:
                continue
            super_map[r[0]] = cid
            group_keys[r[0]] = f"k2::{key[1]}::{key[0][:50]}"
            k2_collisions += 1
        group_keys.setdefault(cid, f"k2::{key[1]}::{key[0][:50]}")

    # Persist
    cur.executemany(
        "UPDATE rfp_requirements SET superseded_by_requirement_id=? WHERE id=?",
        [(canon, rid) for rid, canon in super_map.items()],
    )
    cur.executemany(
        "UPDATE rfp_requirements SET duplicate_group_key=? WHERE id=?",
        [(k, rid) for rid, k in group_keys.items()],
    )
    con.commit()

    surviving = cur.execute(
        "SELECT COUNT(*) FROM rfp_requirements WHERE superseded_by_requirement_id IS NULL"
    ).fetchone()[0]
    superseded = cur.execute(
        "SELECT COUNT(*) FROM rfp_requirements WHERE superseded_by_requirement_id IS NOT NULL"
    ).fetchone()[0]

    print()
    stat("K1 (title+source+section) duplicates", k1_collisions, len(rows))
    stat("K2 (source+section) duplicates",       k2_collisions, len(rows))
    stat("Surviving canonical rows",              surviving,    len(rows))
    stat("Superseded (hidden in default views)",  superseded,   len(rows))

    return 0


if __name__ == "__main__":
    sys.exit(main())
