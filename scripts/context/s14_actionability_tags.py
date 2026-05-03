"""Stage 14 — Actionability tagging.

The user shouldn't have to scroll past 4,000+ rows when only a fraction
are things the proposal team has to actually WRITE a response to. Many
'requirements' are background, intent, definitions, or descriptions of
the State's own rights — informational, but not actionable from a
writing perspective.

We classify each obligation into one of:
  actionable_obligation  — Bidder/Vendor/Contractor must do/provide/submit
                           something. The default — these need a written
                           response.
  informational          — Background, intent, scope, "all times are
                           Eastern", "estimated quantities may vary".
  state_action           — The State / MVC / Treasurer / Director is the
                           actor ("State may reject any/all Quotes").
                           No bidder response required.
  scoring_criterion      — Defines how the proposal will be evaluated
                           ("evaluator will assess responsiveness").
                           Useful context but no direct response.
  procedural             — Describes the procurement process itself
                           (pre-quote conference dates, where to upload).
                           Compliance is "do this on day X", not write.
  definition             — Definition of a term or acronym.

We do NOT alter title / description / source_text. Adds:
  rfp_requirements.actionability TEXT
  rfp_requirements.is_actionable BOOLEAN  -- shorthand for the most-common UI filter

Re-runnable: clears columns first.
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


# Patterns that signal each kind. Order matters — first match wins so put
# the most specific signals first.

DEFINITION_RE = re.compile(
    r"\bshall\s+mean\b|\bmeans?\s+the\b|\bis\s+defined\s+as\b|^\s*[\"“][^\"”]+[\"”]\s+means?\b",
    re.I,
)

SCORING_RE = re.compile(
    r"\b(evaluation\s+criteri|technical\s+evaluation|scoring\s+criteri|"
    r"evaluator\s+(will|shall)\s+(assess|score|consider|review)|"
    r"will\s+be\s+(scored|evaluated))\b",
    re.I,
)

# State / Director / Treasurer / MVC / Court is the actor — bidder doesn't write to this.
STATE_ACTOR_RE = re.compile(
    r"^(?:the\s+)?(state|director|treasurer|mvc|court|division|department)\b\s+"
    r"(may|shall|will|reserves|reserves\s+the\s+right|is\s+authorized|"
    r"intends|expects|anticipates|determines|decides|grants|denies)",
    re.I,
)
STATE_TITLE_RE = re.compile(
    r"^(?:state|director|treasurer|mvc|court|division|department)\s+"
    r"(?:may|reserves|right|authorize|intend|determine|decide|grant|deny|"
    r"acceptance|notification|review)",
    re.I,
)

PROCEDURAL_PATTERNS = [
    re.compile(r"\b(pre[-\s]?quote\s+conference|site\s+visit|electronic\s+questions?|q\s*&\s*a\s+period)\b", re.I),
    re.compile(r"\bquote\s+(opening|due)\s+date\b", re.I),
    re.compile(r"\b(do\s+not\s+upload|do\s+not\s+include|use\s+the\s+njstart\s+system\s+to)\b", re.I),
    re.compile(r"\ball\s+times\s+(?:contained|listed|referenced)", re.I),
]

INFORMATIONAL_PATTERNS = [
    re.compile(r"^(?:purpose|intent|background|introduction|overview)\s*[:.\-—]", re.I),
    re.compile(r"\b(historical|history|previously|prior\s+to|in\s+the\s+past)\b", re.I),
    re.compile(r"\b(estimated|approximate|approximately)\s+\d", re.I),
    re.compile(r"\b(may\s+vary|no\s+guaranteed|not\s+a\s+(?:minimum|maximum)|subject\s+to\s+change)\b", re.I),
    re.compile(r"^this\s+(?:bid\s+)?solicitation\s+(?:is|seeks|describes|sets\s+forth)", re.I),
    re.compile(r"^(?:the\s+)?contract\s+(?:term|amount)\s+(?:is|will\s+be)", re.I),
    re.compile(r"^(?:not\s+applicable|n/?a)\b", re.I),
]

# Strong actionable signals. If we see these AND the subject is the bidder,
# this is almost certainly something the proposal writer must address.
BIDDER_ACTOR_RE = re.compile(
    r"\b(?:vendor|bidder|contractor|vendor\s*\{(?:bidder|contractor)\}|the\s+(?:vendor|bidder|contractor)|"
    r"its|their|we|parsons)\b",
    re.I,
)

ACTION_VERBS_RE = re.compile(
    r"\b(?:shall\s+(?:provide|submit|maintain|deliver|develop|implement|"
    r"establish|ensure|install|conduct|complete|attend|describe|demonstrate|"
    r"identify|comply|certify|warrant|indemnify|register|operate|inspect|"
    r"coordinate|administer|create|build|configure|test|train|track|report|"
    r"include|incorporate|address|perform|manage|monitor|notify|secure|"
    r"refresh|backup|restore|migrate|integrate|upgrade|verify|review|sign|"
    r"acknowledge|certify|obtain|hold|carry|name|list|propose|describe|"
    r"furnish|distribute|repair|replace|update|correct|resolve|escalate|"
    r"document|catalog|invoice|process|pay|reimburse|account)|"
    r"\bmust\s+(?:provide|submit|maintain|deliver|develop|implement|"
    r"establish|ensure|install|conduct|complete|attend|describe|demonstrate|"
    r"identify|comply|certify|warrant|indemnify|register|operate|inspect|"
    r"coordinate|administer|create|build|configure|test|train|track|report|"
    r"include|address|perform|manage|monitor|notify|secure|backup|restore|"
    r"migrate|verify|review|acknowledge|obtain|hold|carry|name|list|"
    r"distribute|repair|replace|update|correct|resolve|document|process|pay))",
    re.I,
)


# Imperative verb at start of title — the LLM paraphraser rendered most
# obligations as "<Verb> <object>" titles. Match a broad set so the
# "actionable" default holds.
IMPERATIVE_TITLE_RE = re.compile(
    r"^(?:Submit|Provide|Maintain|Deliver|Develop|Implement|Establish|Ensure|"
    r"Install|Conduct|Complete|Attend|Describe|Demonstrate|Identify|Comply|"
    r"Certify|Warrant|Indemnify|Register|Operate|Inspect|Coordinate|Administer|"
    r"Create|Build|Configure|Test|Train|Track|Report|Include|Address|Perform|"
    r"Manage|Monitor|Notify|Secure|Backup|Restore|Migrate|Verify|Review|"
    r"Acknowledge|Obtain|Hold|Carry|Name|List|Distribute|Repair|Replace|"
    r"Update|Correct|Resolve|Document|Process|Pay|Apply|Refresh|Integrate|"
    r"Upgrade|Sign|Furnish|Catalog|Invoice|Reimburse|Account|Allow|Run|Use|"
    r"Make|Set|Store|Calibrate|Issue|Schedule|Plan|Draft|Propose|Pose|"
    r"Watch|Detect|Trigger|Pull|Push|Lock|Unlock|Disable|Enable|Activate|"
    r"Deactivate|Remove|Forward|Send|Receive|Approve|Reject|Confirm|Validate|"
    r"Track|Trace|Audit|Investigate|Resume|Restart|Continue|Begin|Finish|"
    r"Stop|Pause|Tighten|Loosen|Lift|Lower|Mount|Dismount|Connect|Disconnect|"
    r"Inject|Extract|Load|Unload|Serve|Service|Replenish|Sweep|Vacuum|Clean|"
    r"Empty|Fill|Mow|Salt|Plow|Order|Purchase|Lease|Rent|Buy|Sell|Build|"
    r"Cooperate|Collaborate|Participate|Contribute|Assign|Re-assign|Reassign|"
    r"Print|Fax|Email|Call|Telephone|Field|Handle|Triage|Escalate|Resolve)\b",
)

# Modal-verb obligation in source text (covers passive: "must be maintained",
# active: "shall provide", etc.)
SOURCE_OBLIGATION_RE = re.compile(
    r"\b(?:shall|must|will|is\s+required|are\s+required|is\s+to|"
    r"required\s+to|obligated\s+to|responsible\s+for|prior\s+to|"
    r"no\s+later\s+than|at\s+a\s+minimum)\b",
    re.I,
)


def classify_actionability(title: Optional[str], desc: Optional[str], src: Optional[str]) -> str:
    title = (title or "").strip()
    desc = (desc or "").strip()
    src = (src or "").strip()
    blob = f"{title}\n{desc}\n{src}"

    # 1. Definitions — must come first, since a definition can include a verb
    if DEFINITION_RE.search(blob):
        return "definition"
    # 2. Scoring criteria
    if SCORING_RE.search(blob):
        return "scoring_criterion"
    # 3. State / Director is the actor — but only if the bidder isn't ALSO mentioned
    # with action language. Mixed sentences fall through to actionable.
    state_subject = bool(STATE_TITLE_RE.match(title) or STATE_ACTOR_RE.search(blob))
    if state_subject:
        bidder_acting = (BIDDER_ACTOR_RE.search(blob) and
                         (ACTION_VERBS_RE.search(blob) or
                          IMPERATIVE_TITLE_RE.match(title) or
                          SOURCE_OBLIGATION_RE.search(src)))
        if not bidder_acting:
            return "state_action"
    # 4. Procedural — process / how to file the bid, not what the contract requires
    for pat in PROCEDURAL_PATTERNS:
        if pat.search(blob):
            return "procedural"

    # 5. Strong actionable signals win.
    if IMPERATIVE_TITLE_RE.match(title):
        return "actionable_obligation"
    if SOURCE_OBLIGATION_RE.search(src) or SOURCE_OBLIGATION_RE.search(desc):
        return "actionable_obligation"
    if ACTION_VERBS_RE.search(blob):
        return "actionable_obligation"

    # 6. Informational only when NO action signals AND content matches a
    #    purpose/intent/background pattern.
    for pat in INFORMATIONAL_PATTERNS:
        if pat.search(blob):
            return "informational"

    # 7. Default: when in doubt, treat as actionable so the writer reviews it.
    #    The cost of false-positive (writer skims over a non-action) is way
    #    lower than false-negative (writer misses a real obligation).
    return "actionable_obligation"


# requirement_class values from the upstream LLM extractor that ALREADY
# sort into our three response-effort buckets.
EFFORT_BY_CLASS = {
    "proposal_obligation": "writeup",       # paragraph-of-Parsons-content
    "technical_spec":      "writeup",
    "inherited_hr":        "writeup",
    "checklist_row":       "attestation",   # yes/comply or attach a form
    "sf_form":             "attestation",
    "deadline":            "attestation",
    "informational":       "info",          # often misnamed — refined below
    # 'unclassified' is left out of this map; we infer below.
}


def classify_response_effort(req_class: Optional[str], actionability: str,
                              source_text: Optional[str], description: Optional[str]) -> str:
    """Return one of: 'writeup' | 'attestation' | 'info'.

    Combines the upstream requirement_class (when set) with my
    actionability tag and a length-of-content heuristic for the
    'unclassified' bucket.
    """
    src = (source_text or "").strip()
    desc = (description or "").strip()
    src_len = len(src)
    desc_len = len(desc)

    # Hard non-writeup signals
    if actionability in ("definition", "scoring_criterion", "state_action"):
        return "info"
    if actionability == "informational":
        return "info"
    if actionability == "procedural":
        return "attestation"

    # Class-driven mapping when the LLM already gave us a strong tag.
    direct = EFFORT_BY_CLASS.get(req_class or "")
    if direct:
        # Override "info" → "writeup" when the LLM tagged "informational"
        # but our actionability said it's actionable AND content is meaty.
        if direct == "info" and actionability == "actionable_obligation" and src_len > 200:
            return "writeup"
        return direct

    # Unclassified — fall back to content-shape heuristic.
    if actionability == "actionable_obligation":
        # Substantive content → writeup
        if src_len + desc_len > 300:
            return "writeup"
        # Form-shaped tabular content (pipes) → attestation
        if "|" in src and src.count("|") >= 4:
            return "attestation"
        # Short and actionable → still writeup, the writer should review
        return "writeup"
    return "info"


def main() -> int:
    con = connect()
    cur = con.cursor()
    banner("STAGE 14 — Actionability tagging")

    ensure_column(cur, "rfp_requirements", "actionability", "TEXT")
    ensure_column(cur, "rfp_requirements", "is_actionable", "INTEGER")
    ensure_column(cur, "rfp_requirements", "response_effort", "TEXT")
    cur.execute("UPDATE rfp_requirements SET actionability=NULL, is_actionable=NULL, response_effort=NULL")
    con.commit()

    rows = cur.execute(
        "SELECT id, title, description, source_text, requirement_class FROM rfp_requirements"
    ).fetchall()
    stat("Total requirements scanned", len(rows))

    counts = defaultdict(int)
    effort_counts = defaultdict(int)
    for rid, title, desc, src, req_class in rows:
        a = classify_actionability(title, desc, src)
        is_act = 1 if a == "actionable_obligation" else 0
        effort = classify_response_effort(req_class, a, src, desc)
        cur.execute(
            """UPDATE rfp_requirements
               SET actionability=?, is_actionable=?, response_effort=?
               WHERE id=?""",
            (a, is_act, effort, rid),
        )
        counts[a] += 1
        effort_counts[effort] += 1
    con.commit()

    print()
    print("All-rows breakdown:")
    for k in ("actionable_obligation", "informational", "state_action",
              "scoring_criterion", "procedural", "definition"):
        n = counts.get(k, 0)
        pct = 100 * n / len(rows) if rows else 0
        print(f"  {k:25s} {n:6,}  ({pct:5.1f}%)")

    # Visible (parent + standalone, kind=obligation) breakdown
    visible = cur.execute(
        """SELECT actionability, COUNT(*) FROM rfp_requirements
           WHERE superseded_by_requirement_id IS NULL
             AND (rollup_role IS NULL OR rollup_role='parent')
             AND (requirement_kind = 'obligation' OR requirement_kind IS NULL)
           GROUP BY actionability"""
    ).fetchall()
    total_v = sum(n for _, n in visible)
    print()
    print("Visible obligations × actionability:")
    for k in ("actionable_obligation", "informational", "state_action",
              "scoring_criterion", "procedural", "definition"):
        n = next((n for k2, n in visible if k2 == k), 0)
        pct = 100 * n / total_v if total_v else 0
        print(f"  {k:25s} {n:6,}  ({pct:5.1f}%)")

    print()
    print("Visible obligations × response_effort  (the writer-facing axis):")
    for effort in ("writeup", "attestation", "info"):
        n = cur.execute("""
          SELECT COUNT(*) FROM rfp_requirements
          WHERE superseded_by_requirement_id IS NULL
            AND (rollup_role IS NULL OR rollup_role='parent')
            AND (requirement_kind = 'obligation' OR requirement_kind IS NULL)
            AND response_effort = ?
        """, (effort,)).fetchone()[0]
        pct = 100 * n / total_v if total_v else 0
        print(f"  {effort:15s} {n:6,}  ({pct:5.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
