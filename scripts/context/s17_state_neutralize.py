"""Stage 17 — State-context neutralization for Parsons evidence.

The Parsons technical proposal corpus we ingested is a Maryland VEIP
submission (RFP V-HQ-24019-S). Half the v1 drafts leaked
"Maryland VEIP", "MVA", "DoIT" verbatim into NJ MVC responses. Drafting
v2 against this raw corpus would repeat the leakage.

This stage adds a ``content_neutral`` column to ``document_chunks`` for
Parsons-source chunks. The neutralized text replaces state-specific
proper nouns with generic placeholders that the v2 drafter prompt is
instructed to bind to NJ MVC context:

  Maryland VEIP / VEIP    → the Inspection Program
  Maryland's? VEIP        → the Inspection Program
  Maryland MVA / MVA      → the State / the Agency
  the State of Maryland   → the State
  Maryland's? Department  → the State's Department
  DoIT                    → the State IT organization
  Glen Burnie / Annapolis / Baltimore  → (regional location)
  V-HQ-24019-S / RFP V-... → the procurement

The original ``content`` is preserved verbatim for audit and citations.
The drafter pulls ``content_neutral`` instead.

Re-runnable: rebuilds the column from scratch.
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


# Replacement order matters — longest first so we don't half-replace.
# The replacement text intentionally reads naturally so the LLM doesn't
# notice the substitution.
RULES = [
    # RFP / contract identifiers
    (re.compile(r"\bRFP\s+V[-–]HQ[-–]\d+[-–]?[A-Z]?\b"),                 "the procurement"),
    (re.compile(r"\bV[-–]HQ[-–]\d+[-–]?[A-Z]?\b"),                       "the procurement"),
    # Maryland state and agency
    (re.compile(r"\bMaryland\s+VEIP\b", re.I),                            "the Inspection Program"),
    (re.compile(r"\bMaryland'?s\s+VEIP\b", re.I),                         "the Inspection Program"),
    (re.compile(r"\bVEIP\b"),                                              "the Inspection Program"),
    (re.compile(r"\bMaryland\s+(MVA|Motor\s+Vehicle\s+Administration)\b"),
                                                                            "the State Motor Vehicle agency"),
    (re.compile(r"\bMVA\b"),                                                "the State Motor Vehicle agency"),
    (re.compile(r"\bMaryland\s+Department\s+of\s+Information\s+Technology\b", re.I),
                                                                            "the State IT organization"),
    (re.compile(r"\bMaryland\s+DoIT\b"),                                    "the State IT organization"),
    (re.compile(r"\bDoIT\b"),                                               "the State IT organization"),
    # Generic state references
    (re.compile(r"\bState\s+of\s+Maryland\b"),                              "the State"),
    (re.compile(r"\bMaryland'?s\s+Department\b"),                           "the State's Department"),
    (re.compile(r"\bin\s+Maryland\b"),                                      "in the State"),
    (re.compile(r"\bMaryland\s+state\b", re.I),                             "the State"),
    (re.compile(r"\bMaryland\s+vehicle\b", re.I),                           "State vehicle"),
    (re.compile(r"\bMaryland\s+motorist", re.I),                            "State motorist"),
    (re.compile(r"\bMaryland\s+driver", re.I),                              "State driver"),
    (re.compile(r"\bMaryland\s+Statute", re.I),                             "State Statute"),
    (re.compile(r"\bMaryland\s+law", re.I),                                 "State law"),
    (re.compile(r"\bMaryland\s+Department\b", re.I),                        "the State Department"),
    (re.compile(r"\bMaryland\b"),                                           "the State"),
    # Locations — regionalize without naming
    (re.compile(r"\b(?:Glen\s+Burnie|Annapolis|Baltimore|Bethesda|Frederick|Hagerstown|Rockville|Salisbury)\b", re.I),
                                                                            "(regional location)"),
]


def neutralize(text: Optional[str]) -> Optional[str]:
    if not text:
        return text
    out = text
    for pat, repl in RULES:
        out = pat.sub(repl, out)
    return out


def main() -> int:
    con = connect()
    cur = con.cursor()
    banner("STAGE 17 — State-context neutralization for Parsons evidence")

    ensure_column(cur, "document_chunks", "content_neutral", "TEXT")
    cur.execute("UPDATE document_chunks SET content_neutral = NULL")
    con.commit()

    rows = cur.execute(
        """SELECT dc.id, dc.content
           FROM document_chunks dc
           JOIN ingested_documents ind ON ind.id = dc.document_id
           WHERE ind.source_type = 'parsons' AND dc.content IS NOT NULL"""
    ).fetchall()
    stat("Parsons chunks to neutralize", len(rows))

    n_changed = 0
    n_total = 0
    sel = con.cursor()
    for cid, content in rows:
        n_total += 1
        neutral = neutralize(content)
        cur.execute(
            "UPDATE document_chunks SET content_neutral = ? WHERE id = ?",
            (neutral, cid),
        )
        if neutral != content:
            n_changed += 1
    con.commit()

    print()
    stat("Chunks scanned", n_total)
    stat("Chunks where text was modified", n_changed)
    print()

    # Smoke-check — sample a couple before/after
    print("  Sample before/after:")
    for cid, content, neutral in cur.execute(
        """SELECT id, substr(content, 1, 200), substr(content_neutral, 1, 200)
           FROM document_chunks
           WHERE content_neutral IS NOT NULL
             AND content_neutral != content
           LIMIT 3"""
    ):
        print(f"\n    chunk#{cid} BEFORE: {content!r}")
        print(f"    chunk#{cid}  AFTER: {neutral!r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
