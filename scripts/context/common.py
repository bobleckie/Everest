"""Shared helpers for the context-restoration pipeline."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent.parent / "rfp.db"


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    return con


def norm_ws(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def norm_alnum(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def banner(title: str) -> None:
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


def stat(label: str, n: int, of: int | None = None) -> None:
    if of is not None and of:
        print(f"  {label:40s} {n:>7,}  ({100*n/of:5.1f}%)")
    else:
        print(f"  {label:40s} {n:>7,}")
