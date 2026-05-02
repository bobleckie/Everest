"""
Prompt template management API endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from typing import List, Optional, Dict, Any
import os
from ..database import get_db
from ..models import PromptTemplate, PromptVersion, Persona, User
from ..auth import get_current_active_user, get_current_admin_user
from ..exceptions import ResourceNotFoundError, AuthorizationError, DatabaseError, ValidationError
from ..logging_config import logger
from datetime import datetime
import json

router = APIRouter()


@router.post("/create")
def create_prompt_template(
    name: str,
    description: str,
    template_type: str,
    template_content: str,
    variables: List[str],
    persona_id: Optional[int] = None,
    model_provider: str = "openai",
    model_name: str = "gpt-4",
    temperature: float = 0.7,
    max_tokens: int = 2000,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Create a new prompt template."""
    try:
        # Validate input
        if not name or not name.strip():
            raise ValidationError("Template name is required")
        if not description or not description.strip():
            raise ValidationError("Template description is required")
        if not template_content or not template_content.strip():
            raise ValidationError("Template content is required")

        # Check if template name already exists
        existing = db.query(PromptTemplate).filter(PromptTemplate.name == name.strip()).first()
        if existing:
            raise ValidationError("Prompt template with this name already exists")

        # Validate persona exists if provided
        if persona_id:
            persona = db.query(Persona).filter(Persona.id == persona_id, Persona.is_active == True).first()
            if not persona:
                raise ValidationError("Invalid or inactive persona")

        # Validate model parameters
        if not (0 <= temperature <= 2):
            raise ValidationError("Temperature must be between 0 and 2")
        if max_tokens <= 0:
            raise ValidationError("Max tokens must be positive")

        logger.info(f"Creating prompt template '{name}' by user {current_user.username}")

        # Validate variables is valid JSON
        try:
            json.dumps(variables)
        except (TypeError, ValueError):
            raise ValidationError("Invalid JSON format for variables")

        template = PromptTemplate(
            name=name.strip(),
            description=description.strip(),
            template_type=template_type,
            template_content=template_content.strip(),
            variables=json.dumps(variables),
            persona_id=persona_id,
            model_provider=model_provider,
            model_name=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            created_by=current_user.id
        )

        db.add(template)
        db.commit()
        db.refresh(template)

        # Create initial version
        version = PromptVersion(
            prompt_template_id=template.id,
            version_number=1,
            template_content=template.template_content,
            variables=template.variables,
            model_provider=template.model_provider,
            model_name=template.model_name,
            temperature=template.temperature,
            max_tokens=template.max_tokens,
            change_reason="Initial version",
            created_by=current_user.id
        )
        db.add(version)
        db.commit()

        logger.info(f"Prompt template '{name}' created successfully with ID {template.id}")
        return {
            "message": "Prompt template created successfully",
            "template_id": template.id,
            "template": {
                "id": template.id,
                "name": template.name,
                "description": template.description,
                "template_type": template.template_type,
                "template_content": template.template_content,
                "variables": json.loads(template.variables),
                "persona_id": template.persona_id,
                "model_provider": template.model_provider,
                "model_name": template.model_name,
                "temperature": template.temperature,
                "max_tokens": template.max_tokens,
                "created_by": template.created_by,
                "is_active": template.is_active,
                "usage_count": template.usage_count,
                "created_at": template.created_at
            }
        }

    except ValidationError:
        raise
    except SQLAlchemyError as e:
        db.rollback()
        logger.error(f"Database error creating prompt template: {str(e)}")
        raise DatabaseError("Failed to create prompt template")
    except Exception as e:
        db.rollback()
        logger.error(f"Unexpected error creating prompt template: {str(e)}")
        raise DatabaseError("Failed to create prompt template")


@router.get("/")
def list_prompt_templates(
    template_type: Optional[str] = None,
    active_only: bool = True,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """List prompt templates with optional filtering."""
    try:
        logger.info(f"Listing prompt templates for user {current_user.username}")

        query = db.query(PromptTemplate)
        if active_only:
            query = query.filter(PromptTemplate.is_active == True)
        if template_type:
            query = query.filter(PromptTemplate.template_type == template_type)

        templates = query.all()

        result = []
        for template in templates:
            # Get persona info if available
            persona_info = None
            if template.persona_id:
                persona = db.query(Persona).filter(Persona.id == template.persona_id).first()
                if persona:
                    persona_info = {
                        "id": persona.id,
                        "name": persona.name,
                        "role": persona.role
                    }

            result.append({
                "id": template.id,
                "name": template.name,
                "description": template.description,
                "template_type": template.template_type,
                "template_content": template.template_content,
                "refined_content": getattr(template, "refined_content", None),
                "refined_at": getattr(template, "refined_at", None),
                "refined_by_model": getattr(template, "refined_by_model", None),
                "variables": json.loads(template.variables) if template.variables else [],
                "persona": persona_info,
                "model_provider": template.model_provider,
                "model_name": template.model_name,
                "temperature": template.temperature,
                "max_tokens": template.max_tokens,
                "created_by": template.created_by,
                "is_active": template.is_active,
                "usage_count": template.usage_count,
                "success_rate": template.success_rate,
                "created_at": template.created_at,
                "updated_at": template.updated_at
            })

        logger.info(f"Retrieved {len(result)} prompt templates")
        return {"templates": result}

    except SQLAlchemyError as e:
        logger.error(f"Database error listing prompt templates: {str(e)}")
        raise DatabaseError("Failed to retrieve prompt templates")
    except Exception as e:
        logger.error(f"Unexpected error listing prompt templates: {str(e)}")
        raise DatabaseError("Failed to retrieve prompt templates")


@router.get("/{template_id}")
def get_prompt_template(
    template_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get a specific prompt template by ID."""
    try:
        logger.info(f"Fetching prompt template {template_id} for user {current_user.username}")

        template = db.query(PromptTemplate).filter(PromptTemplate.id == template_id).first()
        if not template:
            logger.warning(f"Prompt template not found: {template_id}")
            raise ResourceNotFoundError("Prompt template", template_id)

        # Get persona info if available
        persona_info = None
        if template.persona_id:
            persona = db.query(Persona).filter(Persona.id == template.persona_id).first()
            if persona:
                persona_info = {
                    "id": persona.id,
                    "name": persona.name,
                    "description": persona.description,
                    "role": persona.role,
                    "expertise_areas": json.loads(persona.expertise_areas) if persona.expertise_areas else [],
                    "writing_style": json.loads(persona.writing_style) if persona.writing_style else {},
                    "tone": persona.tone,
                    "audience": persona.audience
                }

        # Get version history
        versions = db.query(PromptVersion).filter(
            PromptVersion.prompt_template_id == template_id
        ).order_by(PromptVersion.version_number.desc()).all()

        version_history = []
        for version in versions:
            version_history.append({
                "version_number": version.version_number,
                "change_reason": version.change_reason,
                "model_provider": version.model_provider,
                "model_name": version.model_name,
                "temperature": version.temperature,
                "max_tokens": version.max_tokens,
                "created_by": version.created_by,
                "created_at": version.created_at
            })

        logger.info(f"Prompt template {template_id} retrieved successfully")
        return {
            "template": {
                "id": template.id,
                "name": template.name,
                "description": template.description,
                "template_type": template.template_type,
                "template_content": template.template_content,
                "variables": json.loads(template.variables) if template.variables else [],
                "persona": persona_info,
                "model_provider": template.model_provider,
                "model_name": template.model_name,
                "temperature": template.temperature,
                "max_tokens": template.max_tokens,
                "created_by": template.created_by,
                "is_active": template.is_active,
                "usage_count": template.usage_count,
                "success_rate": template.success_rate,
                "created_at": template.created_at,
                "updated_at": template.updated_at,
                "version_history": version_history
            }
        }

    except ResourceNotFoundError:
        raise
    except SQLAlchemyError as e:
        logger.error(f"Database error fetching prompt template: {str(e)}")
        raise DatabaseError("Failed to retrieve prompt template")
    except Exception as e:
        logger.error(f"Unexpected error fetching prompt template: {str(e)}")
        raise DatabaseError("Failed to retrieve prompt template")


@router.put("/{template_id}")
def update_prompt_template(
    template_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    template_type: Optional[str] = None,
    template_content: Optional[str] = None,
    variables: Optional[List[str]] = None,
    persona_id: Optional[int] = None,
    model_provider: Optional[str] = None,
    model_name: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    is_active: Optional[bool] = None,
    change_reason: str = "Updated template",
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Update a prompt template and create a new version."""
    try:
        logger.info(f"Updating prompt template {template_id} by user {current_user.username}")

        template = db.query(PromptTemplate).filter(PromptTemplate.id == template_id).first()
        if not template:
            logger.warning(f"Prompt template not found: {template_id}")
            raise ResourceNotFoundError("Prompt template", template_id)

        # Check permissions - only creator or admin can update
        if template.created_by != current_user.id and current_user.role != "admin":
            logger.warning(f"Access denied for prompt template {template_id} by user {current_user.username}")
            raise AuthorizationError("Not authorized to update this prompt template")

        # Validate persona exists if provided
        if persona_id is not None:
            if persona_id:  # Allow setting to None to remove persona
                persona = db.query(Persona).filter(Persona.id == persona_id, Persona.is_active == True).first()
                if not persona:
                    raise ValidationError("Invalid or inactive persona")

        # Validate name uniqueness if being changed
        if name and name.strip() != template.name:
            existing = db.query(PromptTemplate).filter(
                PromptTemplate.name == name.strip(),
                PromptTemplate.id != template_id
            ).first()
            if existing:
                raise ValidationError("Prompt template with this name already exists")

        # Validate parameters
        if temperature is not None and not (0 <= temperature <= 2):
            raise ValidationError("Temperature must be between 0 and 2")
        if max_tokens is not None and max_tokens <= 0:
            raise ValidationError("Max tokens must be positive")

        # Get current version number
        current_version = db.query(PromptVersion).filter(
            PromptVersion.prompt_template_id == template_id
        ).order_by(PromptVersion.version_number.desc()).first()

        version_number = current_version.version_number + 1 if current_version else 1

        # Create new version before updating
        version = PromptVersion(
            prompt_template_id=template.id,
            version_number=version_number,
            template_content=template.template_content,
            variables=template.variables,
            model_provider=template.model_provider,
            model_name=template.model_name,
            temperature=template.temperature,
            max_tokens=template.max_tokens,
            change_reason=change_reason,
            created_by=current_user.id
        )
        db.add(version)

        # Update template fields
        if name is not None:
            if not name.strip():
                raise ValidationError("Template name cannot be empty")
            template.name = name.strip()

        if description is not None:
            if not description.strip():
                raise ValidationError("Template description cannot be empty")
            template.description = description.strip()

        if template_type is not None:
            template.template_type = template_type

        if template_content is not None:
            if not template_content.strip():
                raise ValidationError("Template content cannot be empty")
            template.template_content = template_content.strip()

        if variables is not None:
            try:
                json.dumps(variables)
                template.variables = json.dumps(variables)
            except (TypeError, ValueError):
                raise ValidationError("Invalid JSON format for variables")

        if persona_id is not None:
            template.persona_id = persona_id

        if model_provider is not None:
            template.model_provider = model_provider
        if model_name is not None:
            template.model_name = model_name
        if temperature is not None:
            template.temperature = temperature
        if max_tokens is not None:
            template.max_tokens = max_tokens
        if is_active is not None:
            template.is_active = is_active

        template.updated_at = datetime.utcnow()
        db.commit()

        logger.info(f"Prompt template {template_id} updated successfully (version {version_number})")
        return {
            "message": "Prompt template updated successfully",
            "new_version": version_number
        }

    except (ResourceNotFoundError, AuthorizationError, ValidationError):
        raise
    except SQLAlchemyError as e:
        db.rollback()
        logger.error(f"Database error updating prompt template: {str(e)}")
        raise DatabaseError("Failed to update prompt template")
    except Exception as e:
        db.rollback()
        logger.error(f"Unexpected error updating prompt template: {str(e)}")
        raise DatabaseError("Failed to update prompt template")


@router.delete("/{template_id}")
def delete_prompt_template(
    template_id: int,
    current_user: User = Depends(get_current_admin_user),  # Only admins can delete
    db: Session = Depends(get_db)
):
    """Delete a prompt template (admin only)."""
    try:
        logger.info(f"Deleting prompt template {template_id} by admin {current_user.username}")

        template = db.query(PromptTemplate).filter(PromptTemplate.id == template_id).first()
        if not template:
            logger.warning(f"Prompt template not found: {template_id}")
            raise ResourceNotFoundError("Prompt template", template_id)

        # Check if template has executions
        from ..models import PromptExecution
        execution_count = db.query(PromptExecution).filter(PromptExecution.prompt_template_id == template_id).count()
        if execution_count > 0:
            raise ValidationError(f"Cannot delete template that has been executed {execution_count} time(s)")

        # Delete associated versions first
        db.query(PromptVersion).filter(PromptVersion.prompt_template_id == template_id).delete()

        # Delete template
        db.delete(template)
        db.commit()

        logger.info(f"Prompt template {template_id} deleted successfully")
        return {"message": "Prompt template deleted successfully"}

    except ValidationError:
        raise
    except SQLAlchemyError as e:
        db.rollback()
        logger.error(f"Database error deleting prompt template: {str(e)}")
        raise DatabaseError("Failed to delete prompt template")
    except Exception as e:
        db.rollback()
        logger.error(f"Unexpected error deleting prompt template: {str(e)}")
        raise DatabaseError("Failed to delete prompt template")


# ---------------------------------------------------------------------------
# AI-refine a prompt for a specific target model
# ---------------------------------------------------------------------------

CLAUDE_REFINER_MODEL = os.getenv("CLAUDE_REFINER_MODEL", "claude-sonnet-4-5-20250929")


class ExternalServiceUnavailable(Exception):
    """Raised when no prompt refiner provider is available."""


def _get_app_setting(key: str) -> Optional[str]:
    """Load a single value from the app_settings table. Returns None on any error."""
    try:
        from ..database import SessionLocal
        from ..models import AppSetting
        import json as _json
        s = SessionLocal()
        try:
            row = s.query(AppSetting).filter(AppSetting.key == key).first()
            if not row or not row.value_json:
                return None
            try:
                val = _json.loads(row.value_json)
            except (ValueError, TypeError):
                val = row.value_json
            return val if isinstance(val, str) else None
        finally:
            s.close()
    except Exception:
        return None


def _resolve_api_key(env_var: str, setting_key: str) -> Optional[str]:
    """Prefer DB-stored app setting, fall back to environment variable."""
    val = _get_app_setting(setting_key)
    if val:
        return val
    env_val = os.getenv(env_var)
    return env_val or None


def _resolved_claude_model() -> str:
    val = _get_app_setting("refiner_model")
    if val and val.startswith("claude"):
        return val
    return CLAUDE_REFINER_MODEL


def _resolved_openai_model() -> str:
    val = _get_app_setting("refiner_model")
    if val and not val.startswith("claude"):
        return val
    return os.getenv("OPENAI_REFINER_MODEL", "gpt-4o")


class RefinePromptRequest(BaseModel):
    prompt: str
    target_provider: Optional[str] = None  # e.g. "openai", "anthropic"
    target_model: Optional[str] = None     # e.g. "gpt-4", "claude-3-5-sonnet"
    persona_name: Optional[str] = None
    persona_description: Optional[str] = None


class RefinePromptResponse(BaseModel):
    refined_prompt: str
    refiner_model: str
    target_model: Optional[str] = None
    notes: Optional[str] = None


def _build_refiner_system_prompt(target_provider: Optional[str], target_model: Optional[str]) -> str:
    target_desc = ""
    if target_provider or target_model:
        parts = [p for p in (target_provider, target_model) if p]
        target_desc = f" The refined prompt will be executed against: **{' / '.join(parts)}**."
    return (
        "You are an expert prompt engineer. You rewrite user-supplied prompts so that "
        "they are clearer, more specific, and tuned for the exact target model they "
        "will run against." + target_desc + "\n\n"
        "Rules:\n"
        "1. Preserve the user's original intent exactly — do not invent new requirements.\n"
        "2. Preserve all template placeholders of the form {variable_name}.\n"
        "3. Produce a prompt that is self-contained and unambiguous.\n"
        "4. Favor the conventions of the target model (OpenAI chat-completions prefer a concise system-style directive; Anthropic Claude prefers explicit step-by-step guidance and XML-style tags like <context> and <task>).\n"
        "5. Return ONLY the refined prompt. Do not include explanations, preambles, or code fences."
    )


async def _call_claude_refiner(system: str, user_prompt: str) -> str:
    """Call Anthropic Claude to refine a prompt. Raises if unavailable."""
    api_key = _resolve_api_key("ANTHROPIC_API_KEY", "anthropic_api_key")
    if not api_key:
        raise ExternalServiceUnavailable("Anthropic API key not configured (add it in Settings → AI / Models)")
    try:
        import anthropic  # type: ignore
    except ImportError as e:
        raise ExternalServiceUnavailable(
            "anthropic package not installed. Run: pip install anthropic"
        ) from e
    from ..services.ssl_utils import make_sync_httpx_client
    client = anthropic.Anthropic(
        api_key=api_key,
        http_client=make_sync_httpx_client(timeout=120.0),
    )
    model = _resolved_claude_model()
    from ..services import llm_usage as _usage
    import time as _time
    _t0 = _time.time()
    try:
        msg = client.messages.create(
            model=model,
            max_tokens=2000,
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as e:  # network error, auth error, rate limit, etc.
        _usage.record(
            provider="anthropic", model=model,
            latency_ms=_usage.time_block_ms(_t0),
            status="error", error=f"{type(e).__name__}: {e}",
        )
        raise ExternalServiceUnavailable(f"Claude call failed ({model}): {e}") from e
    in_tok, out_tok = _usage.extract_anthropic_usage(msg)
    _usage.record(
        provider="anthropic", model=model,
        input_tokens=in_tok, output_tokens=out_tok,
        latency_ms=_usage.time_block_ms(_t0),
        endpoint="prompt_refiner",
    )
    # Collect text content blocks
    parts = []
    for block in msg.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    refined = "\n".join(parts).strip()
    if not refined:
        raise ExternalServiceUnavailable("Claude returned empty content")
    return refined


async def _call_openai_refiner(system: str, user_prompt: str) -> str:
    """Fallback: use OpenAI to refine the prompt if Claude is not available."""
    api_key = _resolve_api_key("OPENAI_API_KEY", "openai_api_key")
    if not api_key:
        raise ExternalServiceUnavailable("No prompt-refiner configured (add a Claude or OpenAI key in Settings → AI / Models)")
    try:
        import openai  # type: ignore
    except ImportError as e:
        raise ExternalServiceUnavailable("openai package not installed") from e
    from ..services.ssl_utils import make_async_httpx_client
    from ..services import llm_usage as _usage
    import time as _time
    model = _resolved_openai_model()
    _t0 = _time.time()
    try:
        async with make_async_httpx_client(timeout=120.0) as http_client:
            client = openai.AsyncOpenAI(api_key=api_key, http_client=http_client)
            resp = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=2000,
                timeout=60,
            )
            in_tok, out_tok = _usage.extract_openai_usage(resp)
            _usage.record(
                provider="openai", model=model,
                input_tokens=in_tok, output_tokens=out_tok,
                latency_ms=_usage.time_block_ms(_t0),
                endpoint="prompt_refiner",
            )
            return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        _usage.record(
            provider="openai", model=model,
            latency_ms=_usage.time_block_ms(_t0),
            status="error", error=f"{type(e).__name__}: {e}",
        )
        raise ExternalServiceUnavailable(f"OpenAI call failed ({model}): {e}") from e


# NOTE: Mock/offline refinement fallback was removed. If no AI provider
# is reachable, the endpoint returns an error. Displaying fabricated
# "refined" content is not acceptable.


@router.post("/refine", response_model=RefinePromptResponse)
async def refine_prompt(
    body: RefinePromptRequest,
    current_user: User = Depends(get_current_active_user),
):
    """Refine a raw prompt using Claude (preferred) or OpenAI as fallback.

    The refined prompt is returned but NOT persisted. Clients that want to
    save it against a stored template should call `/prompts/{id}/refine`.
    """
    raw = (body.prompt or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="prompt is required")

    system = _build_refiner_system_prompt(body.target_provider, body.target_model)
    user_msg = raw
    if body.persona_name or body.persona_description:
        persona_bits = []
        if body.persona_name:
            persona_bits.append(f"Persona name: {body.persona_name}")
        if body.persona_description:
            persona_bits.append(f"Persona description: {body.persona_description}")
        user_msg = "The prompt will be executed as this persona:\n" + "\n".join(persona_bits) + "\n\nOriginal prompt:\n" + raw

    # Try Claude first, fall back to OpenAI, then to a deterministic mock so
    # the UI remains usable when no provider is reachable (e.g. offline demo).
    refiner_model = _resolved_claude_model()
    notes = None
    try:
        refined = await _call_claude_refiner(system, user_msg)
    except ExternalServiceUnavailable as claude_err:
        logger.warning(f"Claude refiner unavailable ({claude_err}); falling back to OpenAI")
        try:
            refined = await _call_openai_refiner(system, user_msg)
            refiner_model = _resolved_openai_model()
            notes = f"Claude unavailable ({claude_err}); used OpenAI fallback."
        except ExternalServiceUnavailable as openai_err:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"No AI provider is reachable. "
                    f"Claude: {claude_err}. OpenAI: {openai_err}. "
                    f"Check Settings → AI Configuration."
                ),
            )

    return RefinePromptResponse(
        refined_prompt=refined,
        refiner_model=refiner_model,
        target_model=body.target_model,
        notes=notes,
    )


@router.post("/{template_id}/refine")
async def refine_and_save_prompt(
    template_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Refine a stored template's prompt content and persist the result."""
    template = db.query(PromptTemplate).filter(PromptTemplate.id == template_id).first()
    if not template:
        raise ResourceNotFoundError("Prompt template", template_id)

    persona = None
    if template.persona_id:
        persona = db.query(Persona).filter(Persona.id == template.persona_id).first()

    req = RefinePromptRequest(
        prompt=template.template_content or "",
        target_provider=template.model_provider,
        target_model=template.model_name,
        persona_name=persona.name if persona else None,
        persona_description=persona.description if persona else None,
    )
    result = await refine_prompt(req, current_user=current_user)

    template.refined_content = result.refined_prompt
    template.refined_at = datetime.utcnow()
    template.refined_by_model = result.refiner_model
    db.commit()
    db.refresh(template)

    return {
        "id": template.id,
        "refined_content": template.refined_content,
        "refined_at": template.refined_at,
        "refined_by_model": template.refined_by_model,
        "notes": result.notes,
    }
