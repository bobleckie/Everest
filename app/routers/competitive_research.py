from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import CompetitiveResearchData
from ..services import ai_agent

router = APIRouter()

@router.get("/")
def get_competitive_research():
    return {"message": "Competitive Research Agent endpoint"}

@router.post("/analyze")
def analyze_market(query: str, db: Session = Depends(get_db)):
    result = ai_agent.competitive_research(query)
    
    # Store result
    db_data = CompetitiveResearchData(data=result)
    db.add(db_data)
    db.commit()
    db.refresh(db_data)
    
    return {"analysis": result, "id": db_data.id}