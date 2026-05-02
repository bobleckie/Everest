"""
Custom exception classes for the RFP platform.
"""
from typing import Any, Dict, Optional
from fastapi import HTTPException


class RFPException(HTTPException):
    """Base exception class for RFP platform errors."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        error_code: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None
    ):
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.error_code = error_code or f"RFP_{status_code}"


class AuthenticationError(RFPException):
    """Authentication related errors."""

    def __init__(self, detail: str = "Authentication failed"):
        super().__init__(
            status_code=401,
            detail=detail,
            error_code="AUTH_001"
        )


class AuthorizationError(RFPException):
    """Authorization related errors."""

    def __init__(self, detail: str = "Insufficient permissions"):
        super().__init__(
            status_code=403,
            detail=detail,
            error_code="AUTH_002"
        )


class ValidationError(RFPException):
    """Data validation errors."""

    def __init__(self, detail: str = "Validation failed"):
        super().__init__(
            status_code=422,
            detail=detail,
            error_code="VALID_001"
        )


class ResourceNotFoundError(RFPException):
    """Resource not found errors."""

    def __init__(self, resource: str, resource_id: Any = None):
        detail = f"{resource} not found"
        if resource_id:
            detail += f" with id {resource_id}"
        super().__init__(
            status_code=404,
            detail=detail,
            error_code="NOT_FOUND_001"
        )


class DatabaseError(RFPException):
    """Database operation errors."""

    def __init__(self, detail: str = "Database operation failed"):
        super().__init__(
            status_code=500,
            detail=detail,
            error_code="DB_001"
        )


class ExternalServiceError(RFPException):
    """External service (OpenAI, etc.) errors."""

    def __init__(self, service: str, detail: str = "External service error"):
        super().__init__(
            status_code=502,
            detail=f"{service}: {detail}",
            error_code="EXT_001"
        )


class FileProcessingError(RFPException):
    """File processing errors."""

    def __init__(self, detail: str = "File processing failed"):
        super().__init__(
            status_code=400,
            detail=detail,
            error_code="FILE_001"
        )


class ProposalWorkflowError(RFPException):
    """Proposal workflow errors."""

    def __init__(self, detail: str = "Invalid workflow transition"):
        super().__init__(
            status_code=400,
            detail=detail,
            error_code="WORKFLOW_001"
        )