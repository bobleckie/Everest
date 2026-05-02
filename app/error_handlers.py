"""
Global exception handlers for FastAPI.
"""
import logging
from typing import Any, Dict
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from pydantic import ValidationError as PydanticValidationError
import traceback

from .exceptions import (
    RFPException,
    AuthenticationError,
    AuthorizationError,
    ValidationError,
    ResourceNotFoundError,
    DatabaseError,
    ExternalServiceError,
    FileProcessingError,
    ProposalWorkflowError
)

logger = logging.getLogger("app.error_handlers")


def create_error_response(
    status_code: int,
    message: str,
    error_code: str = None,
    details: Any = None
) -> Dict[str, Any]:
    """Create a standardized error response."""
    response = {
        "error": {
            "message": message,
            "code": error_code or f"HTTP_{status_code}",
            "status_code": status_code
        }
    }

    if details:
        response["error"]["details"] = details

    return response


async def rfp_exception_handler(request: Request, exc: RFPException) -> JSONResponse:
    """Handle custom RFP exceptions."""
    logger.warning(
        f"RFP Exception: {exc.detail}",
        extra={
            "error_code": exc.error_code,
            "status_code": exc.status_code,
            "path": request.url.path,
            "method": request.method
        }
    )

    return JSONResponse(
        status_code=exc.status_code,
        content=create_error_response(
            exc.status_code,
            exc.detail,
            exc.error_code
        )
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Handle standard FastAPI HTTP exceptions."""
    logger.warning(
        f"HTTP Exception: {exc.detail}",
        extra={
            "status_code": exc.status_code,
            "path": request.url.path,
            "method": request.method
        }
    )

    return JSONResponse(
        status_code=exc.status_code,
        content=create_error_response(
            exc.status_code,
            exc.detail
        ),
        # Preserve any custom headers the raiser attached (e.g. Retry-After
        # on 429, WWW-Authenticate on 401). Otherwise rate-limit clients
        # would have to guess.
        headers=getattr(exc, "headers", None),
    )


async def validation_exception_handler(request: Request, exc: PydanticValidationError) -> JSONResponse:
    """Handle Pydantic validation errors."""
    errors = []
    for error in exc.errors():
        errors.append({
            "field": ".".join(str(loc) for loc in error["loc"]),
            "message": error["msg"],
            "type": error["type"]
        })

    logger.warning(
        f"Validation Error: {len(errors)} validation errors",
        extra={
            "errors": errors,
            "path": request.url.path,
            "method": request.method
        }
    )

    return JSONResponse(
        status_code=422,
        content=create_error_response(
            422,
            "Validation failed",
            "VALID_001",
            errors
        )
    )


async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    """Handle SQLAlchemy database errors."""
    logger.error(
        f"Database Error: {str(exc)}",
        extra={
            "exception_type": type(exc).__name__,
            "path": request.url.path,
            "method": request.method,
            "traceback": traceback.format_exc()
        }
    )

    # Handle specific database errors
    if isinstance(exc, IntegrityError):
        return JSONResponse(
            status_code=409,
            content=create_error_response(
                409,
                "Database integrity constraint violated",
                "DB_002"
            )
        )

    return JSONResponse(
        status_code=500,
        content=create_error_response(
            500,
            "Database operation failed",
            "DB_001"
        )
    )


async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected exceptions."""
    logger.error(
        f"Unexpected Error: {str(exc)}",
        extra={
            "exception_type": type(exc).__name__,
            "path": request.url.path,
            "method": request.method,
            "traceback": traceback.format_exc()
        }
    )

    # Forward to Sentry if it's enabled. No-op otherwise.
    try:
        from .observability import capture_exception as _capture
        _capture(exc)
    except Exception:  # noqa: BLE001
        pass

    return JSONResponse(
        status_code=500,
        content=create_error_response(
            500,
            "An unexpected error occurred",
            "INTERNAL_001"
        )
    )


# Exception handler mapping for FastAPI
exception_handlers = {
    RFPException: rfp_exception_handler,
    HTTPException: http_exception_handler,
    PydanticValidationError: validation_exception_handler,
    SQLAlchemyError: sqlalchemy_exception_handler,
    Exception: general_exception_handler,
}