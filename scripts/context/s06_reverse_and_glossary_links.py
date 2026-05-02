"""Stage 6 — Reverse references and glossary linkage.

Builds two derived tables:

  requirement_reverse_refs(
    requirement_id,           -- the requirement that is BEING referred TO
    referrer_requirement_id,  -- a requirement whose text references it
    via_section_id,           -- which section_hierarchy node mediated
    confidence
  )

  requirement_glossary_links(
    requirement_id,
    glossary_term_id,
    occurrences INTEGER       -- how many times the term appears in the req
  )

Reverse refs come from requirement_references rows whose
target_section_id matches a section that contains the requirement (i.e.,
the requirement lives inside that section). This produces the "what else
points at me" view.

Glossary linkage is built by scanning each requirement's
(title || description || source_text) for occurrences of any glossary
term (word-bounded, case-insensitive). To avoid matching common short
acronyms inside unrelated words we require the term to be at least 3
chars and have a word boundary on both sides.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from typing import Dict, List, Tuple

from .common import banner, connect, stat


def main() -> int:
    con = connect()
    cur = con.cursor()
    banner("STAGE 6 — Reverse refs + glossary links")

    cur.executescript(
        """
        DROP TABLE IF EXISTS requirement_reverse_refs;
        CREATE TABLE requirement_reverse_refs (
            id                       INTEGER PRIMARY KEY AUTOINCREMENT,
            requirement_id           INTEGER NOT NULL REFERENCES rfp_requirements(id),
            referrer_requirement_id  INTEGER NOT NULL REFERENCES rfp_requirements(id),
            via_section_id           INTEGER REFERENCES section_hierarchy(id),
            confidence               REAL NOT NULL DEFAULT 1.0
        );
        CREATE INDEX idx_rrev_req     ON requirement_reverse_refs(requirement_id);
        CREATE INDEX idx_rrev_referrer ON requirement_reverse_refs(referrer_requirement_id);

        DROP TABLE IF EXISTS requirement_glossary_links;
        CREATE TABLE requirement_glossary_links (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            requirement_id   INTEGER NOT NULL REFERENCES rfp_requirements(id),
            glossary_term_id INTEGER NOT NULL REFERENCES glossary_terms(id),
            occurrences      INTEGER NOT NULL DEFAULT 1,
            UNIQUE (requirement_id, glossary_term_id)
        );
        CREATE INDEX idx_rgl_req  ON requirement_glossary_links(requirement_id);
        CREATE INDEX idx_rgl_term ON requirement_glossary_links(glossary_term_id);
        """
    )

    # --- Reverse references ---
    # For each requirement, find its containing section (by doc + section_id).
    # Then for each requirement_references row whose target_section_id matches
    # any section a requirement lives in, register a reverse edge.

    # Build: section_id (sh.id) -> list of requirement_ids living in that section
    sec_to_reqs: Dict[int, List[int]] = defaultdict(list)
    rows = cur.execute(
        """SELECT r.id, r.document_id, r.section_id, sh.id AS sh_id
           FROM rfp_requirements r
           LEFT JOIN section_hierarchy sh
             ON sh.document_id = r.document_id AND sh.section_code = r.section_id
           WHERE r.superseded_by_requirement_id IS NULL"""
    ).fetchall()
    for rid, did, scode, sh_id in rows:
        if sh_id:
            sec_to_reqs[sh_id].append(rid)

    inserted_rev = 0
    seen_pairs = set()
    for rr_id, referrer, target_sid in cur.execute(
        """SELECT id, requirement_id, target_section_id FROM requirement_references
           WHERE target_section_id IS NOT NULL"""
    ):
        targets = sec_to_reqs.get(target_sid, [])
        for tgt_req in targets:
            if tgt_req == referrer:
                continue
            key = (tgt_req, referrer, target_sid)
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            cur.execute(
                """INSERT INTO requirement_reverse_refs
                   (requirement_id, referrer_requirement_id, via_section_id, confidence)
                   VALUES (?, ?, ?, 0.9)""",
                (tgt_req, referrer, target_sid),
            )
            inserted_rev += 1
    con.commit()

    # --- Glossary linkage ---
    # Pull all terms once. Build a single regex that ORs all terms (longest first
    # to prefer multi-word matches over single-word substrings).
    terms = cur.execute(
        "SELECT id, term, term_normalized FROM glossary_terms"
    ).fetchall()
    if not terms:
        print("  no glossary terms — skipping linkage")
        stat("Reverse-ref edges", inserted_rev)
        return 0

    norm_to_id: Dict[str, int] = {}
    safe_terms: List[Tuple[str, int]] = []
    for tid, term, tn in terms:
        if len(tn) < 3:
            continue
        # Skip noisy "fragments-as-terms" — must contain at least one space-or-hyphen
        # OR be all-caps acronym OR start with a Capitalized word.
        if " " not in term and "-" not in term and not (term.isupper() or term[0].isupper()):
            continue
        # Don't double-register the same normalized term
        if tn in norm_to_id:
            continue
        norm_to_id[tn] = tid
        # Build a regex pattern that's case-insensitive with word boundaries
        # and tolerates internal whitespace differences.
        pat = r"\b" + re.escape(term.strip()) + r"\b"
        safe_terms.append((pat, tid))

    safe_terms.sort(key=lambda x: -len(x[0]))
    big_pat = re.compile("|".join(f"(?P<t{i}>{p})" for i, (p, _) in enumerate(safe_terms)), re.I)
    pat_to_tid: Dict[str, int] = {f"t{i}": tid for i, (_, tid) in enumerate(safe_terms)}

    inserted_gloss = 0
    seen: set = set()
    for req_id, src, desc, title in cur.execute(
        """SELECT id, source_text, description, title
           FROM rfp_requirements WHERE superseded_by_requirement_id IS NULL"""
    ):
        text = " ".join(filter(None, [title, desc, src]))
        counts: Dict[int, int] = defaultdict(int)
        for m in big_pat.finditer(text or ""):
            for grp_name, tid in pat_to_tid.items():
                if m.group(grp_name):
                    counts[tid] += 1
                    break
        for tid, n in counts.items():
            key = (req_id, tid)
            if key in seen:
                continue
            seen.add(key)
            cur.execute(
                """INSERT INTO requirement_glossary_links
                   (requirement_id, glossary_term_id, occurrences)
                   VALUES (?, ?, ?)""",
                (req_id, tid, n),
            )
            inserted_gloss += 1
    con.commit()

    print()
    stat("Reverse-ref edges", inserted_rev)
    stat("Glossary linkages", inserted_gloss)
    print()
    print("  Reqs with at least one inbound reference:")
    n = cur.execute(
        "SELECT COUNT(DISTINCT requirement_id) FROM requirement_reverse_refs"
    ).fetchone()[0]
    print(f"    {n}")
    print("  Reqs with at least one glossary link:")
    n = cur.execute(
        "SELECT COUNT(DISTINCT requirement_id) FROM requirement_glossary_links"
    ).fetchone()[0]
    print(f"    {n}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
