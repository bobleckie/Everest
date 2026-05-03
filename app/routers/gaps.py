"""Gap Workspace — interactive resolution of catalog gaps.

When the Solution Catalog is synthesized, each theme entry includes
``gap_notes`` listing what the Parsons evidence corpus does NOT yet
substantiate. Those bullets become individual gap items in the
``solution_catalog_gaps`` table.

This router lets the user:
  * List + filter the gap items
  * Chat with the agent about a specific gap
  * Upload supporting documents that get ingested into the Parsons
    knowledge corpus, neutralized of state-specific names, and linked
    to the gap as supporting evidence
  * Mark a gap resolved / won't-address
  * Trigger re-analysis: agent revisits the gap with the newly-added
    evidence and recommends a status

Cost note: chat replies and re-analysis use the configured AI provider
(small per-turn). Catalog re-synthesis still runs via Claude Code
subagents (Max plan) and is invoked separately by the user.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Query,
                       UploadFile)
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..models import IngestedDocument, User

router = APIRouter()


# ── Listing ──────────────────────────────────────────────────────────

@router.get("/")
def list_gaps(
    theme_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None,
        description="open | in_progress | resolved | wont_address"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    where = []
    params: Dict[str, Any] = {}
    if theme_id is not None:
        where.append("theme_id = :tid"); params["tid"] = theme_id
    if status:
        where.append("status = :st"); params["st"] = status
    sql = f"""
        SELECT g.id, g.catalog_entry_id, g.theme_id, g.theme_label, g.category,
               g.gap_text, g.rfp_req_codes, g.status, g.resolution_summary,
               g.resolved_at, g.created_at,
               (SELECT COUNT(*) FROM gap_messages WHERE gap_id=g.id) AS msg_count,
               (SELECT COUNT(*) FROM gap_evidence WHERE gap_id=g.id) AS ev_count
        FROM solution_catalog_gaps g
        {('WHERE ' + ' AND '.join(where)) if where else ''}
        ORDER BY g.theme_id, g.id
    """
    rows = db.execute(text(sql), params).fetchall()
    return {
        "gaps": [
            {
                "id": r.id, "catalog_entry_id": r.catalog_entry_id,
                "theme_id": r.theme_id, "theme_label": r.theme_label,
                "category": r.category, "gap_text": r.gap_text,
                "rfp_req_codes": json.loads(r.rfp_req_codes or "[]"),
                "status": r.status,
                "resolution_summary": r.resolution_summary,
                "resolved_at": (r.resolved_at.isoformat()
                                  if hasattr(r.resolved_at, "isoformat")
                                  else r.resolved_at),
                "msg_count": int(r.msg_count or 0),
                "ev_count": int(r.ev_count or 0),
            } for r in rows
        ],
        "total": len(rows),
    }


@router.get("/by-status-summary")
def gap_status_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = db.execute(text("""
        SELECT status, COUNT(*) AS n FROM solution_catalog_gaps GROUP BY status
    """)).fetchall()
    return {"counts": {r.status: int(r.n) for r in rows}}


@router.get("/{gap_id}")
def get_gap(
    gap_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    g = db.execute(text("""
        SELECT g.*, e.title AS entry_title,
               e.capability_statement AS entry_capability
        FROM solution_catalog_gaps g
        LEFT JOIN solution_catalog_entries e ON e.id = g.catalog_entry_id
        WHERE g.id = :gid
    """), {"gid": gap_id}).first()
    if not g:
        raise HTTPException(404, "Gap not found")

    msgs = db.execute(text("""
        SELECT m.id, m.role, m.content, m.attachment_doc_id, m.created_at,
               ind.original_filename AS attachment_filename
        FROM gap_messages m
        LEFT JOIN ingested_documents ind ON ind.id = m.attachment_doc_id
        WHERE m.gap_id = :gid
        ORDER BY m.created_at, m.id
    """), {"gid": gap_id}).fetchall()

    evid = db.execute(text("""
        SELECT ge.id, ge.document_id, ge.description, ge.added_at,
               ind.original_filename, ind.source_type
        FROM gap_evidence ge
        LEFT JOIN ingested_documents ind ON ind.id = ge.document_id
        WHERE ge.gap_id = :gid
        ORDER BY ge.added_at
    """), {"gid": gap_id}).fetchall()

    return {
        "id": g.id, "catalog_entry_id": g.catalog_entry_id,
        "theme_id": g.theme_id, "theme_label": g.theme_label,
        "category": g.category, "gap_text": g.gap_text,
        "rfp_req_codes": json.loads(g.rfp_req_codes or "[]"),
        "status": g.status, "resolution_summary": g.resolution_summary,
        "resolved_at": (g.resolved_at.isoformat()
                          if hasattr(g.resolved_at, "isoformat") else g.resolved_at),
        "entry_title": g.entry_title,
        "entry_capability": g.entry_capability,
        "messages": [
            {"id": m.id, "role": m.role, "content": m.content,
             "attachment_doc_id": m.attachment_doc_id,
             "attachment_filename": m.attachment_filename,
             "created_at": (m.created_at.isoformat()
                              if hasattr(m.created_at, "isoformat")
                              else m.created_at)}
            for m in msgs
        ],
        "evidence": [
            {"id": e.id, "document_id": e.document_id,
             "filename": e.original_filename, "source_type": e.source_type,
             "description": e.description,
             "added_at": (e.added_at.isoformat()
                            if hasattr(e.added_at, "isoformat") else e.added_at)}
            for e in evid
        ],
    }


# ── Chat ─────────────────────────────────────────────────────────────

class GapChatBody(BaseModel):
    content: str


def _gap_chat_system_prompt() -> str:
    return (
        "You are the Parsons capture-team agent helping resolve a gap in the "
        "Parsons Solution Catalog for the NJ MVC RFP T1628. A gap is a "
        "requirement-specific obligation the existing Parsons evidence corpus "
        "does NOT yet substantiate. The user (Parsons capture team) will "
        "either describe the resolution in writing, ask clarifying questions, "
        "or attach a document that closes the gap.\n\n"
        "Your job each turn:\n"
        "1. Read the gap, the catalog context, the conversation so far, and "
        "any new evidence the user has provided.\n"
        "2. Reply concisely (1-3 short paragraphs).\n"
        "3. When you have enough info to recommend a status change, say so "
        "explicitly: 'I recommend marking this RESOLVED because <reason>' or "
        "'this still needs <X> to fully close'.\n"
        "4. NEVER invent Parsons capabilities, contracts, or numbers. Only "
        "rely on what the user said, the gap text, the catalog excerpt, or "
        "uploaded evidence.\n"
        "5. State-neutral voice. Don't reintroduce 'Maryland', 'MVA', "
        "'VEIP', 'DoIT' unless quoting evidence specifically."
    )


def _build_chat_user_prompt(gap_row, history, evidence_chunks) -> str:
    bits: List[str] = []
    bits.append(f"## Theme: {gap_row.theme_label} ({gap_row.category})")
    if gap_row.entry_title:
        bits.append(f"Catalog entry: {gap_row.entry_title}")
    bits.append("")
    bits.append("## The gap")
    bits.append(gap_row.gap_text)
    codes = json.loads(gap_row.rfp_req_codes or "[]")
    if codes:
        bits.append(f"RFP requirement codes mentioned: {', '.join(codes)}")
    bits.append("")
    if gap_row.entry_capability:
        bits.append("## Existing catalog capability statement (excerpt)")
        bits.append(gap_row.entry_capability[:1500])
        bits.append("")
    if evidence_chunks:
        bits.append(f"## Evidence the user has attached so far ({len(evidence_chunks)})")
        for e in evidence_chunks[:6]:
            bits.append(f"- {e['filename']}: {e.get('description') or '(no description)'}")
        bits.append("")
    bits.append("## Conversation so far")
    if not history:
        bits.append("(no messages yet)")
    else:
        for m in history:
            who = "USER" if m["role"] == "user" else ("AGENT" if m["role"] == "agent" else "SYSTEM")
            bits.append(f"{who}: {m['content']}")
    return "\n".join(bits)


@router.post("/{gap_id}/messages")
def post_gap_message(
    gap_id: int,
    body: GapChatBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Append a user message to the gap thread; agent replies via the
    configured AI provider in the same turn."""
    gap = db.execute(text("""
        SELECT g.*, e.title AS entry_title,
               e.capability_statement AS entry_capability
        FROM solution_catalog_gaps g
        LEFT JOIN solution_catalog_entries e ON e.id = g.catalog_entry_id
        WHERE g.id = :gid
    """), {"gid": gap_id}).first()
    if not gap:
        raise HTTPException(404, "Gap not found")

    user_text = (body.content or "").strip()
    if not user_text:
        raise HTTPException(400, "Empty message")

    db.execute(text("""
        INSERT INTO gap_messages (gap_id, role, content, user_id)
        VALUES (:gid, 'user', :c, :uid)
    """), {"gid": gap_id, "c": user_text, "uid": getattr(current_user, "id", None)})
    db.commit()

    # Pull conversation history + evidence list
    hist = db.execute(text("""
        SELECT role, content FROM gap_messages
        WHERE gap_id = :gid ORDER BY created_at, id
    """), {"gid": gap_id}).fetchall()
    history = [{"role": r.role, "content": r.content} for r in hist]

    ev = db.execute(text("""
        SELECT ge.description, ind.original_filename AS filename
        FROM gap_evidence ge
        LEFT JOIN ingested_documents ind ON ind.id = ge.document_id
        WHERE ge.gap_id = :gid ORDER BY ge.added_at
    """), {"gid": gap_id}).fetchall()
    evidence = [{"description": e.description, "filename": e.filename} for e in ev]

    sys_prompt = _gap_chat_system_prompt()
    user_prompt = _build_chat_user_prompt(gap, history, evidence)

    try:
        from ..services.competitor_analyst import _call_ai
        reply = _call_ai(user_prompt, system=sys_prompt) or ""
    except Exception as e:
        reply = f"(agent reply failed: {e})"

    db.execute(text("""
        INSERT INTO gap_messages (gap_id, role, content)
        VALUES (:gid, 'agent', :c)
    """), {"gid": gap_id, "c": reply.strip()})
    db.commit()

    return {"ok": True, "reply": reply.strip()}


# ── Upload ───────────────────────────────────────────────────────────

UPLOADS_DIR = Path(__file__).resolve().parent.parent.parent / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)


def _state_neutralize(text: Optional[str]) -> Optional[str]:
    """Same rules as scripts/context/s17_state_neutralize.py."""
    if not text:
        return text
    rules = [
        (r"\bRFP\s+V[-–]HQ[-–]\d+[-–]?[A-Z]?\b", "the procurement"),
        (r"\bV[-–]HQ[-–]\d+[-–]?[A-Z]?\b", "the procurement"),
        (r"\bMaryland\s+VEIP\b", "the Inspection Program"),
        (r"\bMaryland'?s\s+VEIP\b", "the Inspection Program"),
        (r"\bVEIP\b", "the Inspection Program"),
        (r"\bMaryland\s+(?:MVA|Motor\s+Vehicle\s+Administration)\b", "the State Motor Vehicle agency"),
        (r"\bMVA\b", "the State Motor Vehicle agency"),
        (r"\bMaryland\s+Department\s+of\s+Information\s+Technology\b", "the State IT organization"),
        (r"\bMaryland\s+DoIT\b", "the State IT organization"),
        (r"\bDoIT\b", "the State IT organization"),
        (r"\bState\s+of\s+Maryland\b", "the State"),
        (r"\bMaryland'?s\s+Department\b", "the State's Department"),
        (r"\bin\s+Maryland\b", "in the State"),
        (r"\bMaryland\b", "the State"),
    ]
    out = text
    for pat, repl in rules:
        out = re.sub(pat, repl, out, flags=re.I if "Maryland" in pat else 0)
    return out


@router.post("/{gap_id}/upload")
async def upload_gap_evidence(
    gap_id: int,
    description: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload a supporting document, ingest it as Parsons evidence,
    state-neutralize the chunks, and link to this gap."""
    gap = db.execute(text(
        "SELECT id, theme_id FROM solution_catalog_gaps WHERE id = :gid"
    ), {"gid": gap_id}).first()
    if not gap:
        raise HTTPException(404, "Gap not found")

    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Empty file")
    h = hashlib.sha1(raw).hexdigest()[:8]
    safe_name = f"{h}_{(file.filename or 'upload').replace('/', '_')}"
    fpath = UPLOADS_DIR / safe_name
    with open(fpath, "wb") as f:
        f.write(raw)

    # Create the ingested_documents row
    doc = IngestedDocument(
        filename=safe_name,
        original_filename=file.filename,
        file_type=(file.filename or "").rsplit(".", 1)[-1].lower() or "bin",
        file_size=len(raw),
        source_type="parsons",
        description=description,
        uploaded_by=getattr(current_user, "id", None),
        document_type="gap_evidence",
        parsons_category="gap-resolution",
    )
    # procurement_scope is set via raw SQL since it's not on the SA model
    db.add(doc); db.commit(); db.refresh(doc)
    db.execute(text(
        "UPDATE ingested_documents SET procurement_scope = 'parsons_user_upload' WHERE id = :did"
    ), {"did": doc.id})

    # Parse + chunk via existing service
    chunks_added = 0
    try:
        from ..services.document_parser import parse_and_chunk
        result = parse_and_chunk(db, doc.id, str(fpath))
        chunks_added = result.get("chunks_created", 0)
        # Neutralize the new chunks now
        rows = db.execute(text(
            "SELECT id, content FROM document_chunks WHERE document_id = :did"
        ), {"did": doc.id}).fetchall()
        for r in rows:
            db.execute(text(
                "UPDATE document_chunks SET content_neutral = :cn WHERE id = :id"
            ), {"cn": _state_neutralize(r.content), "id": r.id})
        db.commit()
    except Exception as e:
        # Don't fail the upload if parsing has issues — just record what we have.
        pass

    # Link to the gap
    db.execute(text("""
        INSERT INTO gap_evidence (gap_id, document_id, description, added_by_user_id)
        VALUES (:gid, :did, :desc, :uid)
    """), {"gid": gap_id, "did": doc.id, "desc": description,
            "uid": getattr(current_user, "id", None)})
    # Append a system message
    db.execute(text("""
        INSERT INTO gap_messages (gap_id, role, content, attachment_doc_id, user_id)
        VALUES (:gid, 'system', :c, :did, :uid)
    """), {"gid": gap_id,
            "c": f"Uploaded {file.filename} ({len(raw):,} bytes, {chunks_added} chunks ingested as Parsons evidence)" + (f" — {description}" if description else ""),
            "did": doc.id, "uid": getattr(current_user, "id", None)})
    db.execute(text("""
        UPDATE solution_catalog_gaps SET status = 'in_progress' WHERE id = :gid AND status = 'open'
    """), {"gid": gap_id})
    db.commit()

    return {
        "ok": True, "document_id": doc.id, "filename": file.filename,
        "size_bytes": len(raw), "chunks_added": chunks_added,
    }


# ── Resolve ──────────────────────────────────────────────────────────

class ResolveBody(BaseModel):
    status: str   # 'resolved' | 'wont_address' | 'open' | 'in_progress'
    summary: Optional[str] = None


@router.post("/{gap_id}/resolve")
def resolve_gap(
    gap_id: int,
    body: ResolveBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    valid = {"open", "in_progress", "resolved", "wont_address"}
    if body.status not in valid:
        raise HTTPException(400, f"status must be one of {sorted(valid)}")
    summary = (body.summary or "").strip() or None
    db.execute(text("""
        UPDATE solution_catalog_gaps
        SET status = :st,
            resolution_summary = COALESCE(:sm, resolution_summary),
            resolved_at = CASE WHEN :st IN ('resolved','wont_address') THEN CURRENT_TIMESTAMP ELSE resolved_at END,
            resolved_by_user_id = CASE WHEN :st IN ('resolved','wont_address') THEN :uid ELSE resolved_by_user_id END
        WHERE id = :gid
    """), {"gid": gap_id, "st": body.status, "sm": summary,
            "uid": getattr(current_user, "id", None)})
    db.execute(text("""
        INSERT INTO gap_messages (gap_id, role, content, user_id)
        VALUES (:gid, 'system',
                'Status changed to ' || :st || COALESCE(' — ' || :sm, ''),
                :uid)
    """), {"gid": gap_id, "st": body.status, "sm": summary,
            "uid": getattr(current_user, "id", None)})
    db.commit()
    return {"ok": True, "gap_id": gap_id, "status": body.status}


# ── Re-analyze ───────────────────────────────────────────────────────

@router.post("/{gap_id}/reanalyze")
def reanalyze_gap(
    gap_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Have the agent re-examine the gap with all current evidence
    (existing catalog evidence + uploaded gap evidence + conversation
    history) and recommend a status."""
    gap = db.execute(text("""
        SELECT g.*, e.title AS entry_title,
               e.capability_statement AS entry_capability
        FROM solution_catalog_gaps g
        LEFT JOIN solution_catalog_entries e ON e.id = g.catalog_entry_id
        WHERE g.id = :gid
    """), {"gid": gap_id}).first()
    if not gap:
        raise HTTPException(404, "Gap not found")

    # Pull uploaded-evidence chunk text (state-neutralized)
    new_chunks = db.execute(text("""
        SELECT dc.content_neutral, ind.original_filename
        FROM gap_evidence ge
        JOIN ingested_documents ind ON ind.id = ge.document_id
        JOIN document_chunks dc ON dc.document_id = ind.id
        WHERE ge.gap_id = :gid
        LIMIT 30
    """), {"gid": gap_id}).fetchall()

    user_msgs = db.execute(text("""
        SELECT content FROM gap_messages WHERE gap_id = :gid AND role = 'user'
        ORDER BY created_at LIMIT 20
    """), {"gid": gap_id}).fetchall()

    bits = [
        f"# Re-analyze gap {gap.id}: {gap.theme_label}",
        "",
        "## Original gap",
        gap.gap_text,
        "",
    ]
    if user_msgs:
        bits.append("## User-provided context")
        for m in user_msgs:
            bits.append(f"- {m.content[:500]}")
        bits.append("")
    if new_chunks:
        bits.append(f"## Newly uploaded evidence excerpts ({len(new_chunks)})")
        for c in new_chunks[:10]:
            snippet = (c.content_neutral or "")[:600]
            bits.append(f"### {c.original_filename}")
            bits.append(snippet)
            bits.append("")
    bits.append("## Decision")
    bits.append(
        "Decide whether the original gap is now SUBSTANTIATED. Reply with:\n"
        "STATUS: <resolved|in_progress|open>\n"
        "SUMMARY: <one or two sentences explaining why>\n"
    )

    try:
        from ..services.competitor_analyst import _call_ai
        reply = _call_ai(
            "\n".join(bits),
            system=("You are the Parsons capture-team's gap-resolution analyst. "
                    "Be conservative — only recommend RESOLVED when the new "
                    "evidence directly substantiates the gap. Otherwise "
                    "in_progress with a list of what's still missing."),
        ) or ""
    except Exception as e:
        reply = f"(re-analyze failed: {e})"

    # Parse the recommendation
    m = re.search(r"STATUS:\s*(resolved|in_progress|open|wont_address)", reply, re.I)
    rec_status = (m.group(1).lower() if m else "in_progress")
    summary_match = re.search(r"SUMMARY:\s*(.+)", reply, re.I | re.DOTALL)
    rec_summary = (summary_match.group(1).strip() if summary_match else reply.strip())[:1000]

    db.execute(text("""
        INSERT INTO gap_messages (gap_id, role, content)
        VALUES (:gid, 'agent', :c)
    """), {"gid": gap_id, "c": f"**Re-analysis**\n\n{reply.strip()}"})
    db.commit()

    return {
        "ok": True, "recommended_status": rec_status,
        "summary": rec_summary, "raw": reply,
    }
