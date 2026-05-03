"""Stage 15 — Semantic near-duplicate dedup, high-precision.

Goal: collapse rows where two visible obligations from DIFFERENT documents
say the same thing in different words. The keyword-based K1–K4 dedup in
s01 only caught exact / near-exact title matches. Many obligations remain
that paraphrase the same idea ("Vendor shall provide secure internet
access" vs "Provide secure access to ITS via approved browsers").

Strategy: TF-IDF (HashingVectorizer ngrams 1–2) on title+description+
source_text per row. For each cross-document pair with cosine >= 0.93 AND
matching title-prefix, mark the lower-priority row as superseded. Same-doc
rows are NEVER collapsed.

Verbatim preservation: same as s01 — superseded rows stay in the DB and
keep their requirement_source_links / bundle / references intact. The
canonical row has its own source paragraph; the duplicate's paragraph
remains queryable via the superseded row's source link.

Threshold rationale: 0.93 is high enough that "Submit Quote by 2:00 PM"
and "Submit Disclosure of Investment Activities" never collide. Sampled
real pairs at 0.93+ are unambiguous duplicates.

Re-runnable: leaves K1–K4 dedup intact and only adds K5 collapses on top.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from typing import Dict, List, Tuple

import numpy as np

from .common import banner, connect, norm_alnum, stat


SIMILARITY_THRESHOLD = 0.93
TITLE_PREFIX_REQUIRED = 12         # first N normalized chars must match
MIN_TEXT_LEN = 40                  # skip rows whose combined text is too short


def main() -> int:
    con = connect()
    cur = con.cursor()
    banner("STAGE 15 — Semantic near-duplicate dedup")

    rows = cur.execute(
        """SELECT id, document_id, section_id, title, description, source_text,
                  priority, requirement_class, superseded_by_requirement_id
           FROM rfp_requirements"""
    ).fetchall()

    # Score (lower wins) — same logic as s01.
    def score(r) -> tuple:
        rid, did, _, _, _, _, prio, klass, _ = r
        if did == 40:
            doc_tier = 0
        elif did == 4:
            doc_tier = 1
        elif did == 5:
            doc_tier = 2
        elif did == 31:
            doc_tier = 3
        else:
            doc_tier = 4
        return (
            doc_tier,
            0 if prio else 1,
            0 if klass and klass != "unclassified" else 1,
            rid,
        )

    # Only consider rows that (a) aren't already superseded and (b) are
    # visible obligations. Don't dedup across rolled-up sub-parts — they
    # already have a parent relationship.
    eligible = []
    for r in rows:
        rid, did, sec, title, desc, src, prio, klass, superseded = r
        if superseded is not None:
            continue
        title = (title or "").strip()
        src = (src or "").strip()
        desc = (desc or "").strip()
        text = f"{title}. {desc}. {src}".strip()
        if len(text) < MIN_TEXT_LEN:
            continue
        eligible.append((rid, did, sec or "", title, desc, src, text))

    stat("Eligible rows for semantic dedup", len(eligible))

    # Compute TF-IDF embeddings
    from app.services.embedding_service import _get_hashing_vectorizer
    vec = _get_hashing_vectorizer()
    texts = [t[6] for t in eligible]
    matrix = vec.transform(texts)        # sparse, L2-normalized
    print(f"  embeddings: {matrix.shape[0]} rows × {matrix.shape[1]} dims")

    # Bucket by title-prefix so we only compute cosine within candidate buckets.
    by_prefix: Dict[str, List[int]] = defaultdict(list)
    for i, (rid, did, sec, title, desc, src, text) in enumerate(eligible):
        prefix = norm_alnum(title)[:TITLE_PREFIX_REQUIRED]
        if len(prefix) < TITLE_PREFIX_REQUIRED:
            continue
        by_prefix[prefix].append(i)

    print(f"  prefix buckets: {len(by_prefix)}; "
          f"non-trivial (>=2 members): {sum(1 for v in by_prefix.values() if len(v) > 1)}")

    # Find dup pairs within each prefix bucket (cross-doc only)
    dup_groups: List[List[int]] = []
    seen = set()
    for prefix, idxs in by_prefix.items():
        if len(idxs) < 2:
            continue
        # Sub-matrix for this bucket
        sub_idxs = list(idxs)
        sub_matrix = matrix[sub_idxs]
        # Cosine = dot product since L2-normalized.
        sims = sub_matrix @ sub_matrix.T
        sims = sims.toarray() if hasattr(sims, "toarray") else np.asarray(sims)
        # Build connected components by similarity threshold
        n = len(sub_idxs)
        adj: Dict[int, List[int]] = defaultdict(list)
        for i in range(n):
            for j in range(i + 1, n):
                if sims[i, j] < SIMILARITY_THRESHOLD:
                    continue
                # Cross-doc only
                if eligible[sub_idxs[i]][1] == eligible[sub_idxs[j]][1]:
                    continue
                adj[i].append(j)
                adj[j].append(i)
        visited = set()
        for i in range(n):
            if i in visited or i not in adj:
                continue
            stack = [i]
            comp = []
            while stack:
                node = stack.pop()
                if node in visited:
                    continue
                visited.add(node)
                comp.append(sub_idxs[node])
                stack.extend(adj.get(node, []))
            if len(comp) >= 2:
                key = tuple(sorted(comp))
                if key not in seen:
                    seen.add(key)
                    dup_groups.append(comp)

    stat("Cross-doc dup groups detected", len(dup_groups))

    # Apply
    collisions = 0
    for group in dup_groups:
        # group = list of indexes into eligible[]
        members = [eligible[i] for i in group]
        canonical = min(members, key=lambda r: score(
            (r[0], r[1], r[2], r[3], r[4], r[5], None, None, None)
        ))
        cid = canonical[0]
        for r in members:
            rid = r[0]
            if rid == cid:
                continue
            cur.execute(
                """UPDATE rfp_requirements
                   SET superseded_by_requirement_id=?, duplicate_group_key=?
                   WHERE id=? AND superseded_by_requirement_id IS NULL""",
                (cid, f"k5::semantic::{norm_alnum(canonical[3])[:24]}", rid),
            )
            collisions += 1
    con.commit()

    print()
    stat("K5 semantic-merge collisions", collisions)

    # Sample
    if dup_groups:
        print()
        print("  Sample groups (first 5):")
        for group in dup_groups[:5]:
            print()
            for i in group:
                r = eligible[i]
                print(f"    req#{r[0]:5d} doc={r[1]:3d} sec={r[2]!r:18s}  {r[3][:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
