"""Application settings endpoints (key-value store)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Dict, Any, Optional
import json

from ..database import get_db
from ..models import AppSetting, User
from ..auth import get_current_user, get_current_admin_user

router = APIRouter()

# ── Schemas ──────────────────────────────────────────────────────────

class SettingsOut(BaseModel):
    settings: Dict[str, Any]

class SettingsIn(BaseModel):
    settings: Dict[str, Any]  # { "key": <any JSON value>, ... }

# ── GET all settings ─────────────────────────────────────────────────

@router.get("")
def get_all_settings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = db.query(AppSetting).all()
    result = {}
    for row in rows:
        try:
            result[row.key] = json.loads(row.value_json)
        except (json.JSONDecodeError, TypeError):
            result[row.key] = row.value_json
    return {"settings": result}


# ── PUT settings (admin only) ───────────────────────────────────────

@router.put("")
def save_settings(
    payload: SettingsIn,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin_user),
):
    saved_keys = []
    for key, value in payload.settings.items():
        existing = db.query(AppSetting).filter(AppSetting.key == key).first()
        value_str = json.dumps(value)
        if existing:
            existing.value_json = value_str
            existing.updated_by = admin.id
        else:
            db.add(AppSetting(key=key, value_json=value_str, updated_by=admin.id))
        saved_keys.append(key)
    db.commit()
    return {"saved": saved_keys}


# ── GET single setting ───────────────────────────────────────────────

@router.get("/{key}")
def get_setting(
    key: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Setting '{key}' not found")
    try:
        value = json.loads(row.value_json)
    except (json.JSONDecodeError, TypeError):
        value = row.value_json
    return {"key": key, "value": value}
