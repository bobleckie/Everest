"""In-memory background job registry for the Everest backend.

Use case: long-running endpoints (`/api/knowledge/extract-requirements`)
were synchronous and hit browser timeouts. This module gives us a
minimal, dependency-free way to fire-and-forget work and let the
frontend poll for status.

Limitations (intentional, will be replaced by a `jobs` table + queue
runner in a later phase):
  * In-memory only. Restart-volatile. Don't persist anything important
    here that isn't ALSO written to the DB by the underlying job code.
  * No retries.
  * Jobs run in daemon threads — sufficient for our 1-process Uvicorn
    deploy. NOT safe with multiple worker processes.
  * Result is kept for `RETENTION_SECONDS` after completion, then GC'd.

Public API:
    submit(kind, target, *, doc_id=None, user_id=None, meta=None) -> Job
    get(job_id) -> Job | None
    list_jobs(kind=None, doc_id=None, limit=50) -> list[Job]
    Job (dataclass with `to_dict()`)
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Keep finished jobs around for an hour so the frontend can still poll
# right after completion. Background sweep below GCs older entries.
RETENTION_SECONDS = 60 * 60


@dataclass
class Job:
    id: str
    kind: str
    status: str  # "queued" | "running" | "done" | "error"
    submitted_at: float
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    result: Any = None
    error: Optional[str] = None
    doc_id: Optional[int] = None
    user_id: Optional[int] = None
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        def _iso(ts: Optional[float]) -> Optional[str]:
            return datetime.utcfromtimestamp(ts).isoformat() + "Z" if ts else None
        out: dict = {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "submitted_at": _iso(self.submitted_at),
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at),
            "doc_id": self.doc_id,
            "user_id": self.user_id,
            "meta": dict(self.meta),
        }
        if self.status == "done":
            out["result"] = self.result
        elif self.status == "error":
            out["error"] = self.error
        if self.started_at and self.finished_at:
            out["elapsed_seconds"] = round(self.finished_at - self.started_at, 2)
        return out


# ── Internal state ────────────────────────────────────────────────────
_jobs: dict[str, Job] = {}
_lock = threading.RLock()


def _gc_old_locked() -> None:
    """Evict finished jobs older than RETENTION_SECONDS. Caller holds lock."""
    cutoff = time.time() - RETENTION_SECONDS
    stale = [
        jid for jid, j in _jobs.items()
        if j.finished_at and j.finished_at < cutoff
    ]
    for jid in stale:
        _jobs.pop(jid, None)


def submit(
    kind: str,
    target: Callable[[Job], Any],
    *,
    doc_id: Optional[int] = None,
    user_id: Optional[int] = None,
    meta: Optional[dict] = None,
) -> Job:
    """Register a Job and start a daemon thread running `target(job)`.

    `target` is given the Job object so it can update `meta` (e.g. for
    progress updates) but its return value is what we store as
    `job.result`. Exceptions become `job.error`.
    """
    job_id = uuid.uuid4().hex
    job = Job(
        id=job_id,
        kind=kind,
        status="queued",
        submitted_at=time.time(),
        doc_id=doc_id,
        user_id=user_id,
        meta=dict(meta or {}),
    )
    with _lock:
        _gc_old_locked()
        _jobs[job_id] = job

    def _runner() -> None:
        with _lock:
            job.status = "running"
            job.started_at = time.time()
        try:
            result = target(job)
            with _lock:
                job.status = "done"
                job.result = result
                job.finished_at = time.time()
            logger.info(
                "[jobs] %s %s done in %.1fs",
                kind, job_id, (job.finished_at or 0) - (job.started_at or 0),
            )
        except Exception as e:  # noqa: BLE001 — must capture everything
            logger.exception("[jobs] %s %s FAILED: %s", kind, job_id, e)
            with _lock:
                job.status = "error"
                job.error = f"{type(e).__name__}: {e}"
                job.finished_at = time.time()

    t = threading.Thread(target=_runner, name=f"job-{kind}-{job_id[:8]}", daemon=True)
    t.start()
    return job


def get(job_id: str) -> Optional[Job]:
    with _lock:
        return _jobs.get(job_id)


def list_jobs(
    kind: Optional[str] = None,
    doc_id: Optional[int] = None,
    limit: int = 50,
) -> list[Job]:
    with _lock:
        items = list(_jobs.values())
    items.sort(key=lambda j: j.submitted_at, reverse=True)
    if kind:
        items = [j for j in items if j.kind == kind]
    if doc_id is not None:
        items = [j for j in items if j.doc_id == doc_id]
    return items[: max(1, min(limit, 500))]
