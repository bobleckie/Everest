from ..models import WorkflowData
from ..services import ai_agent, embeddings
from sqlalchemy.orm import Session

def start_rfp_workflow(db: Session, rfp_content: str, competitor_name: str):
    # Step 1: Extract requirements
    requirements = ai_agent.requirement_extractor(rfp_content)
    workflow_step = WorkflowData(workflow_type="rfp_response", step="extract_requirements", data=requirements, status="pending")
    db.add(workflow_step)
    db.commit()
    
    # Step 2: Competitive research
    competitor_analysis = ai_agent.competitive_research(f"Analyze competitor: {competitor_name}")
    workflow_step = WorkflowData(workflow_type="rfp_response", step="competitive_analysis", data=competitor_analysis, status="pending")
    db.add(workflow_step)
    db.commit()
    
    # Step 3: Draft competitor response
    competitor_response = ai_agent.document_composer(f"Draft RFP response for competitor {competitor_name} based on their capabilities")
    workflow_step = WorkflowData(workflow_type="rfp_response", step="competitor_response", data=competitor_response, status="pending")
    db.add(workflow_step)
    db.commit()
    
    # Step 4: Parsons SME Q&A (placeholder - would need interactive session)
    # For now, generate SME input
    sme_input = ai_agent.parsons_sme(f"Provide Parsons response strategy for requirements: {requirements}")
    workflow_step = WorkflowData(workflow_type="rfp_response", step="parsons_sme", data=sme_input, status="pending")
    db.add(workflow_step)
    db.commit()
    
    # Step 5: Compose Parsons response
    parsons_response = ai_agent.document_composer(f"Compose Parsons RFP response based on requirements: {requirements} and SME input: {sme_input}")
    workflow_step = WorkflowData(workflow_type="rfp_response", step="parsons_response", data=parsons_response, status="pending")
    db.add(workflow_step)
    db.commit()
    
    # Step 6: Conflict detection
    conflicts = ai_agent.conflict_detector(parsons_response)
    workflow_step = WorkflowData(workflow_type="rfp_response", step="conflict_check", data=conflicts, status="completed" if "no conflicts" in conflicts.lower() else "pending")
    db.add(workflow_step)
    db.commit()
    
    return {"workflow_id": workflow_step.id, "steps": ["extract_requirements", "competitive_analysis", "competitor_response", "parsons_sme", "parsons_response", "conflict_check"]}

def get_workflow_status(db: Session, workflow_id: int):
    steps = db.query(WorkflowData).filter(WorkflowData.id >= workflow_id - 5, WorkflowData.id <= workflow_id).all()
    return [{"step": s.step, "status": s.status, "data": s.data[:200]} for s in steps]

def approve_step(db: Session, step_id: int, approval_level: int):
    step = db.query(WorkflowData).filter(WorkflowData.id == step_id).first()
    if step:
        step.approval_level = approval_level
        if approval_level >= 2:  # Assuming 2 levels needed
            step.status = "approved"
        db.commit()
    return step