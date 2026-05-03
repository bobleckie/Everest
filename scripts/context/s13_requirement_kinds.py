"""Stage 13 — Tag every requirement with a row-kind.

The extractor was over-permissive: any "shall / must / will" sentence
became a requirement row, even when the underlying text is an XML
schema field constraint, an inspection-checklist line item, or a
boilerplate cross-reference. The user-facing "requirements" list
should only show genuine obligations; specs and checklist items
should be queryable as supporting detail, not at the same grain as
"submit Quote by 2:00 PM".

We do NOT delete or alter any row. We add a single column
``rfp_requirements.requirement_kind`` whose values are:

  obligation         — a real shall/must/will obligation in narrative
                       form. The default. Visible in the requirement
                       browser.
  checklist_item     — one row from an Appendix 3 inspection-checklist
                       table. Real but very granular. Hidden from the
                       default list — surfaces only inside its parent
                       obligation's detail view.
  data_element_spec  — XML schema field / rule constraint
                       (`error is output only`, `Run SetRpmExclusion
                       only if rPMExclusion is not Y`). Not actually
                       an obligation. Hidden by default; visible in a
                       dedicated "spec reference" tab.
  reference_only     — Empty / heading-echo / cross-reference stub.
                       Hidden by default.

Verbatim source text is preserved verbatim: nothing in
``title / description / source_text / requirement_id / section_id``
changes. The bundle and source-link tables continue to give every
hidden row the same one-hop chunk lookup.

Re-runnable: clears the column before re-classifying.
"""
from __future__ import annotations

import re
import sys
from collections import defaultdict
from typing import Optional

from .common import banner, connect, stat


def ensure_column(cur, table: str, col: str, decl: str) -> None:
    cols = {row[1] for row in cur.execute(f"PRAGMA table_info('{table}')")}
    if col not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def classify_kind(section_id: Optional[str], title: Optional[str],
                  description: Optional[str], source_text: Optional[str]) -> str:
    sec = (section_id or "").strip()
    title = (title or "").strip()
    desc = (description or "").strip()
    src = (source_text or "").strip()
    blob = f"{title}\n{desc}\n{src}".lower()

    # ── data_element_spec ──────────────────────────────────────────────
    # camelCase / dotted-camel section_ids that look like XML element
    # paths or rule names. These are schema-level descriptions, not
    # obligations.
    if sec and " " not in sec:
        # camelCase, multi-cap, ASCII identifier shape
        if re.match(r"^[A-Za-z][A-Za-z0-9_.]*$", sec):
            n_upper = sum(1 for c in sec if c.isupper())
            n_dot = sec.count(".")
            if n_upper >= 2 or n_dot >= 1:
                return "data_element_spec"
    # Source/desc patterns: schema-style "X is output only" / "X used only by rules"
    if re.search(r"\b(is\s+output\s+only|used\s+only\s+by\s+rules?)\b", blob):
        return "data_element_spec"
    # "Run <RuleName> only when ..." — typical rule-trigger spec
    if re.search(r"^run\s+[a-z][a-zA-Z0-9_]*\s+only\b", title.lower()):
        return "data_element_spec"

    # ── checklist_item ─────────────────────────────────────────────────
    # Section_id formatted as "<digits>" or "<digits> / <letter><digits>" or
    # "Appendix 3 - Inspection Item NNN" — Appendix-3 inspection codes.
    if re.match(r"^\d{3,4}(\s*[/|]\s*[A-Z]\d+)?\s*$", sec):
        return "checklist_item"
    if re.search(r"^appendix\s+3\s*-\s*inspection\s+item", sec, re.I):
        return "checklist_item"
    # Source text starts with a numeric code + pipe (table-row flatten):
    # "3684 | Air Chamber: LS T‐30 ‐ 2\" Max | S | B33 | 709"
    if re.match(r"^\s*\d{3,4}\s*\|\s*", src):
        return "checklist_item"
    # Description with "out-of-service" + numeric content in the title is
    # almost always a checklist item (the spec "X must not exceed Y").
    if ("out-of-service" in blob or "out of service" in blob) and re.search(r"\d", title):
        return "checklist_item"

    # ── reference_only ─────────────────────────────────────────────────
    # Empty or near-empty content
    if not src and not desc:
        return "reference_only"
    if len(src) < 25 and title.lower() == desc.lower() and len(title) < 40:
        return "reference_only"

    # Default
    return "obligation"


def main() -> int:
    con = connect()
    cur = con.cursor()
    banner("STAGE 13 — Requirement-kind tagging")

    ensure_column(cur, "rfp_requirements", "requirement_kind", "TEXT")
    cur.execute("UPDATE rfp_requirements SET requirement_kind = NULL")
    con.commit()

    rows = cur.execute(
        """SELECT id, section_id, title, description, source_text
           FROM rfp_requirements"""
    ).fetchall()
    stat("Total requirements scanned", len(rows))

    counts = defaultdict(int)
    for rid, sec, title, desc, src in rows:
        kind = classify_kind(sec, title, desc, src)
        cur.execute(
            "UPDATE rfp_requirements SET requirement_kind=? WHERE id=?",
            (kind, rid),
        )
        counts[kind] += 1
    con.commit()

    print()
    for k in ("obligation", "checklist_item", "data_element_spec", "reference_only"):
        n = counts.get(k, 0)
        pct = 100 * n / len(rows) if rows else 0
        print(f"  {k:25s} {n:6,}  ({pct:5.1f}%)")

    # Same breakdown but only across the visible (non-superseded, non-child)
    # set the user actually sees.
    visible = cur.execute(
        """SELECT requirement_kind, COUNT(*) FROM rfp_requirements
           WHERE superseded_by_requirement_id IS NULL
             AND (rollup_role IS NULL OR rollup_role='parent')
           GROUP BY requirement_kind"""
    ).fetchall()
    print()
    print("  Visible (parent + standalone) breakdown:")
    total_visible = sum(n for _, n in visible)
    for k in ("obligation", "checklist_item", "data_element_spec", "reference_only"):
        n = next((n for k2, n in visible if k2 == k), 0)
        pct = 100 * n / total_visible if total_visible else 0
        print(f"    {k:23s} {n:6,}  ({pct:5.1f}%)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
