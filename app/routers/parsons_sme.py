from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import ParsonsSMEData
from ..services import ai_agent

router = APIRouter()

@router.get("/")
def get_parsons_sme():
    return {"message": "Parsons SME endpoint"}

@router.post("/query")
def query_parsons(query: str, db: Session = Depends(get_db)):
    result = ai_agent.parsons_sme(query)
    
    # Store result
    db_data = ParsonsSMEData(data=result)
    db.add(db_data)
    db.commit()
    db.refresh(db_data)
    
@router.post("/refine-content")
def refine_content(content: str, context: str = "", db: Session = Depends(get_db)):
    """
    Refine Parsons content using SME expertise (Claude 4.6 equivalent)
    """
    prompt = f"""As a Parsons subject matter expert, refine and enhance the following content for an RFP response.
    Context: {context}

    Original Content:
    {content}

    Please refine this content to:
    1. Ensure accuracy and completeness
    2. Use professional language appropriate for government RFPs
    3. Highlight Parsons' competitive advantages
    4. Ensure compliance with RFP requirements
    5. Make it compelling and persuasive

    Provide the refined content:"""

    result = ai_agent.parsons_sme(prompt)

    # Store result
    db_data = ParsonsSMEData(data=result)
    db.add(db_data)
    db.commit()
    db.refresh(db_data)

    return {
        "refined_content": result,
        "id": db_data.id,
        "context": context
    }