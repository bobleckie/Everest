from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import SessionLocal, get_db
from ..models import RequirementExtractorData, User
from ..services import ai_agent
from ..services.requirement_classifier import (
    class_summary,
    classify_heuristic,
    classify_llm_batch,
)

router = APIRouter()


@router.get("/")
def get_requirement_extractor():
    return {"message": "Requirement Extractor endpoint"}


@router.post("/extract")
def extract_requirements(content: str, db: Session = Depends(get_db)):
    result = ai_agent.requirement_extractor(content)

    # Store result
    db_data = RequirementExtractorData(data=result)
    db.add(db_data)
    db.commit()
    db.refresh(db_data)

    return {"requirements": result, "id": db_data.id}


# ── Classifier endpoints ─────────────────────────────────────────────

class ClassifyIn(BaseModel):
    proposal_id: int
    heuristic_only: bool = Field(False, description="Skip the LLM pass.")
    llm_only: bool = Field(False, description="Skip the heuristic pass.")
    reset: bool = Field(False, description="Clear existing classifications first.")
    limit: Optional[int] = Field(None, description="Cap LLM-pass rows for smoke tests.")


def _run_classify_bg(body: Dict[str, Any]) -> None:
    """Background runner — uses its own Session."""
    import logging
    log = logging.getLogger(__name__)
    db = SessionLocal()
    try:
        if not body.get("llm_only"):
            r1 = classify_heuristic(
                db,
                proposal_id=body["proposal_id"],
                reset=body.get("reset", False),
                dry_run=False,
            )
            log.info(f"classifier (heuristic) result: {r1}")
        if not body.get("heuristic_only"):
            r2 = classify_llm_batch(
                db,
                proposal_id=body["proposal_id"],
                limit=body.get("limit"),
                dry_run=False,
            )
            log.info(f"classifier (llm) result: {r2}")
    except Exception as e:
        log.exception(f"Background classifier failed: {e}")
    finally:
        db.close()


@router.post("/classify")
def classify_requirements(
    body: ClassifyIn,
    background: BackgroundTasks,
    _user: User = Depends(get_current_user),
):
    """Kick off requirement classification in the background.
    Poll GET /requirement_extractor/classes?proposal_id=X for progress."""
    background.add_task(_run_classify_bg, body.model_dump())
    return {"status": "queued", "message": "Classifier running in background."}


@router.get("/classes")
def get_class_summary(
    proposal_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Return requirement_class distribution for the proposal."""
    return class_summary(db, proposal_id)