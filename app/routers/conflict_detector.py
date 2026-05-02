from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import ConflictDetectorData
from ..services import ai_agent

router = APIRouter()

@router.get("/")
def get_conflict_detector():
    return {"message": "Conflict Detector endpoint"}

@router.post("/analyze")
def analyze_content(content: str, context: str = "", db: Session = Depends(get_db)):
    """
    Analyze content for conflicts and compliance issues
    """
    prompt = f"""Analyze the following content for potential conflicts, compliance issues, or problematic statements.
    Context: {context}

    Content to analyze:
    {content}

    Check for:
    1. Factual inaccuracies
    2. Overly aggressive claims
    3. Compliance violations
    4. Inappropriate language
    5. Conflicts with Parsons' values or policies
    6. Unrealistic commitments

    Return a JSON array of conflicts found, or empty array if none:
    [{"description": "conflict description", "severity": "high|medium|low", "category": "factual|compliance|language|other"}]
    """

    result = ai_agent.conflict_detector(prompt)

    # Try to parse as JSON, fallback to string processing
    try:
        conflicts = eval(result) if isinstance(result, str) else result
        if not isinstance(conflicts, list):
            conflicts = []
    except:
        # If not valid JSON, create a single conflict
        conflicts = [{"description": result, "severity": "medium", "category": "other"}] if result else []

    # Store result
    db_data = ConflictDetectorData(data=str(conflicts))
    db.add(db_data)
    db.commit()
    db.refresh(db_data)

    return {
        "conflicts": conflicts,
        "id": db_data.id,
        "context": context
    }