# SQLite → Postgres migration runbook

This is a **copy**, not a move. SQLite stays on disk untouched. If
anything goes wrong, you flip the env var back and you're running on
SQLite again as if nothing happened.

---

## 0. Pre-flight (5 min)

```powershell
# Make sure all backend changes are in & app is healthy on SQLite.
py -m pytest -q                       # expect: 31 passed
Invoke-WebRequest http://127.0.0.1:8000/healthz   # expect: {"status":"ok"}
Invoke-WebRequest http://127.0.0.1:8000/readyz   # expect: db=ok

# Take a fresh SQLite snapshot.
py -c "import sqlite3, shutil, datetime; con=sqlite3.connect('rfp.db'); con.execute('PRAGMA wal_checkpoint(TRUNCATE)'); con.close(); ts=datetime.datetime.now().strftime('%Y%m%d_%H%M%S'); shutil.copy('rfp.db', f'db_backups/rfp.db.before_pg_migration_{ts}'); print('snapshot taken')"

# Capture the verification fingerprint.
py _fingerprint.py --out _fp_before_pg.json
```

## 1. Stand up Postgres locally (Docker)

Set a strong password (don't commit it!) and bring it up:

```powershell
$env:POSTGRES_PASSWORD = "<pick-something-strong>"
docker compose -f docker-compose.postgres.yml up -d
docker compose -f docker-compose.postgres.yml ps
# Wait until "healthy"
```

Verify connectivity from the host:

```powershell
docker exec -it everest-postgres psql -U everest -d everest -c "SELECT version();"
```

## 2. Dry-run the migration

```powershell
$pgurl = "postgresql+psycopg2://everest:$env:POSTGRES_PASSWORD@localhost:5432/everest"

py _migrate_sqlite_to_postgres.py --target $pgurl
```

Output should list every table and its row count, then say
`DRY RUN complete`. **No data was written.** Spot-check the row counts
against `_fp_before_pg.json`.

## 3. Apply the migration

```powershell
py _migrate_sqlite_to_postgres.py --target $pgurl --apply
```

The script will:
1. Refuse to run unless the target Postgres is empty.
2. Create the schema in Postgres via `Base.metadata.create_all`.
3. Copy data in dependency order, batched.
4. Reset Postgres sequences to `MAX(id)+1` per table.
5. Verify row counts + sample-row hashes per table.

A successful run ends with `ALL TABLES MATCH. Migration validated.`

If verification fails it exits non-zero with the offending table(s)
named. **Don't switch DATABASE_URL** — fix the issue, drop the
Postgres data (`docker compose -f docker-compose.postgres.yml down -v`),
and re-run.

## 4. Switch the app over

```powershell
# Edit .env and change DATABASE_URL:
#   DATABASE_URL=postgresql+psycopg2://everest:PW@localhost:5432/everest
# (or set $env:DATABASE_URL for the current shell)

# Restart the backend (no --reload).
# Use the "Start Backend (FastAPI)" task or:
py -m uvicorn app.main:app --port 8000 --host 127.0.0.1
```

Backend startup logs will show the new DATABASE_URL (redacted) and
`[config] model<->DB schema check OK (no drift)`. The drift guard from
step #2b will catch any model-vs-actual schema mismatch.

## 5. Smoke test against Postgres

```powershell
# Health
Invoke-WebRequest http://127.0.0.1:8000/healthz
Invoke-WebRequest http://127.0.0.1:8000/readyz   # db=ok now means Postgres

# Run the test suite (uses an ephemeral SQLite per test — should still pass)
py -m pytest -q

# Hit a few real endpoints in the browser:
#  - login
#  - dashboard
#  - documents list
#  - audit log: GET /api/audit
```

If anything misbehaves, **stop**, change `DATABASE_URL` back to
`sqlite:///./rfp.db`, restart. SQLite is unchanged. You're back to
the pre-migration state with zero data loss.

## 6. (Later) Re-run verification any time

```powershell
py _migrate_sqlite_to_postgres.py --target $pgurl --verify-only
```

This is a read-only diff — useful right after migration and again a
day or two later to confirm the row-count and sample-hash equality
still holds (it should, modulo any new rows your usage of the app
adds *after* the cutover).

## 7. Decommissioning SQLite (optional, much later)

DON'T do this until you've been on Postgres for at least a week with
no issues, and you have at least one Postgres backup. Then:

- Move `rfp.db` (and `rfp.db-wal`, `rfp.db-shm`) into
  `db_backups/sqlite_archive_<date>/`.
- Keep them. Don't delete. They cost nothing.

## What can go wrong

| Symptom | Likely cause | Fix |
|---|---|---|
| `REFUSING TO RUN — target has non-empty tables` | The Postgres DB isn't empty. | Either point at a fresh DB, or `docker compose -f docker-compose.postgres.yml down -v` to nuke the volume and start fresh, or pass `--allow-non-empty` (only do this if you really know what you're doing). |
| `ROW COUNT MISMATCH` | Concurrent writes during copy. | Stop the app first. Re-run. |
| `SAMPLE HASH MISMATCH` | Type coercion (datetime, JSON) differs. | Check the sample rows for both DBs by hand. Most often this is a timestamp precision issue — Postgres stores microseconds, SQLite stores strings. The script's `repr(tuple(row))` is sensitive to that. If counts match and the only diff is timestamp formatting, you're probably fine. |
| App won't start after switch | `model<->DB schema drift` log line will tell you which column is missing. The migrator uses `Base.metadata.create_all`, so this should be impossible — but if it happens, drop the Postgres volume and re-run. |
