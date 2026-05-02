from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import DocumentComposerData
from ..services import ai_agent, presentation_generation

router = APIRouter()

@router.get("/")
def get_document_composer():
    return {"message": "Document Composer endpoint"}

@router.post("/compose")
def compose_document(query: str, format: str = "pdf", db: Session = Depends(get_db)):
    content = ai_agent.document_composer(query)
    
    # Check for conflicts
    from ..services.ai_agent import conflict_detector
    conflicts = conflict_detector(content)
    if "conflict" in conflicts.lower():
        return {"error": "Conflicts detected", "details": conflicts}
    
    # Generate presentation
    if format == "pdf":
        file_data = presentation_generation.generate_pdf(content)
    elif format == "html":
        file_data = presentation_generation.generate_html(content).encode()
    elif format == "word":
        file_data = presentation_generation.generate_word(content)
    elif format == "ppt":
        file_data = presentation_generation.generate_ppt(content)
    else:
        return {"error": "Unsupported format"}
    
    # Store result
    db_data = DocumentComposerData(data=content)
    db.add(db_data)
    db.commit()
    db.refresh(db_data)
    
    return {"file": file_data, "id": db_data.id, "format": format}