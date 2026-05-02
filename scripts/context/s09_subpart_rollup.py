"""Stage 9 — Sub-part rollup.

Many "requirements" are actually sub-bullets of a single obligation. The
extractor split them because each bullet sentence read as a discrete shall.
The user shouldn't be forced to read each bullet as a top-level item.

Rollup signal:
  * Same source chunk (requirement_source_links.chunk_id is identical) — i.e.
    every bullet was extracted from the same paragraph.
  * Same canonical document and same section_id.
  * Adjacent / near-adjacent rfp_requirements.id values within that group.

For each cluster of size >= 2 we pick a parent (lowest id) and assign every
other member to it. The parent's identity is preserved as a real
``rfp_requirements`` row (no synthetic rows). The browse tree should query
``requirement_groups`` and treat the parent as the visible node, the
children as collapsible sub-parts.

Schema:
  requirement_groups(
    id PK,
    parent_requirement_id INTEGER,    -- visible at top level
    child_requirement_id  INTEGER,    -- nested under parent
    rollup_kind           TEXT,       -- 'same-chunk' | 'lead-phrase'
    rollup_label          TEXT,       -- short summary for the parent header
    UNIQUE (child_requirement_id)
  )

Adds an indexable column to rfp_requirements:
  rollup_role TEXT  -- 'parent' | 'child' | NULL (standalone)

Re-runnable: drops + recreates table; clears column.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from typing import Dict, List, Tuple

from .common import banner, connect, stat


def ensure_column(cur, table: str, col: str, decl: str) -> None:
    cols = {row[1] for row in cur.execute(f"PRAGMA table_info('{table}')")}
    if col not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def main() -> int:
    con = connect()
    cur = con.cursor()
    banner("STAGE 9 — Sub-part rollup")

    cur.executescript(
        """
        DROP TABLE IF EXISTS requirement_groups;
        CREATE TABLE requirement_groups (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_requirement_id INTEGER NOT NULL,
            child_requirement_id  INTEGER NOT NULL UNIQUE,
            rollup_kind           TEXT NOT NULL,
            rollup_label          TEXT
        );
        CREATE INDEX idx_rg_parent ON requirement_groups(parent_requirement_id);
        """
    )
    ensure_column(cur, "rfp_requirements", "rollup_role", "TEXT")
    cur.execute("UPDATE rfp_requirements SET rollup_role = NULL")
    con.commit()

    # Build (chunk_id, doc_id, section_id) clusters.
    rows = cur.execute(
        """SELECT r.id, r.document_id, r.section_id, r.title,
                  lnk.chunk_id, r.priority, r.category, r.requirement_class
           FROM rfp_requirements r
           LEFT JOIN requirement_source_links lnk ON lnk.requirement_id = r.id
           WHERE r.superseded_by_requirement_id IS NULL"""
    ).fetchall()
    stat("Surviving requirements", len(rows))

    # Group by (chunk_id, doc_id, section_id-lower)
    by_key: Dict[Tuple, List[tuple]] = defaultdict(list)
    for rid, did, sec, title, cid, prio, cat, klass in rows:
        if cid is None:
            continue  # standalone, no clustering anchor
        key = (cid, did, (sec or "").lower())
        by_key[key].append((rid, title or "", prio, klass))

    parent_map: Dict[int, int] = {}  # child -> parent
    label_map: Dict[int, str] = {}   # parent -> rollup label
    n_clusters = 0
    n_children = 0

    def score_for_parent(item: tuple) -> tuple:
        rid, title, prio, klass = item
        # Prefer rows with priority + classification, then lowest id.
        return (
            0 if prio else 1,
            0 if klass and klass != "unclassified" else 1,
            rid,
        )

    for key, members in by_key.items():
        if len(members) < 2:
            continue
        members_sorted = sorted(members, key=score_for_parent)
        parent_rid, parent_title, _, _ = members_sorted[0]
        n_clusters += 1
        for child in members_sorted[1:]:
            child_rid = child[0]
            if child_rid == parent_rid:
                continue
            parent_map[child_rid] = parent_rid
            n_children += 1
        label_map[parent_rid] = parent_title[:160]

    # Persist
    inserts = []
    for child_rid, parent_rid in parent_map.items():
        inserts.append(
            (parent_rid, child_rid, "same-chunk", label_map.get(parent_rid))
        )
    cur.executemany(
        """INSERT INTO requirement_groups
           (parent_requirement_id, child_requirement_id, rollup_kind, rollup_label)
           VALUES (?, ?, ?, ?)""",
        inserts,
    )
    # Tag rfp_requirements
    parents = set(parent_map.values())
    for rid in parents:
        cur.execute("UPDATE rfp_requirements SET rollup_role='parent' WHERE id=?", (rid,))
    for rid in parent_map.keys():
        cur.execute("UPDATE rfp_requirements SET rollup_role='child' WHERE id=?", (rid,))
    con.commit()

    print()
    stat("Clusters formed (>= 2 members)", n_clusters)
    stat("Child requirements rolled up under a parent", n_children)
    stat("Surviving parents (visible top-level rows)",
         len(rows) - n_children, len(rows))

    # Sample
    print()
    print("  Sample clusters (top 5 by member count):")
    sample = cur.execute(
        """SELECT parent_requirement_id, COUNT(*) AS n
           FROM requirement_groups GROUP BY parent_requirement_id ORDER BY n DESC LIMIT 5"""
    ).fetchall()
    for parent_id, n in sample:
        prow = cur.execute(
            "SELECT title, section_id FROM rfp_requirements WHERE id=?", (parent_id,)
        ).fetchone()
        kids = cur.execute(
            """SELECT r.id, r.title FROM requirement_groups rg
               JOIN rfp_requirements r ON r.id = rg.child_requirement_id
               WHERE rg.parent_requirement_id=? ORDER BY r.id LIMIT 3""",
            (parent_id,),
        ).fetchall()
        print(f"\n    parent#{parent_id} (sec={prow[1]!r}) {prow[0][:60]!r} +{n} children")
        for kid_id, kid_title in kids:
            print(f"      └─ child#{kid_id} {kid_title[:60]!r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
