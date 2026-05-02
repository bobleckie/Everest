from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Dict, Any
from pydantic import BaseModel
import json

from ..database import get_db
from ..models import ParsonsData
from ..auth import get_current_user

router = APIRouter(prefix="/parsons", tags=["parsons"])

class ParsonsDataCreate(BaseModel):
    section: str
    data: Dict[str, Any]

class ParsonsDataResponse(BaseModel):
    id: int
    section: str
    data: Dict[str, Any]
    created_at: str
    updated_at: str

@router.post("/company-overview", response_model=ParsonsDataResponse)
async def save_company_overview(
    data: Dict[str, Any],
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Save company overview data"""
    try:
        # Check if company overview already exists
        existing = db.query(ParsonsData).filter(
            ParsonsData.section == "company-overview",
            ParsonsData.user_id == current_user["id"]
        ).first()

        if existing:
            # Update existing
            existing.data = json.dumps(data)
            db.commit()
            db.refresh(existing)
            return {
                "id": existing.id,
                "section": existing.section,
                "data": json.loads(existing.data),
                "created_at": existing.created_at.isoformat(),
                "updated_at": existing.updated_at.isoformat()
            }
        else:
            # Create new
            parsons_data = ParsonsData(
                section="company-overview",
                data=json.dumps(data),
                user_id=current_user["id"]
            )
            db.add(parsons_data)
            db.commit()
            db.refresh(parsons_data)
            return {
                "id": parsons_data.id,
                "section": parsons_data.section,
                "data": json.loads(parsons_data.data),
                "created_at": parsons_data.created_at.isoformat(),
                "updated_at": parsons_data.updated_at.isoformat()
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save company overview: {str(e)}")

@router.get("/company-overview")
async def get_company_overview(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Get company overview data"""
    try:
        parsons_data = db.query(ParsonsData).filter(
            ParsonsData.section == "company-overview",
            ParsonsData.user_id == current_user["id"]
        ).first()

        if parsons_data:
            return json.loads(parsons_data.data)
        else:
            return {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get company overview: {str(e)}")

@router.post("/{section}", response_model=ParsonsDataResponse)
async def save_parsons_data(
    section: str,
    data: Dict[str, Any],
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Save Parsons data for any section"""
    try:
        # Validate section
        valid_sections = [
            "technical-capabilities", "past-performance", "key-personnel",
            "facilities", "financial-info", "quality-management",
            "safety-compliance", "certifications", "subcontractors",
            "project-management", "data-validation"
        ]

        if section not in valid_sections:
            raise HTTPException(status_code=400, detail=f"Invalid section: {section}")

        # Check if section data already exists
        existing = db.query(ParsonsData).filter(
            ParsonsData.section == section,
            ParsonsData.user_id == current_user["id"]
        ).first()

        if existing:
            # Update existing
            existing.data = json.dumps(data)
            db.commit()
            db.refresh(existing)
            return {
                "id": existing.id,
                "section": existing.section,
                "data": json.loads(existing.data),
                "created_at": existing.created_at.isoformat(),
                "updated_at": existing.updated_at.isoformat()
            }
        else:
            # Create new
            parsons_data = ParsonsData(
                section=section,
                data=json.dumps(data),
                user_id=current_user["id"]
            )
            db.add(parsons_data)
            db.commit()
            db.refresh(parsons_data)
            return {
                "id": parsons_data.id,
                "section": parsons_data.section,
                "data": json.loads(parsons_data.data),
                "created_at": parsons_data.created_at.isoformat(),
                "updated_at": parsons_data.updated_at.isoformat()
            }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save Parsons data: {str(e)}")

@router.get("/{section}")
async def get_parsons_data(
    section: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Get Parsons data for any section"""
    try:
        parsons_data = db.query(ParsonsData).filter(
            ParsonsData.section == section,
            ParsonsData.user_id == current_user["id"]
        ).first()

        if parsons_data:
            return json.loads(parsons_data.data)
        else:
            return {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get Parsons data: {str(e)}")

@router.get("/")
async def get_all_parsons_data(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Get all Parsons data sections"""
    try:
        parsons_data = db.query(ParsonsData).filter(
            ParsonsData.user_id == current_user["id"]
        ).all()

        result = {}
        for item in parsons_data:
            result[item.section] = {
                "id": item.id,
                "data": json.loads(item.data),
                "created_at": item.created_at.isoformat(),
                "updated_at": item.updated_at.isoformat()
            }

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get Parsons data: {str(e)}")

@router.delete("/{section}")
async def delete_parsons_data(
    section: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Delete Parsons data for a section"""
    try:
        parsons_data = db.query(ParsonsData).filter(
            ParsonsData.section == section,
            ParsonsData.user_id == current_user["id"]
        ).first()

        if not parsons_data:
            raise HTTPException(status_code=404, detail=f"Parsons data for section '{section}' not found")

        db.delete(parsons_data)
        db.commit()

        return {"message": f"Parsons data for section '{section}' deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete Parsons data: {str(e)}")