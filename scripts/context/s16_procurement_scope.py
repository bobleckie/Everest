"""Stage 16 — Tag each ingested document with its procurement scope.

The DB has 34 RFP-package documents that span multiple procurement
cycles. Only the ~9 docs incorporated by the live 2026 solicitation are
the actual RESPONSE scope. The rest (prior 2021 master, all the
Amendment 1–19 files from the 2021 round, the consolidated baseline
synthetic doc) are comparison material — useful for redlines and diff
analysis, but NOT what the bidder is responding to in 2026.

This stage adds:
  ingested_documents.procurement_scope TEXT
    'current_2026'         — incorporated by reference into the live RFP.
    'archive_2021_compare' — prior procurement cycle, kept for comparison.
    'parsons_knowledge'    — Parsons internal knowledge (already source_type='parsons').
    'reference'            — embed_test fixtures and similar.
    'amendment_2026'       — amendments to the live 2026 solicitation
                             (none yet on this corpus, but reserved).

Default UI views and the synthesis pipeline restrict to current_2026.
The user can still flip a toggle to bring in the archive set when doing
a redline / diff comparison.

Re-runnable: clears the column before re-tagging.
"""
from __future__ import annotations

import re
import sys
from typing import Optional

from .common import banner, connect, stat


def ensure_column(cur, table: str, col: str, decl: str) -> None:
    cols = {row[1] for row in cur.execute(f"PRAGMA table_info('{table}')")}
    if col not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


# Explicit per-doc scoping rules — file-name-based since we can't ask the
# user to remember which doc was uploaded for which purpose.
ARCHIVE_PATTERNS = [
    re.compile(r"\bRevised\b.*\b2021\b", re.I),
    re.compile(r"\b03\.01\.2021\b"),
    re.compile(r"\b04\.26\.2021\b"),
    re.compile(r"\b04\.06\.2021\b"),
    re.compile(r"\b06\.04\.2021\b"),
    re.compile(r"\bAmendment\s+(?:1|2|3|4|5|6|7|8|9|10|11|14|15|16|17|18|19|20)\b", re.I),
    re.compile(r"\bRound\s+2QA\b", re.I),
    re.compile(r"\b082819\b"),                  # August 28 2019 procurement-form date
    re.compile(r"\bas-amended\b", re.I),         # consolidation_run synthetic doc 40
    re.compile(r"\bAppendix\s+3\.2H\s+03\.01\.2021\b", re.I),
    re.compile(r"\bRevised\s+Price\s+Sheet\b", re.I),  # both 2021 revised price sheets
]

CURRENT_2026_FILENAMES = {
    # Exact filename matches (case-insensitive, normalized whitespace).
    "T1628 Bid Solicitation 4.22.26.pdf": "current_2026",
    "T1628 Appendix 3.docx": "current_2026",  # technical appendix incorporated by ref into 2026
    "T1628 Price Sheet 4.22.2026.xlsx": "current_2026",
    "Attachment 2 - Standard Procurement Forms Packet~11.pdf": "current_2026",
    "Combined State of New Jersey Standard Terms and Conditions 6.3.2025~7.pdf": "current_2026",
    "Current Collective Bargaining Agreement.pdf": "current_2026",
    "T1628 Pre Quote Conference PowerPoint.pptx": "current_2026",
    "T1628 SoNJ Third-Party Information Security Questionnaire.pdf": "current_2026",
    "4618_R2  L2025 c359.pdf": "current_2026",                      # NJ statute incorporated 2025
}


def classify(filename: Optional[str], original_filename: Optional[str], source_type: Optional[str]) -> str:
    if source_type == "parsons":
        return "parsons_knowledge"
    if source_type and source_type not in ("rfp", "parsons"):
        return "reference"
    name = (original_filename or filename or "").strip()
    # Explicit current set first
    if name in CURRENT_2026_FILENAMES:
        return CURRENT_2026_FILENAMES[name]
    # Archive heuristics
    for pat in ARCHIVE_PATTERNS:
        if pat.search(name):
            return "archive_2021_compare"
    # Default fallback for RFP-typed docs that didn't match any rule:
    # treat as archive so the default UI doesn't include unfamiliar docs.
    # If a new 2026 amendment arrives, add an explicit rule.
    return "archive_2021_compare"


def main() -> int:
    con = connect()
    cur = con.cursor()
    banner("STAGE 16 — Procurement-scope tagging")

    ensure_column(cur, "ingested_documents", "procurement_scope", "TEXT")
    cur.execute("UPDATE ingested_documents SET procurement_scope = NULL")
    con.commit()

    rows = cur.execute(
        "SELECT id, filename, original_filename, source_type FROM ingested_documents"
    ).fetchall()
    stat("Documents to scope-tag", len(rows))

    counts = {"current_2026": 0, "archive_2021_compare": 0,
              "parsons_knowledge": 0, "reference": 0, "amendment_2026": 0}
    for did, fname, ofname, stype in rows:
        scope = classify(fname, ofname, stype)
        cur.execute(
            "UPDATE ingested_documents SET procurement_scope=? WHERE id=?",
            (scope, did),
        )
        counts[scope] = counts.get(scope, 0) + 1
    con.commit()

    print()
    for k, n in counts.items():
        print(f"  {k:25s} {n:3d}")

    # Per-scope obligation count (writeups only)
    print()
    print("Writeup obligations by procurement scope (visible top-level):")
    for r in cur.execute("""
        SELECT ind.procurement_scope, COUNT(*) AS n
        FROM rfp_requirements r
        LEFT JOIN ingested_documents ind ON ind.id = r.document_id
        WHERE r.superseded_by_requirement_id IS NULL
          AND (r.rollup_role IS NULL OR r.rollup_role = 'parent')
          AND (r.requirement_kind = 'obligation' OR r.requirement_kind IS NULL)
          AND (r.response_effort = 'writeup' OR r.response_effort IS NULL)
        GROUP BY ind.procurement_scope
        ORDER BY n DESC
    """):
        print(f"  {(r[0] or '(no doc)'):25s} {r[1]:6,}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
