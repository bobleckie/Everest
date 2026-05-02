"""Stage 5 — Reference extraction and resolution.

For every requirement, scan its source_text + description for cross-references
and resolve each one to a concrete target. Persist so the UI doesn't recompute.

Output:
  requirement_references(
    id INTEGER PK,
    requirement_id INTEGER NOT NULL,
    ref_kind TEXT,              -- 'section' | 'appendix' | 'attachment' |
                                -- 'exhibit' | 'schedule' | 'form' |
                                -- 'regulatory' | 'see-internal' | 'self'
    ref_label TEXT,             -- the literal extracted text "Section 3.2.1"
    ref_normalized TEXT,        -- "3.2.1" / "Appendix C" / "N.J.S.A. 39:8-44"
    target_kind TEXT,           -- 'requirement' | 'section' | 'document' | 'table' | 'unresolved'
    target_requirement_id INTEGER,
    target_section_id INTEGER,  -- FK to section_hierarchy.id
    target_document_id INTEGER, -- FK to ingested_documents.id
    target_table_id INTEGER,    -- FK to extracted_tables.id
    target_excerpt TEXT,        -- short preview text for hover tooltips
    confidence REAL             -- 0..1
  )

Resolution strategy
-------------------
Section refs ("Section 4.2.1", "§ 3.2"):
  1. Look in section_hierarchy for an exact section_code in any document
     of the same proposal. Prefer the document the requirement came from.
  2. If not found, prefix-match (3.2 → 3.2.1, 3.2.2 — surface all matches).

Appendix / Attachment / Exhibit / Schedule / Form refs:
  1. Match against section_hierarchy section_code (e.g. "Appendix 3.2H").
  2. Match against ingested_documents.original_filename / a few aliases
     (e.g. "Attachment 2" → docs whose filename starts with "Attachment 2").
  3. Match against extracted_tables.title.

Regulatory citations (N.J.S.A. 39:8-44, 49 C.F.R. 51, 18 U.S.C. § 1342):
  Stored as ref_kind='regulatory' with normalized form. Targets are not
  resolved to internal documents (external authority).

Implicit "see Section X" / "as set forth in Y": treated as section/appendix
refs depending on the noun.

"the foregoing" / "above-defined": ref_kind='self' with target_requirement_id
pointing at the predecessor requirement (requires position context — for
now we just record the marker; the bundle stage will surface predecessor).

Re-runnable: drops + recreates table.
"""
from __future__ import annotations

import re
import sys
from typing import Dict, List, Optional, Tuple

from .common import banner, connect, norm_alnum, norm_ws, stat


# Capture patterns. Each yields (kind, normalized_label, raw_match)
PATTERNS = [
    # Section refs
    ("section", re.compile(r"\bSection\s+(\d+(?:\.\d+)*)\b", re.I)),
    ("section", re.compile(r"§\s*(\d+(?:\.\d+)*)\b")),
    # Appendix
    ("appendix", re.compile(r"\bAppendix\s+([A-Z0-9][A-Za-z0-9.\-]*)", re.I)),
    # Attachment
    ("attachment", re.compile(r"\bAttachment\s+([A-Z0-9][A-Za-z0-9.\-]*)", re.I)),
    # Exhibit
    ("exhibit", re.compile(r"\bExhibit\s+([A-Z0-9][A-Za-z0-9.\-]*)", re.I)),
    # Schedule
    ("schedule", re.compile(r"\bSchedule\s+([A-Z0-9][A-Za-z0-9.\-]*)", re.I)),
    # Form
    ("form", re.compile(r"\bForm\s+([A-Z0-9][A-Za-z0-9.\-]+)", re.I)),
    # Regulatory
    ("regulatory", re.compile(r"\bN\.J\.S\.A\.\s+([0-9A-Z:.\-]+)")),
    ("regulatory", re.compile(r"\bN\.J\.A\.C\.\s+([0-9A-Z:.\-]+)")),
    ("regulatory", re.compile(r"\b(\d+\s+C\.F\.R\.\s+[0-9A-Za-z.\-]+)")),
    ("regulatory", re.compile(r"\b(\d+\s+U\.S\.C\.\s+§?\s*[0-9A-Za-z.\-]+)")),
    # "see Section X.Y / Attachment N / Appendix N"
    ("see-internal", re.compile(r"\b[Ss]ee\s+(Section\s+\d+(?:\.\d+)*|Attachment\s+[A-Z0-9]+|Appendix\s+[A-Z0-9]+|Exhibit\s+[A-Z0-9]+)")),
    # "the foregoing" / "above-defined" — markers, not specific refs
    ("self", re.compile(r"\b(the\s+foregoing|above[\-\s]defined|aforementioned)\b", re.I)),
    # "in accordance with" + nominal
    ("see-internal", re.compile(r"\bin\s+accordance\s+with\s+(Section\s+\d+(?:\.\d+)*|Appendix\s+[A-Z0-9]+|Attachment\s+[A-Z0-9]+|Exhibit\s+[A-Z0-9]+)", re.I)),
    # "as set forth in / as defined in" + nominal
    ("see-internal", re.compile(r"\bas\s+(?:set\s+forth|defined|provided|specified)\s+in\s+(Section\s+\d+(?:\.\d+)*|Appendix\s+[A-Z0-9]+|Attachment\s+[A-Z0-9]+)", re.I)),
]


def extract_refs(text: str) -> List[Tuple[str, str, str]]:
    """Yield (kind, normalized, raw_text) tuples found in text."""
    out: List[Tuple[str, str, str]] = []
    seen_at = set()
    for kind, pat in PATTERNS:
        for m in pat.finditer(text):
            span = (m.start(), m.end(), kind)
            if span in seen_at:
                continue
            seen_at.add(span)
            raw = m.group(0)
            cap = m.group(1) if m.lastindex else m.group(0)
            normalized = cap.strip()
            if kind == "appendix":
                normalized = f"Appendix {normalized}"
            elif kind == "attachment":
                normalized = f"Attachment {normalized}"
            elif kind == "exhibit":
                normalized = f"Exhibit {normalized}"
            elif kind == "schedule":
                normalized = f"Schedule {normalized}"
            elif kind == "form":
                normalized = f"Form {normalized}"
            out.append((kind, normalized, raw))
    return out


def main() -> int:
    con = connect()
    cur = con.cursor()
    banner("STAGE 5 — Reference resolution")

    cur.executescript(
        """
        DROP TABLE IF EXISTS requirement_references;
        CREATE TABLE requirement_references (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            requirement_id        INTEGER NOT NULL REFERENCES rfp_requirements(id),
            ref_kind              TEXT NOT NULL,
            ref_label             TEXT NOT NULL,
            ref_normalized        TEXT NOT NULL,
            target_kind           TEXT NOT NULL,
            target_requirement_id INTEGER REFERENCES rfp_requirements(id),
            target_section_id     INTEGER REFERENCES section_hierarchy(id),
            target_document_id    INTEGER REFERENCES ingested_documents(id),
            target_table_id       INTEGER REFERENCES extracted_tables(id),
            target_excerpt        TEXT,
            confidence            REAL NOT NULL DEFAULT 1.0
        );
        CREATE INDEX idx_rrefs_req ON requirement_references(requirement_id);
        CREATE INDEX idx_rrefs_target_req ON requirement_references(target_requirement_id);
        CREATE INDEX idx_rrefs_target_sec ON requirement_references(target_section_id);
        CREATE INDEX idx_rrefs_kind ON requirement_references(ref_kind);
        CREATE INDEX idx_rrefs_norm ON requirement_references(ref_normalized);
        """
    )

    # Build resolver indexes once
    section_by_doc: Dict[int, Dict[str, Tuple[int, str, int, int]]] = {}
    for sid, did, code, title, first_cid, last_cid in cur.execute(
        "SELECT id, document_id, section_code, title, first_chunk_id, last_chunk_id FROM section_hierarchy"
    ):
        section_by_doc.setdefault(did, {})[code.lower()] = (sid, title, first_cid, last_cid)

    # Build a flat global index for "Appendix X" cross-doc resolution.
    section_global: Dict[str, List[Tuple[int, int, str, int, int]]] = {}
    for did, mp in section_by_doc.items():
        for code, (sid, title, fc, lc) in mp.items():
            section_global.setdefault(code, []).append((sid, did, title, fc, lc))

    # Build doc-by-name index for attachment label → document resolution.
    doc_by_name: Dict[str, int] = {}
    for did, fname, orig in cur.execute(
        "SELECT id, filename, original_filename FROM ingested_documents"
    ):
        for name in (fname, orig):
            if not name:
                continue
            doc_by_name[name.lower()] = did

    # Build table index by section + title.
    tables_by_section: Dict[str, List[Tuple[int, int, str]]] = {}
    for tid, did, sec, title in cur.execute(
        "SELECT id, document_id, section_code, title FROM extracted_tables"
    ):
        if sec:
            tables_by_section.setdefault(sec.lower(), []).append((tid, did, title))

    # Pull a chunk excerpt for tooltip rendering on resolved refs.
    def excerpt_for_section(section_id: int) -> Optional[str]:
        row = cur.execute(
            """SELECT title, first_chunk_id FROM section_hierarchy WHERE id=?""",
            (section_id,),
        ).fetchone()
        if not row:
            return None
        title, fc = row
        if fc:
            ch = cur.execute("SELECT substr(content, 1, 360) FROM document_chunks WHERE id=?", (fc,)).fetchone()
            if ch:
                return ch[0]
        return title

    requirements = cur.execute(
        """SELECT id, document_id, source_text, description
           FROM rfp_requirements WHERE superseded_by_requirement_id IS NULL"""
    ).fetchall()
    stat("Requirements scanned", len(requirements))

    inserted = 0
    by_kind = {}
    resolved = 0
    unresolved = 0

    for req_id, doc_id, src, desc in requirements:
        text = " ".join(filter(None, [src, desc]))
        if not text.strip():
            continue
        refs = extract_refs(text)
        seen_local = set()
        for kind, normalized, raw in refs:
            key = (kind, normalized.lower())
            if key in seen_local:
                continue
            seen_local.add(key)
            by_kind[kind] = by_kind.get(kind, 0) + 1

            target_kind = "unresolved"
            target_section_id = None
            target_doc_id = None
            target_table_id = None
            target_req_id = None
            excerpt = None
            confidence = 0.0

            if kind == "section":
                code = normalized
                # Same-doc first
                hit = section_by_doc.get(doc_id, {}).get(code.lower())
                if hit:
                    target_kind = "section"
                    target_section_id = hit[0]
                    target_doc_id = doc_id
                    excerpt = excerpt_for_section(hit[0])
                    confidence = 0.95
                else:
                    # Cross-doc match
                    rows = section_global.get(code.lower())
                    if rows:
                        target_kind = "section"
                        target_section_id = rows[0][0]
                        target_doc_id = rows[0][1]
                        excerpt = excerpt_for_section(rows[0][0])
                        confidence = 0.7
                    else:
                        # Prefix match: "3.2" -> any "3.2.x"
                        for cand_code in section_global:
                            if cand_code.startswith(code.lower() + "."):
                                rows2 = section_global[cand_code]
                                target_kind = "section"
                                target_section_id = rows2[0][0]
                                target_doc_id = rows2[0][1]
                                excerpt = excerpt_for_section(rows2[0][0])
                                confidence = 0.5
                                break

            elif kind in ("appendix", "attachment", "exhibit", "schedule"):
                code_lower = normalized.lower()
                # Try same-doc section
                hit = section_by_doc.get(doc_id, {}).get(code_lower)
                if hit:
                    target_kind = "section"
                    target_section_id = hit[0]
                    target_doc_id = doc_id
                    excerpt = excerpt_for_section(hit[0])
                    confidence = 0.9
                else:
                    rows = section_global.get(code_lower)
                    if rows:
                        target_kind = "section"
                        target_section_id = rows[0][0]
                        target_doc_id = rows[0][1]
                        excerpt = excerpt_for_section(rows[0][0])
                        confidence = 0.7
                    else:
                        # Try resolving as a document filename
                        for name, did in doc_by_name.items():
                            if normalized.lower() in name:
                                target_kind = "document"
                                target_doc_id = did
                                confidence = 0.6
                                break
                # Surface a related table if any
                tables = tables_by_section.get(code_lower)
                if tables and target_table_id is None:
                    target_table_id = tables[0][0]

            elif kind == "form":
                # Forms often live as their own ingested doc.
                normalized_alnum = norm_alnum(normalized)
                for name, did in doc_by_name.items():
                    if normalized_alnum and normalized_alnum in norm_alnum(name):
                        target_kind = "document"
                        target_doc_id = did
                        confidence = 0.55
                        break

            elif kind == "regulatory":
                target_kind = "regulatory"  # external; no resolve
                confidence = 1.0

            elif kind == "see-internal":
                # Recurse: extract the internal noun and treat as section/appendix.
                m = re.match(
                    r"(Section|Appendix|Attachment|Exhibit)\s+([A-Za-z0-9.\-]+)",
                    normalized.split(maxsplit=1)[-1] if " " in normalized else normalized,
                    re.I,
                )
                if m:
                    inner_kind = m.group(1).lower()
                    inner_label = (
                        m.group(2)
                        if inner_kind == "section"
                        else f"{inner_kind.capitalize()} {m.group(2)}"
                    )
                    code_lower = inner_label.lower() if inner_kind != "section" else inner_label
                    hit = section_by_doc.get(doc_id, {}).get(code_lower.lower())
                    if hit:
                        target_kind = "section"
                        target_section_id = hit[0]
                        target_doc_id = doc_id
                        excerpt = excerpt_for_section(hit[0])
                        confidence = 0.85

            elif kind == "self":
                target_kind = "self"
                confidence = 1.0

            cur.execute(
                """INSERT INTO requirement_references
                   (requirement_id, ref_kind, ref_label, ref_normalized,
                    target_kind, target_requirement_id, target_section_id,
                    target_document_id, target_table_id, target_excerpt, confidence)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    req_id,
                    kind,
                    raw,
                    normalized,
                    target_kind,
                    target_req_id,
                    target_section_id,
                    target_doc_id,
                    target_table_id,
                    (excerpt or "")[:600] if excerpt else None,
                    confidence,
                ),
            )
            inserted += 1
            if target_kind not in ("unresolved",):
                resolved += 1
            else:
                unresolved += 1

    con.commit()

    print()
    stat("References extracted", inserted)
    stat("Resolved",  resolved,   inserted)
    stat("Unresolved", unresolved, inserted)

    print()
    print("  By kind:")
    for k, n in sorted(by_kind.items(), key=lambda x: -x[1]):
        print(f"    {k:20s} {n:5d}")

    print()
    print("  Sample resolved:")
    for r in cur.execute(
        """SELECT rr.requirement_id, rr.ref_label, rr.ref_normalized,
                  rr.target_kind, sh.section_code, sh.title, ind.original_filename
           FROM requirement_references rr
           LEFT JOIN section_hierarchy sh ON sh.id = rr.target_section_id
           LEFT JOIN ingested_documents ind ON ind.id = rr.target_document_id
           WHERE rr.target_kind != 'unresolved'
           ORDER BY RANDOM() LIMIT 5"""
    ):
        print(f"    req#{r[0]} {r[1]!r} → {r[3]} sh={r[4]!r} title={(r[5] or '')[:40]!r} doc={(r[6] or '')[:40]!r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
