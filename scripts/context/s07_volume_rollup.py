"""Stage 7 — Volume rollup.

Build a 3-level browsing structure that the UI can render as a tree without
losing any underlying obligation:

    Theme (e.g. "Solution Architecture")
      └── Section (section_hierarchy node)
            └── Atomic requirement (rfp_requirements row)

We do NOT mutate rfp_requirements. Instead we add two rollup tables:

  requirement_themes(
    id, label, slug, ord, description
  )

  requirement_theme_assignments(
    requirement_id, theme_id, confidence
  )

Theme assignment is rule-based + section-pattern based, so it's
deterministic and re-runnable. Rules below.

Each requirement also gets pinned to a primary section_hierarchy row via
``requirement_primary_section`` (one column added to rfp_requirements,
nullable). This is the "where does this req live in the tree" pointer
that the UI uses for the Section level.

Re-runnable: drops + recreates theme tables; clears column before refilling.
"""
from __future__ import annotations

import re
import sys
from typing import List, Optional, Tuple

from .common import banner, connect, stat


# Themes are ordered for stable display. Each is matched in order; first hit
# wins. Each theme has a list of conditions; ANY match assigns the theme.
THEMES: List[dict] = [
    {
        "label": "Solution Architecture & Software",
        "slug": "solution-architecture",
        "section_prefixes": ["3.2", "3.4", "3.5", "4.20", "4.21", "4.22"],
        "section_keywords": ["software", "ngvid", "architecture", "platform",
                              "system design"],
        "title_keywords": ["software", "architecture", "platform", "rules engine"],
    },
    {
        "label": "Inspection & Test Operations",
        "slug": "inspection-operations",
        "section_prefixes": ["3.3", "3.6", "3.7", "3.8", "3.10", "3.11", "3.12",
                              "3.13", "3.14", "3.15", "3.16", "3.17",
                              "Appendix 3.1", "Appendix 3.2", "Appendix 3.3",
                              "Appendix 3.4"],
        "section_keywords": ["inspection", "test", "calibration", "lift",
                              "obd", "emission"],
        "title_keywords": ["inspection", "test", "calibration", "lift", "obd"],
    },
    {
        "label": "Service Levels & Performance",
        "slug": "service-levels",
        "section_prefixes": ["3.6.1", "3.39"],
        "section_keywords": ["sla", "service level", "performance test",
                              "wait time", "uptime"],
        "title_keywords": ["service level", "uptime", "wait time",
                            "performance test", "downtime",
                            "liquidated damages"],
    },
    {
        "label": "Reporting, Data & Analytics",
        "slug": "reporting",
        "section_prefixes": ["Appendix 3.2A", "Appendix 3.4F", "5.11"],
        "section_keywords": ["report", "analytics", "dashboard", "monitoring"],
        "title_keywords": ["report", "dashboard", "analytic", "monitor",
                            "log", "audit log"],
    },
    {
        "label": "Facilities, Buildings & Grounds",
        "slug": "facilities",
        "section_prefixes": ["Appendix 3.2H"],
        "section_keywords": ["facility", "building", "grounds", "maintenance"],
        "title_keywords": ["facility", "building", "grounds",
                            "janitorial", "snow", "landscape"],
    },
    {
        "label": "Personnel, Staffing & Training",
        "slug": "personnel",
        "section_prefixes": ["3.21", "3.22"],
        "section_keywords": ["staff", "training", "personnel", "key person"],
        "title_keywords": ["staff", "training", "key person", "supervisor",
                            "background check", "drug test", "uniform"],
    },
    {
        "label": "Pricing & Financial",
        "slug": "pricing",
        "section_prefixes": ["5", "Price"],
        "section_keywords": ["price", "pricing", "cost", "rate", "fee"],
        "title_keywords": ["price", "pricing", "cost", "rate", "fee",
                            "escalation", "invoic"],
    },
    {
        "label": "Service & Schedule",
        "slug": "service-schedule",
        "section_prefixes": ["3.31", "Hours of Operation"],
        "section_keywords": ["hours of operation", "schedule", "calendar"],
        "title_keywords": ["hours of operation", "schedule", "holiday",
                            "calendar"],
    },
    {
        "label": "Compliance Forms & Submission",
        "slug": "compliance-forms",
        "section_prefixes": ["3.13", "Form"],
        "section_keywords": ["form", "ownership disclosure", "set-asides",
                              "offer and acceptance"],
        "title_keywords": ["form", "ownership disclosure", "submit",
                            "macbride", "ada compliance"],
    },
    {
        "label": "Insurance & Bonding",
        "slug": "insurance",
        "section_keywords": ["insurance", "bond", "performance bond",
                              "liability", "indemnification"],
        "title_keywords": ["insurance", "bond", "performance bond",
                            "liability", "indemnif"],
    },
    {
        "label": "Information Security & Privacy",
        "slug": "info-sec",
        "doc_ids": [35],          # Third-party security questionnaire
        "section_keywords": ["security", "privacy", "data protection",
                              "confidential"],
        "title_keywords": ["security", "encrypt", "access control", "audit log",
                            "data protection", "privacy"],
    },
    {
        "label": "Standard Terms & Conditions",
        "slug": "standard-tcs",
        "doc_ids": [9],           # Combined NJ Standard T&Cs
        "section_keywords": [],
        "title_keywords": [],
    },
    {
        "label": "Collective Bargaining & Labor",
        "slug": "labor",
        "doc_ids": [10],
    },
    {
        "label": "Legal & Regulatory Citations",
        "slug": "legal-regulatory",
        "section_keywords": ["n.j.s.a", "n.j.a.c", "u.s.c", "c.f.r"],
        "title_keywords": ["statute", "regulation"],
    },
    {
        "label": "Amendments & Q&A",
        "slug": "amendments",
        "filename_substrings": ["amendment", "Round 2QA", "QA"],
    },
    {
        "label": "Pre-Quote Conference",
        "slug": "pre-quote",
        "doc_ids": [11],
    },
]


def assign_theme(
    section_code: Optional[str],
    title: Optional[str],
    description: Optional[str],
    source_text: Optional[str],
    document_id: int,
    filename: Optional[str],
) -> Tuple[int, float]:
    """Return (theme_index, confidence)."""
    sc = (section_code or "").lower()
    blob = " ".join(filter(None, [title, description, source_text])).lower()
    fname = (filename or "").lower()

    for idx, theme in enumerate(THEMES):
        if document_id in theme.get("doc_ids", []):
            return idx, 0.95
        for sub in theme.get("filename_substrings", []):
            if sub.lower() in fname:
                return idx, 0.9
        for prefix in theme.get("section_prefixes", []):
            if sc.startswith(prefix.lower()):
                return idx, 0.9
        for kw in theme.get("section_keywords", []):
            if kw.lower() in sc:
                return idx, 0.85
        for kw in theme.get("title_keywords", []):
            if kw.lower() in blob:
                return idx, 0.7

    # Catch-all bucket
    return -1, 0.0


def main() -> int:
    con = connect()
    cur = con.cursor()
    banner("STAGE 7 — Volume rollup (themes + section pinning)")

    cur.executescript(
        """
        DROP TABLE IF EXISTS requirement_theme_assignments;
        DROP TABLE IF EXISTS requirement_themes;

        CREATE TABLE requirement_themes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            slug        TEXT NOT NULL UNIQUE,
            label       TEXT NOT NULL,
            ord         INTEGER NOT NULL,
            description TEXT
        );

        CREATE TABLE requirement_theme_assignments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            requirement_id  INTEGER NOT NULL REFERENCES rfp_requirements(id),
            theme_id        INTEGER NOT NULL REFERENCES requirement_themes(id),
            confidence      REAL NOT NULL DEFAULT 1.0,
            UNIQUE (requirement_id)
        );
        CREATE INDEX idx_rta_theme ON requirement_theme_assignments(theme_id);
        """
    )

    # Insert themes (plus catch-all)
    theme_ids: List[int] = []
    for ord_, theme in enumerate(THEMES):
        cur.execute(
            "INSERT INTO requirement_themes (slug, label, ord) VALUES (?, ?, ?)",
            (theme["slug"], theme["label"], ord_),
        )
        theme_ids.append(cur.lastrowid)
    cur.execute(
        "INSERT INTO requirement_themes (slug, label, ord) VALUES (?, ?, ?)",
        ("uncategorized", "Uncategorized", len(THEMES) + 1),
    )
    catchall_id = cur.lastrowid

    # Add primary-section column if missing
    existing = {r[1] for r in cur.execute("PRAGMA table_info('rfp_requirements')")}
    if "primary_section_id" not in existing:
        cur.execute(
            "ALTER TABLE rfp_requirements ADD COLUMN primary_section_id INTEGER REFERENCES section_hierarchy(id)"
        )
    cur.execute("UPDATE rfp_requirements SET primary_section_id = NULL")

    # Build section lookup with several normalization passes:
    #   - exact (case-insensitive)
    #   - stripped of parentheticals  ("7 (Federal)" -> "7")
    #   - stripped of trailing alpha+numbers ("3.13.1.A" -> "3.13.1")
    #   - title contains keyword (last-resort, doc-scoped)
    sh_lookup: dict[Tuple[int, str], int] = {}
    sh_global_by_code: dict[str, int] = {}
    sh_titles: dict[Tuple[int, int], str] = {}     # (doc_id, sh_id) -> title
    for sid, did, code, title in cur.execute(
        "SELECT id, document_id, section_code, title FROM section_hierarchy"
    ):
        c = code.lower().strip()
        sh_lookup[(did, c)] = sid
        # Strip parenthetical "(Foo)" suffix
        c2 = re.sub(r"\s*\([^)]+\)\s*$", "", c).strip()
        if c2 and (did, c2) not in sh_lookup:
            sh_lookup[(did, c2)] = sid
        # Numeric-only prefix "3.13.1.A" -> "3.13.1"
        m = re.match(r"^(\d+(?:\.\d+)*)", c2)
        if m:
            num = m.group(1)
            if (did, num) not in sh_lookup:
                sh_lookup[(did, num)] = sid
        sh_global_by_code.setdefault(c, sid)
        if c2 != c:
            sh_global_by_code.setdefault(c2, sid)
        sh_titles[(did, sid)] = (title or "")

    # Iterate surviving reqs
    rows = cur.execute(
        """SELECT r.id, r.document_id, r.section_id, r.title, r.description,
                  r.source_text, ind.original_filename
           FROM rfp_requirements r
           LEFT JOIN ingested_documents ind ON ind.id = r.document_id
           WHERE r.superseded_by_requirement_id IS NULL"""
    ).fetchall()
    stat("Surviving requirements to rollup", len(rows))

    by_theme: dict[int, int] = {}
    pinned = 0
    for rid, did, scode, title, desc, src, fname in rows:
        idx, conf = assign_theme(scode, title, desc, src, did, fname)
        theme_id = theme_ids[idx] if idx >= 0 else catchall_id
        if idx >= 0:
            by_theme[theme_id] = by_theme.get(theme_id, 0) + 1
        else:
            by_theme[catchall_id] = by_theme.get(catchall_id, 0) + 1
        cur.execute(
            "INSERT INTO requirement_theme_assignments (requirement_id, theme_id, confidence) VALUES (?, ?, ?)",
            (rid, theme_id, conf),
        )

        # Pin to a section_hierarchy node — try progressively looser matches.
        sec_id = None
        if scode:
            scode_lower = scode.lower().strip()
            # 1. Exact (case-insensitive) same-doc
            sec_id = sh_lookup.get((did, scode_lower))
            # 2. Strip parenthetical suffix
            if not sec_id:
                stripped = re.sub(r"\s*\([^)]+\)\s*$", "", scode_lower).strip()
                if stripped and stripped != scode_lower:
                    sec_id = sh_lookup.get((did, stripped))
            # 3. Numeric prefix only ("3.13.1.A" -> "3.13.1")
            if not sec_id:
                m = re.match(r"^(\d+(?:\.\d+)*)", scode_lower)
                if m:
                    sec_id = sh_lookup.get((did, m.group(1)))
            # 4. Cross-doc exact
            if not sec_id:
                sec_id = sh_global_by_code.get(scode_lower)
            # 5. Title-keyword match within same doc (last resort)
            if not sec_id and len(scode_lower) >= 4:
                # Split section_id into significant tokens and search titles
                tokens = [t for t in re.split(r"[\s\-_/]+", scode_lower) if len(t) >= 4]
                for (d, sid_), title in sh_titles.items():
                    if d != did or not title:
                        continue
                    title_lower = title.lower()
                    if all(t in title_lower for t in tokens) if tokens else False:
                        sec_id = sid_
                        break
        # 6. Source-link fallback — use the chunk we already linked this req to.
        if not sec_id:
            link = cur.execute(
                """SELECT lnk.chunk_doc_id, ch.section_id
                   FROM requirement_source_links lnk
                   LEFT JOIN document_chunks ch ON ch.id = lnk.chunk_id
                   WHERE lnk.requirement_id = ?""",
                (rid,),
            ).fetchone()
            if link and link[1]:
                chunk_doc, chunk_sec = link
                sec_id = sh_lookup.get((chunk_doc, chunk_sec.lower()))
                if not sec_id:
                    m = re.match(r"^(\d+(?:\.\d+)*)", chunk_sec.lower())
                    if m:
                        sec_id = sh_lookup.get((chunk_doc, m.group(1)))
                if not sec_id:
                    sec_id = sh_global_by_code.get(chunk_sec.lower())
        if sec_id:
            cur.execute(
                "UPDATE rfp_requirements SET primary_section_id=? WHERE id=?",
                (sec_id, rid),
            )
            pinned += 1

    con.commit()

    print()
    print("  Theme distribution:")
    for tid, label in cur.execute(
        "SELECT id, label FROM requirement_themes ORDER BY ord"
    ):
        n = by_theme.get(tid, 0)
        print(f"    {label:40s} {n:6d}")

    print()
    stat("Requirements pinned to a section node", pinned, len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
