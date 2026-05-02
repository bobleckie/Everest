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

    # Build section lookup: (doc_id, lower-section_code) -> sh.id
    sh_lookup: dict[Tuple[int, str], int] = {}
    for sid, did, code in cur.execute(
        "SELECT id, document_id, section_code FROM section_hierarchy"
    ):
        sh_lookup[(did, code.lower())] = sid

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

        # Pin to a section_hierarchy node
        sec_id = None
        if scode:
            sec_id = sh_lookup.get((did, scode.lower()))
            if not sec_id:
                # Try cross-doc match
                for (d, c), sid_ in sh_lookup.items():
                    if c == scode.lower():
                        sec_id = sid_
                        break
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
