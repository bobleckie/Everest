# Everest — Backup & Restore Runbook

## What gets backed up

| Asset | When | Why |
|---|---|---|
| `rfp.db` | Every run | All structured data (proposals, requirements, audit log, etc.) |
| `chroma_db/` | Every run | Vector embeddings — re-creating costs $$ + hours of LLM time |
| `uploads/` | Sundays only (or `-IncludeUploads`) | Source PDFs / DOCX. Big (~200 MB), changes infrequently |
| `.env` | NOT backed up | Contains secrets — copy out-of-band to a password manager |

## Where backups live

Default: `C:\Users\<you>\OneDrive - Parsons Corp\Everest Backup`

Override with the `EVEREST_BACKUP_DEST` env var or `-BackupDest` parameter.

## How to install the daily scheduled task (one-time)

Run **as Administrator** from the Everest workspace folder:

```powershell
powershell -ExecutionPolicy Bypass -File install_backup_task.ps1 `
  -BackupDest "C:\Users\you\OneDrive - Parsons Corp\Everest Backup"
```

This registers a Windows Scheduled Task `EverestRFP-DBBackup` that runs
daily at 2 AM. Verify with:

```powershell
Get-ScheduledTask -TaskName "EverestRFP-DBBackup"
```

## Manual backup right now

```powershell
# Daily-style backup (rfp.db + chroma):
powershell -File backup_db.ps1

# Full backup (also includes uploads/):
powershell -File backup_db.ps1 -IncludeUploads
```

## Restore

```powershell
# 1) Dry run (default — shows what would happen, doesn't touch anything):
powershell -File restore_db.ps1 -BackupDir "C:\Users\you\OneDrive - Parsons Corp\Everest Backup"

# 2) Actually restore — interactive (you'll be asked to type 'restore'):
powershell -File restore_db.ps1 `
  -BackupDir "C:\Users\you\OneDrive - Parsons Corp\Everest Backup" -Apply

# 3) Restore a SPECIFIC backup (use the timestamp from the filename):
powershell -File restore_db.ps1 -BackupDir "..." -Timestamp 2026-04-28_020000 -Apply
```

Before applying, the restore script automatically copies the *current*
live files into `db_backups/pre-restore-<ts>/` so the operation is
itself rollback-safe.

After restore, **restart the FastAPI backend** to pick up the new DB.

## Periodic confidence check (the "drill")

A scheduled backup that's never been restored is not a backup. Once a
month, run:

```powershell
py _run_restore_drill.py
```

This:
1. Finds the most recent `rfp_*.db` in the backup folder.
2. Uses sqlite3 `.backup` to restore it into a temp dir.
3. Runs `PRAGMA integrity_check`.
4. Diffs row counts against the live `rfp.db`.
5. Prints "OK — backup restores cleanly" or fails loudly.

Read-only — never touches live files.

## Recovery scenarios

### "I lost my laptop"

1. Set up a fresh checkout of Everest.
2. Copy `.env` from your password manager.
3. `powershell -File restore_db.ps1 -BackupDir <path-to-OneDrive-or-share> -Apply`.
4. (If `uploads/` wasn't restored) `restore_db.ps1 -IncludeUploads`.
5. `py seed_admin.py` (idempotent — only updates the admin password).
6. Start backend, log in, verify dashboard.

### "I corrupted rfp.db today"

1. Stop backend.
2. `restore_db.ps1 -BackupDir <path>` (dry run, see what's there).
3. `restore_db.ps1 -BackupDir <path> -Apply` (interactive confirm).
4. Start backend.
5. Run `py _fingerprint.py` and compare to the row-count baseline in
   `db_backups/pre-prod-hardening-2026-04-28/fingerprint.json`.

### "I deleted a single proposal/document by mistake"

Don't restore the whole DB — that loses everything since the last
backup. Instead:
1. `restore_db.ps1 -BackupDir <path>` (dry run to see latest).
2. Manually open the backup file with sqlite3 / DBeaver.
3. Hand-copy the missing rows out of the backup and into the live DB.
   (We don't have automated row-level restore yet.)

## What this DOES NOT cover

- **Off-site backups** — OneDrive provides cloud sync, but a single
  Parsons account being compromised could lose backups too. Move to
  Azure Blob with versioning before going to wider rollout.
- **Point-in-time recovery** — SQLite + nightly snapshots gives at most
  24h granularity. Postgres + WAL archiving (later todo) brings this
  down to seconds.
- **`.env` secrets** — store in 1Password / corp vault separately.
