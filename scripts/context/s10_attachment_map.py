"""Stage 10 — Attachment / Exhibit / Schedule label → document map.

When an RFP says "submit Attachment 2 with the Quote", the citation
resolver currently can only resolve "Attachment 2" if some section in
``section_hierarchy`` happens to have section_code "Attachment 2". For
documents that ARE attachments themselves (e.g. ingested as separate
files), there's no resolution path. This stage builds a label →
document_id map by parsing each ingested document's filename for
attachment-style labels and persisting an alias table.

Output:
  document_aliases(
    id, document_id, alias TEXT NOT NULL, alias_kind TEXT,
    alias_normalized TEXT
  )

Then re-runs the reference resolver's section step is unnecessary — but
we update s05's logic via a follow-up stage that consults this table.
For now, the API can join requirement_references → document_aliases on
ref_normalized = alias_normalized to surface the document.
"""
from __future__ import annotations

import re
import sys
from typing import List, Tuple

from .common import banner, connect, stat


# Filename patterns we recognize as carrying an attachment-style label.
LABEL_PATTERNS = [
    # "Attachment 2 - Standard Procurement Forms Packet"
    (re.compile(r"\bAttachment\s+([A-Z0-9][A-Za-z0-9.\-]*)\b", re.I), "attachment"),
    # "Appendix 3 - Inspection Item 2704"
    (re.compile(r"\bAppendix\s+([A-Z0-9][A-Za-z0-9.\-]*)\b", re.I), "appendix"),
    # "Exhibit B - Insurance"
    (re.compile(r"\bExhibit\s+([A-Z0-9][A-Za-z0-9.\-]*)\b", re.I), "exhibit"),
    # "Schedule 1: Service Levels"
    (re.compile(r"\bSchedule\s+([A-Z0-9][A-Za-z0-9.\-]*)\b", re.I), "schedule"),
    # "Form OD-1"
    (re.compile(r"\bForm\s+([A-Z0-9][A-Za-z0-9.\-]+)\b", re.I), "form"),
    # "Amendment 6"
    (re.compile(r"\bAmendment\s+(\d+[A-Za-z0-9.\-]*)\b", re.I), "amendment"),
]


def labels_from_name(name: str) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    if not name:
        return out
    for pat, kind in LABEL_PATTERNS:
        for m in pat.finditer(name):
            num = m.group(1).strip()
            label = f"{kind.capitalize()} {num}" if kind != "form" else f"Form {num}"
            out.append((label, kind))
    return out


def normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def main() -> int:
    con = connect()
    cur = con.cursor()
    banner("STAGE 10 — Attachment / document alias map")

    cur.executescript(
        """
        DROP TABLE IF EXISTS document_aliases;
        CREATE TABLE document_aliases (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id     INTEGER NOT NULL REFERENCES ingested_documents(id),
            alias           TEXT NOT NULL,
            alias_kind      TEXT NOT NULL,
            alias_normalized TEXT NOT NULL,
            UNIQUE (alias_normalized, document_id)
        );
        CREATE INDEX idx_da_norm ON document_aliases(alias_normalized);
        CREATE INDEX idx_da_doc ON document_aliases(document_id);
        """
    )

    rows = cur.execute(
        "SELECT id, original_filename, filename FROM ingested_documents"
    ).fetchall()
    stat("Documents", len(rows))

    inserted = 0
    by_kind = {}
    for did, orig, fname in rows:
        seen = set()
        for nm in (orig, fname):
            for label, kind in labels_from_name(nm or ""):
                norm = normalize(label)
                if (norm, did) in seen:
                    continue
                seen.add((norm, did))
                cur.execute(
                    """INSERT OR IGNORE INTO document_aliases
                       (document_id, alias, alias_kind, alias_normalized)
                       VALUES (?, ?, ?, ?)""",
                    (did, label, kind, norm),
                )
                inserted += cur.rowcount
                by_kind[kind] = by_kind.get(kind, 0) + 1
    con.commit()

    print()
    stat("Aliases persisted", inserted)
    for k, n in sorted(by_kind.items(), key=lambda x: -x[1]):
        print(f"    {k:15s} {n}")
    print()
    print("  Sample:")
    for r in cur.execute(
        """SELECT da.alias, ind.original_filename FROM document_aliases da
           JOIN ingested_documents ind ON ind.id = da.document_id LIMIT 8"""
    ):
        print(f"    {r[0]!r}  →  {r[1]}")

    # Now upgrade requirement_references: for any unresolved ref whose
    # ref_normalized matches an alias, attach the target document.
    upd = cur.execute(
        """SELECT rr.id, rr.ref_normalized
           FROM requirement_references rr
           WHERE rr.target_kind = 'unresolved'
             AND rr.ref_kind IN ('attachment','appendix','exhibit','schedule','form')"""
    ).fetchall()
    upgraded = 0
    for rr_id, ref_norm in upd:
        norm = normalize(ref_norm)
        hit = cur.execute(
            "SELECT document_id FROM document_aliases WHERE alias_normalized=? LIMIT 1",
            (norm,),
        ).fetchone()
        if hit:
            cur.execute(
                """UPDATE requirement_references
                   SET target_kind='document', target_document_id=?, confidence=0.8
                   WHERE id=?""",
                (hit[0], rr_id),
            )
            upgraded += 1
    con.commit()
    stat("Unresolved refs upgraded via alias", upgraded)
    return 0


if __name__ == "__main__":
    sys.exit(main())
