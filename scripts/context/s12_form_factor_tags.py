"""Stage 12 — Form-factor tagging for fit-aware synthesis.

The user's quality bar: "If the RFP wants a tablet solution, workstation
content from a prior response must be filtered out and ignored."

This stage tags every requirement and every Parsons content chunk with a
``form_factor`` label drawn from a small fixed taxonomy. The synthesis
drafter should restrict the candidate pool to chunks whose form_factor is
``null`` (general) or matches the requirement's form_factor.

The tagger is deterministic — keyword voting against curated phrase lists.
We don't need it to be perfect; we need it to keep "workstation
implementation" content out of "tablet provisioning" responses.

Adds columns:
  rfp_requirements.form_factor TEXT
  document_chunks.form_factor TEXT          (only meaningful on Parsons docs)

The taxonomy (mutually-exclusive):
  workstation   — fixed lane / counter inspection workstation, NGWorkstation
  tablet        — handheld / iPad / mobile-tablet inspection tools
  mobile-lane   — MIT / mobile inspection team, roving on-site units
  cif           — centralized inspection facility (state-run)
  pif           — private inspection facility (vendor-licensed shops)
  reporting     — analytics / dashboards / data warehouse
  facilities    — buildings, grounds, janitorial, snow removal
  forms         — compliance attachments (Ownership Disclosure, MacBride…)
  legal         — Standard T&Cs, statutory citations
  staffing      — personnel, training, key persons
  pricing       — price sheets, escalations
  general       — fallback / cross-cutting (the value used in DB is NULL)
"""
from __future__ import annotations

import re
import sys
from typing import List, Optional, Tuple

from .common import banner, connect, stat


# Each row: (label, regex). First match wins.
TAGS: List[Tuple[str, re.Pattern]] = [
    ("tablet",       re.compile(r"\b(tablet|hand[-\s]?held|ipad|android\s+tablet|portable\s+inspection\s+device)\b", re.I)),
    ("mobile-lane",  re.compile(r"\bmobile\s+inspection\s+(team|lane|facility)|\bMITs?\b|roving\s+inspection|fleet\s+inspection", re.I)),
    ("cif",          re.compile(r"\b(centraliz[ed]+\s+inspection\s+facilit|CIFs?)\b", re.I)),
    ("pif",          re.compile(r"\b(private\s+inspection\s+facilit|PIFs?|repair\s+facilit)\b", re.I)),
    ("workstation",  re.compile(r"\b(NG\s*workstation|inspection\s+workstation|lane\s+workstation|equipment\s+terminal)\b", re.I)),
    ("reporting",    re.compile(r"\b(dashboard|analytics?|business\s+intelligence|management\s+console|reporting\s+(structure|database|module)|NGV-?ODS|reports?\s+repository)\b", re.I)),
    ("facilities",   re.compile(r"\b(grounds\s+maintenance|janitorial|snow\s+removal|building\s+maintenance|HVAC|landscape)\b", re.I)),
    ("forms",        re.compile(r"\b(ownership\s+disclosure|macbride|EEO\s+certification|disabled\s+veteran|set-?aside|bid\s+amendment)\b", re.I)),
    ("legal",        re.compile(r"\b(N\.?J\.?S\.?A\.?|N\.?J\.?A\.?C\.?|C\.?F\.?R\.?|U\.?S\.?C\.?|standard\s+terms\s+and\s+conditions|indemnif|liability)\b", re.I)),
    ("staffing",     re.compile(r"\b(staffing\s+plan|key\s+personnel|background\s+check|drug\s+test|workforce|union|collective\s+bargain|MAC\s+Technician)\b", re.I)),
    ("pricing",      re.compile(r"\b(price\s+sheet|pricing\s+(model|scenario|escalat)|cost\s+escalation|invoice|fee\s+structure|per[-\s]inspection\s+rate|liquidated\s+damages)\b", re.I)),
    ("performance",  re.compile(r"\b(service\s+level|SLA|wait\s+time|uptime|downtime|performance\s+test|stress\s+test|throughput)\b", re.I)),
]


def tag_text(text: str) -> Optional[str]:
    if not text:
        return None
    for label, rx in TAGS:
        if rx.search(text):
            return label
    return None


def ensure_column(cur, table: str, col: str, decl: str) -> None:
    cols = {row[1] for row in cur.execute(f"PRAGMA table_info('{table}')")}
    if col not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def main() -> int:
    con = connect()
    cur = con.cursor()
    banner("STAGE 12 — Form-factor tagging (fit-aware synthesis)")

    ensure_column(cur, "rfp_requirements", "form_factor", "TEXT")
    ensure_column(cur, "document_chunks", "form_factor", "TEXT")
    cur.execute("UPDATE rfp_requirements SET form_factor = NULL")
    cur.execute("UPDATE document_chunks SET form_factor = NULL")
    con.commit()

    # Tag requirements (combine title + description + source_text)
    sel = con.cursor()
    by_tag: dict = {}
    n_req_tagged = 0
    for rid, title, desc, src in sel.execute(
        """SELECT id, title, description, source_text FROM rfp_requirements
           WHERE superseded_by_requirement_id IS NULL"""
    ):
        text = " ".join(filter(None, [title, desc, src]))
        tag = tag_text(text)
        if tag:
            cur.execute("UPDATE rfp_requirements SET form_factor=? WHERE id=?", (tag, rid))
            by_tag[tag] = by_tag.get(tag, 0) + 1
            n_req_tagged += 1
    con.commit()

    print()
    stat("Requirements tagged", n_req_tagged)
    for label, _ in TAGS:
        print(f"  {label:14s} {by_tag.get(label, 0)}")

    # Tag Parsons content chunks
    by_tag2: dict = {}
    n_chunk_tagged = 0
    for cid, content in sel.execute(
        """SELECT dc.id, dc.content
           FROM document_chunks dc
           JOIN ingested_documents ind ON ind.id = dc.document_id
           WHERE ind.source_type = 'parsons' AND dc.content IS NOT NULL"""
    ):
        tag = tag_text(content)
        if tag:
            cur.execute("UPDATE document_chunks SET form_factor=? WHERE id=?", (tag, cid))
            by_tag2[tag] = by_tag2.get(tag, 0) + 1
            n_chunk_tagged += 1
    con.commit()

    print()
    stat("Parsons chunks tagged", n_chunk_tagged)
    for label, _ in TAGS:
        print(f"  {label:14s} {by_tag2.get(label, 0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
