"""
Persona management API endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from typing import List, Optional
from ..database import get_db
from ..models import Persona, User
from ..auth import get_current_active_user, get_current_admin_user
from ..exceptions import ResourceNotFoundError, AuthorizationError, DatabaseError, ValidationError
from ..logging_config import logger
from datetime import datetime
import json

router = APIRouter()


@router.post("/create")
def create_persona(
    name: str,
    description: str,
    role: str,
    expertise_areas: List[str],
    writing_style: dict,
    tone: str = "professional",
    audience: str = "internal",
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Create a new persona for prompt optimization."""
    try:
        # Validate input
        if not name or not name.strip():
            raise ValidationError("Persona name is required")
        if not description or not description.strip():
            raise ValidationError("Persona description is required")
        if not role or not role.strip():
            raise ValidationError("Persona role is required")

        # Check if persona name already exists
        existing = db.query(Persona).filter(Persona.name == name.strip()).first()
        if existing:
            raise ValidationError("Persona with this name already exists")

        logger.info(f"Creating persona '{name}' by user {current_user.username}")

        # Validate expertise_areas and writing_style are valid JSON
        try:
            json.dumps(expertise_areas)
            json.dumps(writing_style)
        except (TypeError, ValueError):
            raise ValidationError("Invalid JSON format for expertise areas or writing style")

        persona = Persona(
            name=name.strip(),
            description=description.strip(),
            role=role.strip(),
            expertise_areas=json.dumps(expertise_areas),
            writing_style=json.dumps(writing_style),
            tone=tone,
            audience=audience,
            created_by=current_user.id
        )

        db.add(persona)
        db.commit()
        db.refresh(persona)

        logger.info(f"Persona '{name}' created successfully with ID {persona.id}")
        return {
            "message": "Persona created successfully",
            "persona_id": persona.id,
            "persona": {
                "id": persona.id,
                "name": persona.name,
                "description": persona.description,
                "role": persona.role,
                "expertise_areas": json.loads(persona.expertise_areas),
                "writing_style": json.loads(persona.writing_style),
                "tone": persona.tone,
                "audience": persona.audience,
                "created_by": persona.created_by,
                "is_active": persona.is_active,
                "usage_count": persona.usage_count,
                "created_at": persona.created_at
            }
        }

    except ValidationError:
        raise
    except SQLAlchemyError as e:
        db.rollback()
        logger.error(f"Database error creating persona: {str(e)}")
        raise DatabaseError("Failed to create persona")
    except Exception as e:
        db.rollback()
        logger.error(f"Unexpected error creating persona: {str(e)}")
        raise DatabaseError("Failed to create persona")


@router.get("/")
def list_personas(
    active_only: bool = True,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """List all personas."""
    try:
        logger.info(f"Listing personas for user {current_user.username}")

        query = db.query(Persona)
        if active_only:
            query = query.filter(Persona.is_active == True)

        personas = query.all()

        result = []
        for persona in personas:
            result.append({
                "id": persona.id,
                "name": persona.name,
                "description": persona.description,
                "role": persona.role,
                "expertise_areas": json.loads(persona.expertise_areas) if persona.expertise_areas else [],
                "writing_style": json.loads(persona.writing_style) if persona.writing_style else {},
                "tone": persona.tone,
                "audience": persona.audience,
                "created_by": persona.created_by,
                "is_active": persona.is_active,
                "usage_count": persona.usage_count,
                "created_at": persona.created_at,
                "updated_at": persona.updated_at
            })

        logger.info(f"Retrieved {len(result)} personas")
        return {"personas": result}

    except SQLAlchemyError as e:
        logger.error(f"Database error listing personas: {str(e)}")
        raise DatabaseError("Failed to retrieve personas")
    except Exception as e:
        logger.error(f"Unexpected error listing personas: {str(e)}")
        raise DatabaseError("Failed to retrieve personas")


@router.get("/{persona_id}")
def get_persona(
    persona_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get a specific persona by ID."""
    try:
        logger.info(f"Fetching persona {persona_id} for user {current_user.username}")

        persona = db.query(Persona).filter(Persona.id == persona_id).first()
        if not persona:
            logger.warning(f"Persona not found: {persona_id}")
            raise ResourceNotFoundError("Persona", persona_id)

        logger.info(f"Persona {persona_id} retrieved successfully")
        return {
            "persona": {
                "id": persona.id,
                "name": persona.name,
                "description": persona.description,
                "role": persona.role,
                "expertise_areas": json.loads(persona.expertise_areas) if persona.expertise_areas else [],
                "writing_style": json.loads(persona.writing_style) if persona.writing_style else {},
                "tone": persona.tone,
                "audience": persona.audience,
                "created_by": persona.created_by,
                "is_active": persona.is_active,
                "usage_count": persona.usage_count,
                "created_at": persona.created_at,
                "updated_at": persona.updated_at
            }
        }

    except ResourceNotFoundError:
        raise
    except SQLAlchemyError as e:
        logger.error(f"Database error fetching persona: {str(e)}")
        raise DatabaseError("Failed to retrieve persona")
    except Exception as e:
        logger.error(f"Unexpected error fetching persona: {str(e)}")
        raise DatabaseError("Failed to retrieve persona")


@router.put("/{persona_id}")
def update_persona(
    persona_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    role: Optional[str] = None,
    expertise_areas: Optional[List[str]] = None,
    writing_style: Optional[dict] = None,
    tone: Optional[str] = None,
    audience: Optional[str] = None,
    is_active: Optional[bool] = None,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Update a persona."""
    try:
        logger.info(f"Updating persona {persona_id} by user {current_user.username}")

        persona = db.query(Persona).filter(Persona.id == persona_id).first()
        if not persona:
            logger.warning(f"Persona not found: {persona_id}")
            raise ResourceNotFoundError("Persona", persona_id)

        # Check permissions - only creator or admin can update
        if persona.created_by != current_user.id and current_user.role != "admin":
            logger.warning(f"Access denied for persona {persona_id} by user {current_user.username}")
            raise AuthorizationError("Not authorized to update this persona")

        # Validate name uniqueness if being changed
        if name and name.strip() != persona.name:
            existing = db.query(Persona).filter(Persona.name == name.strip(), Persona.id != persona_id).first()
            if existing:
                raise ValidationError("Persona with this name already exists")

        # Update fields
        if name is not None:
            if not name.strip():
                raise ValidationError("Persona name cannot be empty")
            persona.name = name.strip()

        if description is not None:
            if not description.strip():
                raise ValidationError("Persona description cannot be empty")
            persona.description = description.strip()

        if role is not None:
            if not role.strip():
                raise ValidationError("Persona role cannot be empty")
            persona.role = role.strip()

        if expertise_areas is not None:
            try:
                json.dumps(expertise_areas)
                persona.expertise_areas = json.dumps(expertise_areas)
            except (TypeError, ValueError):
                raise ValidationError("Invalid JSON format for expertise areas")

        if writing_style is not None:
            try:
                json.dumps(writing_style)
                persona.writing_style = json.dumps(writing_style)
            except (TypeError, ValueError):
                raise ValidationError("Invalid JSON format for writing style")

        if tone is not None:
            persona.tone = tone
        if audience is not None:
            persona.audience = audience
        if is_active is not None:
            persona.is_active = is_active

        persona.updated_at = datetime.utcnow()
        db.commit()

        logger.info(f"Persona {persona_id} updated successfully")
        return {"message": "Persona updated successfully"}

    except (ResourceNotFoundError, AuthorizationError, ValidationError):
        raise
    except SQLAlchemyError as e:
        db.rollback()
        logger.error(f"Database error updating persona: {str(e)}")
        raise DatabaseError("Failed to update persona")
    except Exception as e:
        db.rollback()
        logger.error(f"Unexpected error updating persona: {str(e)}")
        raise DatabaseError("Failed to update persona")


@router.delete("/{persona_id}")
def delete_persona(
    persona_id: int,
    current_user: User = Depends(get_current_admin_user),  # Only admins can delete
    db: Session = Depends(get_db)
):
    """Delete a persona (admin only)."""
    try:
        logger.info(f"Deleting persona {persona_id} by admin {current_user.username}")

        persona = db.query(Persona).filter(Persona.id == persona_id).first()
        if not persona:
            logger.warning(f"Persona not found: {persona_id}")
            raise ResourceNotFoundError("Persona", persona_id)

        # Check if persona is being used by any prompt templates
        from ..models import PromptTemplate
        usage_count = db.query(PromptTemplate).filter(PromptTemplate.persona_id == persona_id).count()
        if usage_count > 0:
            raise ValidationError(f"Cannot delete persona that is used by {usage_count} prompt template(s)")

        db.delete(persona)
        db.commit()

        logger.info(f"Persona {persona_id} deleted successfully")
        return {"message": "Persona deleted successfully"}

    except (ResourceNotFoundError, ValidationError):
        raise
    except SQLAlchemyError as e:
        db.rollback()
        logger.error(f"Database error deleting persona: {str(e)}")
        raise DatabaseError("Failed to delete persona")
    except Exception as e:
        db.rollback()
        logger.error(f"Unexpected error deleting persona: {str(e)}")
        raise DatabaseError("Failed to delete persona")