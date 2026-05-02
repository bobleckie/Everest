"""Idempotent seed: ensures the default Parsons admin user exists.

Adds missing columns (first_name, last_name, must_change_password) to the
users table if an older SQLite DB was created before the model was updated.

Default admin (override via env):
  ADMIN_FIRST_NAME=Robert
  ADMIN_LAST_NAME=Leckie
  ADMIN_EMAIL=Robert.Leckie@parsons.com
  ADMIN_USERNAME=Robert.Leckie@parsons.com   # defaults to email
  ADMIN_PASSWORD=changeme123!
  FORCE_PASSWORD_CHANGE=1

Run:  py -3 seed_admin.py
"""
import os
from sqlalchemy import inspect, text
from app.database import SessionLocal, engine, Base
from app.models import User
from app.auth import get_password_hash


def ensure_columns(table, expected):
    """Add any missing columns to `table` (SQLite-safe).

    `expected` is a dict of {column_name: 'TYPE [DEFAULT ...]'} SQL fragments.
    """
    insp = inspect(engine)
    try:
        existing = {c["name"] for c in insp.get_columns(table)}
    except Exception:
        return  # table doesn't exist yet; create_all will handle it
    alters = []
    for col, spec in expected.items():
        if col not in existing:
            alters.append(f"ALTER TABLE {table} ADD COLUMN {col} {spec}")
    if alters:
        with engine.begin() as conn:
            for stmt in alters:
                conn.execute(text(stmt))
                print(f"Applied: {stmt}")


Base.metadata.create_all(bind=engine)
ensure_columns("users", {
    "first_name": "VARCHAR",
    "last_name": "VARCHAR",
    "must_change_password": "BOOLEAN DEFAULT 0",
})
ensure_columns("prompt_templates", {
    "refined_content": "TEXT",
    "refined_at": "DATETIME",
    "refined_by_model": "VARCHAR",
})

first_name = os.getenv("ADMIN_FIRST_NAME", "Robert")
last_name = os.getenv("ADMIN_LAST_NAME", "Leckie")
email = os.getenv("ADMIN_EMAIL", "Robert.Leckie@parsons.com")
username = os.getenv("ADMIN_USERNAME", email)
password = os.getenv("ADMIN_PASSWORD", "changeme123!")
force_change = os.getenv("FORCE_PASSWORD_CHANGE", "1") not in ("0", "false", "False", "")

db = SessionLocal()
try:
    # Clean up legacy rows with NULL username (from earlier broken seeds)
    bad = db.query(User).filter(User.username.is_(None)).all()
    for u in bad:
        db.delete(u)
    if bad:
        db.commit()
        print(f"Removed {len(bad)} legacy rows with NULL username")

    # Match by username OR email (covers earlier placeholder 'admin')
    existing = (
        db.query(User)
        .filter((User.username == username) | (User.email == email))
        .first()
    )

    if existing:
        existing.username = username
        existing.email = email
        existing.first_name = first_name
        existing.last_name = last_name
        existing.hashed_password = get_password_hash(password)
        existing.role = "admin"
        existing.is_active = True
        existing.must_change_password = force_change
        db.commit()
        print(f"Updated admin '{username}' (id={existing.id}, must_change={force_change})")
    else:
        user = User(
            username=username,
            email=email,
            first_name=first_name,
            last_name=last_name,
            hashed_password=get_password_hash(password),
            role="admin",
            is_active=True,
            must_change_password=force_change,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        print(f"Created admin '{username}' (id={user.id}, must_change={force_change})")

    # Remove stale placeholder 'admin' account if it isn't the real admin
    placeholder = db.query(User).filter(User.username == "admin").first()
    if placeholder and placeholder.email != email:
        db.delete(placeholder)
        db.commit()
        print("Removed stale placeholder 'admin' account")

    print(f"Login with: {username} / {password}")
    if force_change:
        print("  (first-login password change will be required)")
finally:
    db.close()
