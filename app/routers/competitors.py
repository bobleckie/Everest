"""Competitor library CRUD + news endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import json

from ..database import get_db
from ..models import Competitor, CompetitorNewsItem, User
from ..auth import get_current_user, get_current_admin_user

router = APIRouter()

# ── Schemas ──────────────────────────────────────────────────────────

class CompetitorIn(BaseModel):
    name: str
    website: Optional[str] = None
    aliases: Optional[List[str]] = None
    description: Optional[str] = None
    watchlist: bool = True

class CompetitorOut(BaseModel):
    id: int
    name: str
    website: Optional[str]
    aliases: Optional[List[str]]
    description: Optional[str]
    watchlist: bool
    created_at: Optional[str]
    updated_at: Optional[str]

class CompetitorUpdate(BaseModel):
    name: Optional[str] = None
    website: Optional[str] = None
    aliases: Optional[List[str]] = None
    description: Optional[str] = None
    watchlist: Optional[bool] = None

class NewsItemOut(BaseModel):
    id: int
    competitor_id: int
    title: str
    url: Optional[str]
    source: Optional[str]
    summary: Optional[str]
    published_at: Optional[str]
    fetched_at: Optional[str]

# ── Helpers ──────────────────────────────────────────────────────────

def _competitor_to_out(c: Competitor) -> dict:
    aliases = None
    if c.aliases:
        try:
            aliases = json.loads(c.aliases)
        except (json.JSONDecodeError, TypeError):
            aliases = []
    return {
        "id": c.id,
        "name": c.name,
        "website": c.website,
        "aliases": aliases,
        "description": c.description,
        "watchlist": c.watchlist,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }

def _news_to_out(n: CompetitorNewsItem) -> dict:
    return {
        "id": n.id,
        "competitor_id": n.competitor_id,
        "title": n.title,
        "url": n.url,
        "source": n.source,
        "summary": n.summary,
        "published_at": n.published_at.isoformat() if n.published_at else None,
        "fetched_at": n.fetched_at.isoformat() if n.fetched_at else None,
    }

# ── Aggregated news ticker (all watchlisted) — defined BEFORE /{competitor_id} ─

@router.get("/news/latest")
def get_latest_news_all(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Latest news across all watchlisted competitors."""
    watchlisted_ids = [
        c.id for c in db.query(Competitor).filter(Competitor.watchlist == True).all()
    ]
    if not watchlisted_ids:
        return {"news": []}
    items = (
        db.query(CompetitorNewsItem)
        .filter(CompetitorNewsItem.competitor_id.in_(watchlisted_ids))
        .order_by(CompetitorNewsItem.fetched_at.desc())
        .limit(limit)
        .all()
    )
    return {"news": [_news_to_out(n) for n in items]}

# ── CRUD ─────────────────────────────────────────────────────────────

@router.get("")
def list_competitors(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    comps = db.query(Competitor).order_by(Competitor.name).all()
    return {"competitors": [_competitor_to_out(c) for c in comps]}


@router.post("")
def create_competitor(
    payload: CompetitorIn,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin_user),
):
    existing = db.query(Competitor).filter(Competitor.name == payload.name).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Competitor '{payload.name}' already exists")
    comp = Competitor(
        name=payload.name,
        website=payload.website,
        aliases=json.dumps(payload.aliases) if payload.aliases else None,
        description=payload.description,
        watchlist=payload.watchlist,
        created_by=admin.id,
    )
    db.add(comp)
    db.commit()
    db.refresh(comp)
    return _competitor_to_out(comp)


@router.get("/{competitor_id}")
def get_competitor(
    competitor_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    comp = db.query(Competitor).filter(Competitor.id == competitor_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Competitor not found")
    return _competitor_to_out(comp)


@router.put("/{competitor_id}")
def update_competitor(
    competitor_id: int,
    payload: CompetitorUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin_user),
):
    comp = db.query(Competitor).filter(Competitor.id == competitor_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Competitor not found")
    if payload.name is not None:
        comp.name = payload.name
    if payload.website is not None:
        comp.website = payload.website
    if payload.aliases is not None:
        comp.aliases = json.dumps(payload.aliases)
    if payload.description is not None:
        comp.description = payload.description
    if payload.watchlist is not None:
        comp.watchlist = payload.watchlist
    db.commit()
    db.refresh(comp)
    return _competitor_to_out(comp)


@router.delete("/{competitor_id}")
def delete_competitor(
    competitor_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin_user),
):
    comp = db.query(Competitor).filter(Competitor.id == competitor_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Competitor not found")
    # Delete associated news items first
    db.query(CompetitorNewsItem).filter(CompetitorNewsItem.competitor_id == competitor_id).delete()
    db.delete(comp)
    db.commit()
    return {"deleted": competitor_id}


# ── News ─────────────────────────────────────────────────────────────

@router.get("/{competitor_id}/news")
def get_competitor_news(
    competitor_id: int,
    since: Optional[str] = Query(None, description="ISO date string, e.g. 2026-01-01"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    comp = db.query(Competitor).filter(Competitor.id == competitor_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Competitor not found")

    q = db.query(CompetitorNewsItem).filter(CompetitorNewsItem.competitor_id == competitor_id)
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
            q = q.filter(CompetitorNewsItem.fetched_at >= since_dt)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid 'since' date format")

    items = q.order_by(CompetitorNewsItem.fetched_at.desc()).limit(limit).all()
    return {"competitor_id": competitor_id, "news": [_news_to_out(n) for n in items]}


@router.post("/{competitor_id}/refresh-news")
def refresh_competitor_news(
    competitor_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin_user),
):
    """Manually trigger news refresh for a competitor."""
    comp = db.query(Competitor).filter(Competitor.id == competitor_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Competitor not found")

    from ..services.competitor_news import fetch_news_for_competitor
    count = fetch_news_for_competitor(db, comp)
    return {"competitor_id": competitor_id, "new_items": count}



