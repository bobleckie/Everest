from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional, List
from ..database import get_db
from ..models import Document, OrchestratorData, CompetitiveResearchData, ParsonsSMEData, ConflictDetectorData, DocumentComposerData, WorkflowData, User
from ..services import document_ingestion, ai_agent, workflow
from ..auth import get_current_active_user
import os

router = APIRouter()


class StartWorkflowRequest(BaseModel):
    rfp_content: str = Field(..., alias="rfpContent", min_length=1)
    competitor_name: str = Field(..., alias="competitorName", min_length=1)

    class Config:
        populate_by_name = True


class ApproveStepRequest(BaseModel):
    step_id: int
    approval_level: int = 1

@router.get("/")
def get_orchestrator():
    return {"message": "Orchestrator endpoint"}

@router.post("/upload-document")
async def upload_document(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")
    
    content = await file.read()
    doc_type = file.filename.split('.')[-1].lower()
    
    # Process the document
    if doc_type == 'pdf':
        text = document_ingestion.extract_text_from_pdf(content)
    elif doc_type == 'docx':
        text = document_ingestion.extract_text_from_docx(content)
    elif doc_type in ['jpg', 'jpeg', 'png']:
        text = document_ingestion.extract_text_from_image(content)
    else:
        text = content.decode('utf-8', errors='ignore')
    
    # Store in DB
    db_doc = Document(filename=file.filename, content=text, doc_type=doc_type)
    db.add(db_doc)
    db.commit()
    db.refresh(db_doc)
    return {"message": "Document uploaded", "id": db_doc.id}

# NOTE: Two legacy endpoints removed (POST /query, POST /search).
# They used the unscoped legacy chromadb layer (app/services/embeddings.py)
# which had NO proposal_id filtering and could mix data across RFPs.
# Modern UI uses /api/parsons-response/proposals/{id}/ask (rfp_qa.py)
# which is strictly scoped to one active proposal. Removing these
# endpoints eliminates a cross-RFP leakage vector. The chromadb store
# is empty (0 embeddings) — these endpoints had no real callers.

@router.post("/delegate")
def delegate_query(query: str, persona: str, db: Session = Depends(get_db)):
    # Delegate to specific persona
    if persona == "competitive_research":
        from ..services.ai_agent import competitive_research
        result = competitive_research(query)
        db_data = CompetitiveResearchData(data=result)
    elif persona == "parsons_sme":
        from ..services.ai_agent import parsons_sme
        result = parsons_sme(query)
        db_data = ParsonsSMEData(data=result)
    elif persona == "conflict_detector":
        from ..services.ai_agent import conflict_detector
        result = conflict_detector(query)
        db_data = ConflictDetectorData(data=result)
    elif persona == "document_composer":
        from ..services.ai_agent import document_composer
        result = document_composer(query)
        db_data = DocumentComposerData(data=result)
    elif persona == "requirement_extractor":
        from ..services.ai_agent import requirement_extractor
        result = requirement_extractor(query)
        from ..models import RequirementExtractorData
        db_data = RequirementExtractorData(data=result)
    else:
        return {"error": "Unknown persona"}
    
    db.add(db_data)
    db.commit()
    db.refresh(db_data)
    
    return {"result": result, "persona": persona, "id": db_data.id}

@router.post("/start-rfp-workflow")
def start_rfp_workflow(
    req: StartWorkflowRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    result = workflow.start_rfp_workflow(db, req.rfp_content, req.competitor_name)
    return result

@router.get("/workflows")
def list_workflows(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    workflow_type: Optional[str] = "rfp_response",
    limit: int = 50,
):
    """
    List recent workflow runs, grouped by the terminal step id. Each run is a set of
    contiguous steps ending with a 'conflict_check' (or latest step) for the given type.
    """
    rows = (
        db.query(WorkflowData)
        .filter(WorkflowData.workflow_type == workflow_type)
        .order_by(WorkflowData.id.desc())
        .limit(limit * 6)  # 6 steps per run
        .all()
    )
    # Group rows into runs ending at a 'conflict_check' step (the terminal step in
    # the pipeline). Any leading orphan rows (from failed runs) are ignored here so
    # the UI only sees complete runs. A proper WorkflowRun table is a TODO.
    runs: List[dict] = []
    current: List[WorkflowData] = []
    for row in reversed(rows):  # oldest first
        current.append(row)
        if row.step == "conflict_check":
            runs.append(_serialize_run(current))
            current = []
    # `current` now holds an in-flight run that has not yet reached conflict_check.
    # Only surface it if it actually started (has extract_requirements).
    if current and any(s.step == "extract_requirements" for s in current):
        runs.append(_serialize_run(current))
    runs.reverse()  # most recent first
    return {"workflows": runs[:limit]}


def _serialize_run(steps: List[WorkflowData]) -> dict:
    return {
        "workflow_id": steps[-1].id,
        "status": "completed" if all(s.status in ("completed", "approved") for s in steps) else "in_progress",
        "current_step": sum(1 for s in steps if s.status in ("completed", "approved")),
        "steps": [
            {
                "id": s.id,
                "name": s.step,
                "status": s.status,
                "approval_level": s.approval_level or 0,
                "preview": (s.data or "")[:200],
            }
            for s in steps
        ],
    }


@router.get("/stats")
def dashboard_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Summary counters for the Dashboard landing page."""
    total_documents = db.query(Document).count()
    # Active = any workflow row still in a non-terminal state
    active_steps = (
        db.query(WorkflowData)
        .filter(WorkflowData.workflow_type == "rfp_response")
        .filter(WorkflowData.status.in_(["pending", "in_progress"]))
        .count()
    )
    completed_responses = (
        db.query(WorkflowData)
        .filter(WorkflowData.workflow_type == "rfp_response")
        .filter(WorkflowData.step == "conflict_check")
        .filter(WorkflowData.status.in_(["completed", "approved"]))
        .count()
    )
    pending_approvals = (
        db.query(WorkflowData)
        .filter(WorkflowData.workflow_type == "rfp_response")
        .filter(WorkflowData.approval_level == 0)
        .filter(WorkflowData.status == "pending")
        .count()
    )
    return {
        "totalDocuments": total_documents,
        "activeWorkflows": active_steps,
        "completedResponses": completed_responses,
        "pendingApprovals": pending_approvals,
    }


@router.get("/workflow-status/{workflow_id}")
def get_workflow_status(
    workflow_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    # Return the six steps preceding and including workflow_id (inclusive).
    rows = (
        db.query(WorkflowData)
        .filter(WorkflowData.id <= workflow_id, WorkflowData.workflow_type == "rfp_response")
        .order_by(WorkflowData.id.desc())
        .limit(6)
        .all()
    )
    rows.reverse()
    if not rows:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return _serialize_run(rows)


@router.post("/approve-step")
def approve_step(
    req: ApproveStepRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    result = workflow.approve_step(db, req.step_id, req.approval_level)
    if not result:
        raise HTTPException(status_code=404, detail="Step not found")
    return {"approved": result.status, "step_id": result.id, "approval_level": result.approval_level or 0}


@router.post("/start-vendor-analysis", deprecated=True)
def start_vendor_analysis():
    """RETIRED — use POST /api/intelligence/competitors/{id}/intelligence-run instead.

    This endpoint produced overlapping output with the dossier path; both have
    been merged into the unified intelligence run, which runs every connector
    (deterministic + Anthropic web_search), synthesizes threads + a timeline,
    and drafts the 8 dossier categories with full evidence citations.

    Resolution: pass the competitor id (not just the vendor name) to the new
    endpoint. If you only have a name, call POST /api/competitors first to
    create the competitor row.
    """
    raise HTTPException(
        status_code=410,
        detail={
            "message": "POST /api/orchestrator/start-vendor-analysis is retired.",
            "new_path": "/api/intelligence/competitors/{competitor_id}/intelligence-run",
            "note": "Pass competitor_id, not vendor_name, to the new endpoint.",
        },
    )


@router.get("/vendor-analysis/{analysis_id}")
def get_vendor_analysis(analysis_id: int, db: Session = Depends(get_db)):
    """Fetch a previously-run deep vendor analysis by id."""
    row = db.query(CompetitiveResearchData).filter(CompetitiveResearchData.id == analysis_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Analysis not found")
    import json as _json
    try:
        payload = _json.loads(row.data) if row.data else {}
    except Exception:
        # Legacy rows stored plain-text markdown; return as a minimal envelope.
        payload = {"synthesis_markdown": row.data or "", "legacy": True}
    return {"analysis_id": row.id, **payload}


@router.get("/vendor-analysis")
def list_vendor_analyses(limit: int = 20, db: Session = Depends(get_db)):
    """List recent vendor analyses (newest first, lightweight projection)."""
    rows = (
        db.query(CompetitiveResearchData)
        .order_by(CompetitiveResearchData.id.desc())
        .limit(max(1, min(limit, 100)))
        .all()
    )
    import json as _json
    out = []
    for r in rows:
        try:
            p = _json.loads(r.data) if r.data else {}
        except Exception:
            p = {"legacy": True}
        out.append({
            "id": r.id,
            "vendor_name": p.get("vendor_name"),
            "model": p.get("model"),
            "started_at": p.get("started_at"),
            "completed_at": p.get("completed_at"),
            "has_errors": bool(p.get("errors")),
            "citation_count": len(p.get("citations") or []),
        })
    return {"analyses": out}

@router.post("/refine-with-opus")
def refine_with_opus(content: str, conflicts: list = None, context: str = "", db: Session = Depends(get_db)):
    """
    Final refinement using Opus model to resolve conflicts
    """
    conflicts_text = ""
    if conflicts and len(conflicts) > 0:
        conflicts_text = "\n".join([f"- {c.get('description', 'Unknown conflict')}" for c in conflicts])

    prompt = f"""As an expert RFP response writer using advanced AI (Opus model), perform final refinement of the following content.
    Context: {context}

    Original Content:
    {content}

    {f'Conflicts to resolve: {conflicts_text}' if conflicts_text else 'No conflicts to resolve.'}

    Please provide the final refined version that:
    1. Resolves all identified conflicts
    2. Maintains professional tone and accuracy
    3. Optimizes for RFP evaluation criteria
    4. Ensures compliance and persuasiveness
    5. Is ready for submission

    Final refined content:"""

    result = ai_agent.run_agent(prompt)

    # Store result
    db_data = OrchestratorData(data=result)
    db.add(db_data)
    db.commit()
    db.refresh(db_data)

    return {
        "refined_content": result,
        "id": db_data.id,
        "conflicts_resolved": len(conflicts) if conflicts else 0,
        "context": context
    }