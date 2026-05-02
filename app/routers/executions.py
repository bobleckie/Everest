"""
API router for prompt execution and optimization.
"""
from typing import Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
import json

from ..database import get_db, SessionLocal
from ..models import User, PromptTemplate, PromptExecution
from ..auth import get_current_user
from ..ai_service import ai_service
from ..logging_config import logger
from ..exceptions import ValidationError, ExternalServiceError


router = APIRouter(tags=["prompt-execution"])


async def _execute_prompt_background(template_id: int, variables: dict, user_id: int):
    db = SessionLocal()
    try:
        await ai_service.execute_prompt(template_id, variables, user_id, db)
    except Exception as e:
        logger.error(f"Async prompt execution failed: {str(e)}")
    finally:
        db.close()


class ExecutePromptRequest(BaseModel):
    """Request model for prompt execution."""
    template_id: int = Field(..., description="ID of the prompt template to execute")
    variables: Dict[str, Any] = Field(..., description="Variables to substitute in the template")
    async_execution: bool = Field(False, description="Whether to execute asynchronously")


class ExecutePromptResponse(BaseModel):
    """Response model for prompt execution."""
    execution_id: int
    content: str
    model_provider: str
    model_name: str
    tokens_used: int
    execution_time_ms: int
    persona_used: str = None


class OptimizePromptRequest(BaseModel):
    """Request model for prompt optimization."""
    template_id: int = Field(..., description="ID of the prompt template to optimize")
    test_variables: Dict[str, Any] = Field(..., description="Test variables for optimization")
    optimization_criteria: List[str] = Field(
        ["clarity", "specificity", "actionability"],
        description="Criteria for optimization"
    )


class OptimizePromptResponse(BaseModel):
    """Response model for prompt optimization."""
    original_output: Dict[str, Any]
    optimization_analysis: str
    optimization_suggestions: Dict[str, Any]


class PromptExecutionStats(BaseModel):
    """Statistics for prompt execution."""
    total_executions: int
    successful_executions: int
    average_execution_time_ms: float
    total_tokens_used: int
    success_rate: float


@router.post("/execute", response_model=ExecutePromptResponse)
async def execute_prompt(
    request: ExecutePromptRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Execute a prompt template with the given variables."""
    try:
        if request.async_execution:
            # Use a fresh DB session for background execution
            background_tasks.add_task(
                _execute_prompt_background,
                request.template_id,
                request.variables,
                current_user.id
            )
            # Return a placeholder response for async execution
            return ExecutePromptResponse(
                execution_id=0,
                content="Execution started asynchronously",
                model_provider="async",
                model_name="pending",
                tokens_used=0,
                execution_time_ms=0
            )
        else:
            # Synchronous execution
            result = await ai_service.execute_prompt(
                request.template_id,
                request.variables,
                current_user.id,
                db
            )
            return ExecutePromptResponse(**result)

    except ValidationError as e:
        logger.warning(f"Validation error in prompt execution: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except ExternalServiceError as e:
        logger.error(f"External service error in prompt execution: {str(e)}")
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error in prompt execution: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/optimize", response_model=OptimizePromptResponse)
async def optimize_prompt(
    request: OptimizePromptRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Optimize a prompt template based on specified criteria."""
    try:
        # Check if user has permission to optimize prompts
        if current_user.role not in ["admin", "proposal_manager"]:
            raise HTTPException(
                status_code=403,
                detail="Insufficient permissions to optimize prompts"
            )

        result = await ai_service.optimize_prompt(
            request.template_id,
            request.test_variables,
            request.optimization_criteria,
            current_user.id,
            db
        )

        return OptimizePromptResponse(**result)

    except ValidationError as e:
        logger.warning(f"Validation error in prompt optimization: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except ExternalServiceError as e:
        logger.error(f"External service error in prompt optimization: {str(e)}")
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error in prompt optimization: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/executions/{execution_id}")
async def get_execution_result(
    execution_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get the result of a prompt execution."""
    try:
        execution = db.query(PromptExecution).filter(
            PromptExecution.id == execution_id,
            PromptExecution.user_id == current_user.id
        ).first()

        if not execution:
            raise HTTPException(status_code=404, detail="Execution not found")

        return {
            "id": execution.id,
            "prompt_template_id": execution.prompt_template_id,
            "persona_id": execution.persona_id,
            "input_variables": json.loads(execution.input_variables) if execution.input_variables else None,
            "output_content": execution.output_content,
            "model_provider": execution.model_provider,
            "model_name": execution.model_name,
            "tokens_used": execution.tokens_used,
            "execution_time_ms": execution.execution_time_ms,
            "success": execution.success,
            "error_message": execution.error_message,
            "created_at": execution.created_at.isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving execution result: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/executions/stats", response_model=PromptExecutionStats)
async def get_execution_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get statistics for prompt executions."""
    try:
        # Get executions for current user
        executions = db.query(PromptExecution).filter(
            PromptExecution.user_id == current_user.id
        ).all()

        if not executions:
            return PromptExecutionStats(
                total_executions=0,
                successful_executions=0,
                average_execution_time_ms=0.0,
                total_tokens_used=0,
                success_rate=0.0
            )

        total_executions = len(executions)
        successful_executions = len([e for e in executions if e.success])
        total_execution_time = sum(e.execution_time_ms for e in executions if e.execution_time_ms)
        total_tokens = sum(e.tokens_used for e in executions if e.tokens_used)

        return PromptExecutionStats(
            total_executions=total_executions,
            successful_executions=successful_executions,
            average_execution_time_ms=total_execution_time / total_executions if total_executions > 0 else 0,
            total_tokens_used=total_tokens,
            success_rate=successful_executions / total_executions if total_executions > 0 else 0
        )

    except Exception as e:
        logger.error(f"Error retrieving execution stats: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/executions")
async def list_user_executions(
    skip: int = 0,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List prompt executions for the current user."""
    try:
        executions = db.query(PromptExecution).filter(
            PromptExecution.user_id == current_user.id
        ).order_by(PromptExecution.created_at.desc()).offset(skip).limit(limit).all()

        return [
            {
                "id": e.id,
                "prompt_template_id": e.prompt_template_id,
                "persona_id": e.persona_id,
                "model_provider": e.model_provider,
                "model_name": e.model_name,
                "tokens_used": e.tokens_used,
                "execution_time_ms": e.execution_time_ms,
                "success": e.success,
                "created_at": e.created_at.isoformat()
            }
            for e in executions
        ]

    except Exception as e:
        logger.error(f"Error listing executions: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")