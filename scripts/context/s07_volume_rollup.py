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
        "section_prefixes": ["3.2", "3.4", "3.5", "4.20", "4.21", "4.22",
                              "3.x", "4.4",
                              "Appendix 3",
                              "exceptionParms",
                              "Inspection.",
                              "Vehicle.",
                              "Emissions.",
                              "OBD",
                              "rules.",
                              "rule.",
                              "result.",
                              "BeginTestSelection",
                              "EndTest",
                              "DataElement",
                              "DTCCode",
                              "TestSelection",
                              "VIR"],
        "section_keywords": ["software", "ngvid", "ngsystem", "architecture",
                              "platform", "system design", "vehicle data",
                              "data table", "transmission of files",
                              "internal tracking", "rules engine",
                              "data migration", "audit data", "vid",
                              "appendix 3 - aim", "appendix 3 -",
                              "user management", "audit administration",
                              "xml schema", "data element", "specification",
                              "constraint", "rule", "exception",
                              "rules engine"],
        "title_keywords": ["software", "architecture", "platform",
                            "rules engine", "vin decoder", "ngvid", "ngsystem",
                            "ngworkstation", "data migration", "ngv-",
                            "interface", "integration", "ngworkstation",
                            "connect direct", "ftps", "xml schema", "jad",
                            "user management", "audit administration",
                            "data element", "constraint", "field",
                            "exemption", "inspection.", "rules engine"],
    },
    {
        "label": "Inspection & Test Operations",
        "slug": "inspection-operations",
        "section_prefixes": ["3.3", "3.6", "3.7", "3.8", "3.10", "3.11", "3.12",
                              "3.13", "3.14", "3.15", "3.16", "3.17",
                              "Appendix 3.1", "Appendix 3.2", "Appendix 3.3",
                              "Appendix 3.4",
                              "Begin Test Selection", "Inspection",
                              "Vehicle Inspection"],
        "section_keywords": ["inspection", "test", "calibration", "lift",
                              "obd", "emission", "vehicle", "smog", "tampering",
                              "diesel", "i/m"],
        "title_keywords": ["inspection", "test", "calibration", "lift", "obd",
                            "emission", "tampering", "i/m", "diesel"],
    },
    {
        "label": "Service Levels & Performance",
        "slug": "service-levels",
        "section_prefixes": ["3.6.1", "3.39"],
        "section_keywords": ["sla", "service level", "performance test",
                              "wait time", "uptime", "operational phase defect",
                              "defect correction"],
        "title_keywords": ["service level", "uptime", "wait time",
                            "performance test", "downtime", "stress test",
                            "liquidated damages", "defect", "severity",
                            "throughput", "response time"],
    },
    {
        "label": "Reporting, Data & Analytics",
        "slug": "reporting",
        "section_prefixes": ["Appendix 3.2A", "Appendix 3.4F", "5.11"],
        "section_keywords": ["report", "analytics", "dashboard", "monitoring",
                              "data table", "audit data", "ngvid reporting",
                              "data warehouse"],
        "title_keywords": ["report", "dashboard", "analytic", "monitor",
                            "log", "audit log", "kpi", "metrics", "ngv-ods"],
    },
    {
        "label": "Facilities, Buildings & Grounds",
        "slug": "facilities",
        "section_prefixes": ["Appendix 3.2H", "4.16",
                              "Appendix F",
                              "Installation"],
        "section_keywords": ["facility", "building", "grounds", "maintenance",
                              "shared site", "site utilities",
                              "installation"],
        "title_keywords": ["facility", "building", "grounds",
                            "janitorial", "snow", "landscape", "vacuum",
                            "sweep", "carpets", "cleaning", "restroom",
                            "utility", "utilities", "roller assembl",
                            "hex bolts", "lift plate", "pumpkin"],
    },
    {
        "label": "Personnel, Staffing & Training",
        "slug": "personnel",
        "section_prefixes": ["3.21", "3.22", "Resumes",
                              "Experience with Contracts"],
        "section_keywords": ["staff", "training", "personnel", "key person",
                              "resume", "experience with contracts",
                              "past performance", "subcontractor utilization"],
        "title_keywords": ["staff", "training", "key person", "supervisor",
                            "background check", "drug test", "uniform",
                            "resume", "past performance", "experience",
                            "p.o.s", "subcontractor"],
    },
    {
        "label": "Pricing & Financial",
        "slug": "pricing",
        "section_prefixes": ["5", "Price", "6.x Price"],
        "section_keywords": ["price", "pricing", "cost", "rate", "fee",
                              "no charge"],
        "title_keywords": ["price", "pricing", "cost", "rate", "fee",
                            "escalation", "invoic", "no charge"],
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
        "section_prefixes": ["3.13", "Form",
                              "Subcontracting Set-Aside",
                              "Small Business and/or DVB Subcontracting",
                              "Business Registration",
                              "Director's Right",
                              "Bid Solicitation Checklist",
                              "Technical Quote",
                              "Quote Preparation",
                              "Quote Delivery",
                              "Quote Preparation - General",
                              "Quote Preparation and Submission",
                              "NJSTART Submission",
                              "Clarification of Quote",
                              "Cover",
                              "2.4"],
        "section_keywords": ["form", "ownership disclosure", "set-asides",
                              "set-aside",
                              "offer and acceptance",
                              "business registration", "brc",
                              "subcontracting", "small business", "dvb",
                              "director's right", "quote acceptance",
                              "macbride", "ada",
                              "technical quote", "quote preparation",
                              "quote delivery", "quote submission",
                              "njstart submission", "njstart",
                              "clarification of quote", "site visit",
                              "mandatory site visit", "cover sheet",
                              "redacted", "opra"],
        "title_keywords": ["form", "ownership disclosure", "submit",
                            "macbride", "ada compliance",
                            "business registration", "brc",
                            "set-aside", "subcontract", "dvb",
                            "small business", "director may", "director's",
                            "quote", "njstart", "site visit", "redacted",
                            "opra", "deadline", "due date"],
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
        "section_prefixes": ["3.41", "Identity and Authentication",
                              "Media Protection"],
        "section_keywords": ["security", "privacy", "data protection",
                              "confidential", "identity and authentication",
                              "access control", "encryption",
                              "media protection", "records retention",
                              "fraud detection"],
        "title_keywords": ["security", "encrypt", "access control",
                            "audit log", "data protection", "privacy",
                            "authentication", "identity",
                            "media storage", "records retention",
                            "watch list", "fraud"],
    },
    {
        "label": "Business Continuity & Disaster Recovery",
        "slug": "bcdr",
        "section_prefixes": ["Business Continuity",
                              "System Backup",
                              "Disaster Recovery"],
        "section_keywords": ["business continuity", "disaster recovery",
                              "system backup", "backup and recovery",
                              "bc/dr", "bcp", "drp"],
        "title_keywords": ["business continuity", "disaster recovery",
                            "backup", "bc/dr", "bcp", "drp", "rto", "rpo"],
    },
    {
        "label": "Quality Assurance",
        "slug": "quality",
        "section_prefixes": ["Quality Management",
                              "Quality Assurance",
                              "Quality Control"],
        "section_keywords": ["quality management", "quality assurance",
                              "quality control", "qa plan", "qc", "qa/qc"],
        "title_keywords": ["qa plan", "qa/qc", "quality assurance",
                            "quality control", "fraud mitigation"],
    },
    {
        "label": "Customer & Public Interface",
        "slug": "customer-interface",
        "section_prefixes": ["Call Center",
                              "Motorist Hotline",
                              "Motorist Appointment",
                              "Motorist VIR",
                              "Public Webpages",
                              "Public Web Interface"],
        "section_keywords": ["call center", "motorist hotline",
                              "motorist appointment",
                              "motorist vir",
                              "vir reprint",
                              "public web", "public webpage",
                              "customer service", "hotline"],
        "title_keywords": ["call center", "motorist hotline", "voicemail",
                            "motorist appointment", "vir reprint",
                            "public web", "public webpage",
                            "toll-free", "customer service",
                            "erf repair", "ert repair", "reservation"],
    },
    {
        "label": "Project & Implementation Management",
        "slug": "implementation",
        "section_prefixes": ["3.19", "4.1", "4.2", "4.34",
                              "Project Description",
                              "Project Management",
                              "Project Descriptions and Requirements",
                              "Document Repository",
                              "Project Change Management",
                              "Change Management",
                              "Mobilization Plan",
                              "Deliverable Review",
                              "State Contract Manager",
                              "Organization Charts",
                              "Location",
                              "News Releases",
                              "Extranet Plan",
                              "Data Conversion and Migration",
                              "Participant"],
        "section_keywords": ["project description", "project management",
                              "implementation phase", "implementation plan",
                              "transition plan", "transition", "kickoff",
                              "document repository", "program documentation",
                              "deliver complete", "implementation",
                              "change management", "mobilization",
                              "deliverable review", "scm",
                              "state contract manager", "organization chart",
                              "news release", "extranet"],
        "title_keywords": ["implementation phase", "transition", "kickoff",
                            "project management plan",
                            "transition plan", "deliver complete",
                            "submit draft", "draft transition",
                            "document repository", "program documentation",
                            "change management", "mobilization",
                            "scm", "organization chart", "news release",
                            "extranet"],
    },
    {
        "label": "Standard Terms & Conditions",
        "slug": "standard-tcs",
        "doc_ids": [9],           # Combined NJ Standard T&Cs
        "section_prefixes": ["1.", "2.", "Standard Terms"],
        "section_keywords": ["indemnification", "warranty",
                              "termination", "applicable law",
                              "anti-discrimination", "prevailing wage",
                              "cooperative purchasing", "contract amount",
                              "blanket p.o. specific definitions",
                              "sstc exceptions", "purpose, intent"],
        "title_keywords": ["indemnif", "applicable law", "termination",
                            "antitrust", "anti-discrimination",
                            "prevailing wage", "cooperative purchasing",
                            "estimated contract amount",
                            "intrastate cooperative",
                            "purpose, intent", "sstc exception"],
    },
    {
        "label": "Collective Bargaining & Labor",
        "slug": "labor",
        "doc_ids": [10],
    },
    {
        "label": "Legal & Regulatory Citations",
        "slug": "legal-regulatory",
        "section_prefixes": ["Section 1 (C.39", "Section 2 (",
                              "Section 3 (", "Section 4 ("],
        "section_keywords": ["n.j.s.a", "n.j.a.c", "u.s.c", "c.f.r",
                              "c.39:8", "c. 39:8", "p.l. 20",
                              "section 1 (c.39"],
        "title_keywords": ["statute", "regulation", "court", "treasurer",
                            "n.j.s.", "n.j.a.", "c.f.r", "u.s.c"],
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


# Top-level discipline category each theme rolls into. Drives the
# high-level filter shown first in the browser ("show only Technical") so
# the user can scope the tree without scrolling through 17 themes.
THEME_CATEGORY_MAP: dict[str, str] = {
    "solution-architecture":  "Technical",
    "inspection-operations":  "Operational",
    "service-levels":         "Operational",
    "reporting":              "Technical",
    "facilities":             "Operational",
    "personnel":              "Operational",
    "pricing":                "Commercial",
    "service-schedule":       "Operational",
    "compliance-forms":       "Compliance",
    "insurance":              "Commercial",
    "info-sec":               "Technical",
    "bcdr":                   "Technical",
    "quality":                "Operational",
    "customer-interface":     "Operational",
    "implementation":         "Operational",
    "standard-tcs":           "Compliance",
    "labor":                  "Operational",
    "legal-regulatory":       "Compliance",
    "amendments":             "Other",
    "pre-quote":              "Other",
    "uncategorized":          "Other",
}

CATEGORY_ORDER = ["Technical", "Operational", "Commercial", "Compliance", "Other"]


def assign_theme(
    section_code: Optional[str],
    title: Optional[str],
    description: Optional[str],
    source_text: Optional[str],
    document_id: int,
    filename: Optional[str],
) -> Tuple[int, float]:
    """Return (theme_index, confidence)."""
    sc_raw = (section_code or "")
    sc = sc_raw.lower()
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

    # Last-ditch shape heuristics for technical XML/data-element style
    # section_ids the keyword rules don't catch (e.g. "SetCatDTCCode_Final",
    # "exceptionParms.rPMExclusion", "TamperCheck"). These are always
    # technical rule/data-element specs from the XML schema in Appendix 3,
    # so they belong with Solution Architecture & Software.
    is_camel_id = (
        sc_raw and len(sc_raw) >= 4
        and re.match(r"^[A-Za-z][A-Za-z0-9_.]*$", sc_raw)
        and sum(1 for c in sc_raw if c.isupper()) >= 2
        and " " not in sc_raw
    )
    if is_camel_id:
        for idx, theme in enumerate(THEMES):
            if theme["slug"] == "solution-architecture":
                return idx, 0.5

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
            category    TEXT,
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
            "INSERT INTO requirement_themes (slug, label, category, ord) VALUES (?, ?, ?, ?)",
            (theme["slug"], theme["label"],
             THEME_CATEGORY_MAP.get(theme["slug"], "Other"), ord_),
        )
        theme_ids.append(cur.lastrowid)
    cur.execute(
        "INSERT INTO requirement_themes (slug, label, category, ord) VALUES (?, ?, ?, ?)",
        ("uncategorized", "Uncategorized",
         THEME_CATEGORY_MAP.get("uncategorized", "Other"), len(THEMES) + 1),
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
