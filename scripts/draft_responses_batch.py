"""Batch-draft Parsons responses for every in-scope writeup obligation.

Picks the rows that:
  * are visible (not superseded, not a child of a rollup parent)
  * are obligations (requirement_kind = 'obligation' or NULL)
  * need a writeup (response_effort = 'writeup' or NULL)
  * come from a current-2026 scope document
  * have not already been drafted (parsons_response_status IS NULL or 'not_started')

Calls ``draft_response_for_requirement`` on each, with bounded concurrency
to keep token rate sensible. Each call hits the configured AI provider
(OpenAI / Anthropic). Cost / time depend on pool size and prompt length;
the drafter caps the prompt at ~9 KB of Parsons evidence.

Re-runnable: only processes rows whose status is ``not_started`` or NULL.
Already-drafted rows are skipped unless ``--force`` is passed.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

# When run as a top-level script, the project root isn't on sys.path. Fix it.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

DB_PATH = _PROJECT_ROOT / "rfp.db"


def ensure_suggestions_column():
    con = sqlite3.connect(DB_PATH)
    cols = {row[1] for row in con.execute("PRAGMA table_info('rfp_requirements')")}
    if "parsons_response_suggestions" not in cols:
        con.execute("ALTER TABLE rfp_requirements ADD COLUMN parsons_response_suggestions TEXT")
        con.commit()
    con.close()


def get_targets(force: bool, only_proposal: int | None, limit: int | None):
    con = sqlite3.connect(DB_PATH)
    where = [
        "r.superseded_by_requirement_id IS NULL",
        "(r.rollup_role IS NULL OR r.rollup_role = 'parent')",
        "(r.requirement_kind = 'obligation' OR r.requirement_kind IS NULL)",
        "(r.response_effort = 'writeup' OR r.response_effort IS NULL)",
        "ind.procurement_scope = 'current_2026'",
    ]
    if not force:
        where.append("(r.parsons_response_status IS NULL OR r.parsons_response_status IN ('not_started','rejected','ai_drafted'))")
    if only_proposal:
        where.append("(r.proposal_id = ? OR r.proposal_id IS NULL)")
    sql = f"""
        SELECT r.id FROM rfp_requirements r
        JOIN ingested_documents ind ON ind.id = r.document_id
        WHERE {' AND '.join(where)}
        ORDER BY r.id
        {f'LIMIT {int(limit)}' if limit else ''}
    """
    params = (only_proposal,) if only_proposal else ()
    rows = [r[0] for r in con.execute(sql, params).fetchall()]
    con.close()
    return rows


def draft_one(rid: int, force: bool):
    """Run the drafter for a single requirement. Returns (rid, ok, info)."""
    # Lazy import inside the worker so each thread gets its own SA session.
    from app.database import SessionLocal
    from app.services.parsons_response import draft_response_for_requirement
    db = SessionLocal()
    try:
        result = draft_response_for_requirement(db, rid, force=force)
        if "error" in result:
            return (rid, False, result["error"])
        return (rid, True, result.get("compliance_disposition") or "?")
    except Exception as e:
        return (rid, False, repr(e))
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=4,
                        help="Concurrency (default 4).")
    parser.add_argument("--limit", type=int, default=None,
                        help="Cap rows for testing.")
    parser.add_argument("--force", action="store_true",
                        help="Re-draft already-drafted rows.")
    parser.add_argument("--proposal", type=int, default=1,
                        help="Limit to this proposal_id (default 1).")
    args = parser.parse_args()

    ensure_suggestions_column()
    targets = get_targets(args.force, args.proposal, args.limit)
    n = len(targets)
    if not n:
        print("No requirements need drafting. Pass --force to re-draft.")
        return 0

    print(f"Drafting Parsons responses for {n:,} requirements")
    print(f"  workers: {args.workers}")
    print(f"  proposal_id: {args.proposal}")
    print(f"  force re-draft: {args.force}")
    print()

    started = time.time()
    ok_count = 0
    err_count = 0
    disposition_counts: dict[str, int] = {}
    last_print = started

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(draft_one, rid, args.force): rid for rid in targets}
        for i, fut in enumerate(as_completed(futures), 1):
            rid, ok, info = fut.result()
            if ok:
                ok_count += 1
                disposition_counts[info] = disposition_counts.get(info, 0) + 1
            else:
                err_count += 1
                if err_count <= 5:
                    print(f"  ERR req#{rid}: {info[:140]}")
            now = time.time()
            if now - last_print >= 5 or i == n:
                elapsed = now - started
                rate = i / elapsed if elapsed else 0
                eta_s = (n - i) / rate if rate else 0
                print(f"  [{i:5d}/{n:5d}]  ok={ok_count}  err={err_count}  "
                      f"{rate:5.1f} req/s  eta={int(eta_s/60):>3d}m{int(eta_s%60):02d}s",
                      flush=True)
                last_print = now

    elapsed = time.time() - started
    print()
    print(f"Done in {int(elapsed/60)}m{int(elapsed%60)}s")
    print(f"  drafted: {ok_count:,}")
    print(f"  errors:  {err_count:,}")
    print()
    print("Disposition breakdown:")
    for d, n in sorted(disposition_counts.items(), key=lambda x: -x[1]):
        print(f"  {d:25s} {n:5d}")
    return 0 if err_count < ok_count else 1


if __name__ == "__main__":
    sys.exit(main())
