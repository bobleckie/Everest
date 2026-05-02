"""
Logging configuration for the RFP platform.
"""
import contextvars
import logging
import logging.config
import sys
from pathlib import Path
from typing import Dict, Any, Optional
import json
from datetime import datetime


# Per-request correlation id. Set by RequestIdMiddleware in app/main.py
# at the start of each request and reset at the end. When no request is
# in flight (background jobs, tests, scheduled tasks), the value is "-".
request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)


class RequestIdFilter(logging.Filter):
    """Inject `request_id` into every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.request_id = request_id_ctx.get()
        except LookupError:
            record.request_id = "-"
        return True


def setup_logging(
    level: str = "INFO",
    log_file: Optional[str] = None,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
) -> None:
    """Setup logging configuration.

    `log_file` defaults to `<EVEREST_LOGS_DIR or workspace/logs>/rfp_platform.log`
    so the active logs directory is consistent with `app.paths`.
    """
    if log_file is None:
        try:
            from . import paths as _paths_mod
            log_file = str(_paths_mod.logs_dir() / "rfp_platform.log")
        except Exception:
            log_file = "logs/rfp_platform.log"

    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Logging configuration
    config: Dict[str, Any] = {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "request_id": {
                "()": "app.logging_config.RequestIdFilter",
            },
        },
        "formatters": {
            "detailed": {
                "format": "%(asctime)s [%(request_id)s] %(name)s - %(levelname)s - %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S"
            },
            "json": {
                "format": json.dumps({
                    "timestamp": "%(asctime)s",
                    "level": "%(levelname)s",
                    "logger": "%(name)s",
                    "message": "%(message)s",
                    "module": "%(module)s",
                    "function": "%(funcName)s",
                    "line": "%(lineno)d",
                    "request_id": "%(request_id)s"
                }),
                "datefmt": "%Y-%m-%dT%H:%M:%S%z"
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": level,
                "formatter": "detailed",
                "stream": sys.stdout,
                "filters": ["request_id"],
            },
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": level,
                "formatter": "json",
                "filename": log_file,
                "maxBytes": max_bytes,
                "backupCount": backup_count,
                "encoding": "utf-8",
                "filters": ["request_id"],
            }
        },
        "root": {
            "level": level,
            "handlers": ["console", "file"]
        },
        "loggers": {
            "uvicorn": {
                "level": level,
                "handlers": ["console", "file"],
                "propagate": False
            },
            "uvicorn.access": {
                "level": "INFO",
                "handlers": ["console", "file"],
                "propagate": False
            },
            "sqlalchemy": {
                "level": "WARNING",
                "handlers": ["console", "file"],
                "propagate": False
            },
            "app": {
                "level": level,
                "handlers": ["console", "file"],
                "propagate": False
            }
        }
    }

    logging.config.dictConfig(config)


class RequestLogger:
    """Middleware for logging HTTP requests."""

    def __init__(self):
        self.logger = logging.getLogger("app.requests")

    async def log_request(self, request, response=None, error=None):
        """Log an HTTP request."""
        log_data = {
            "method": request.method,
            "url": str(request.url),
            "headers": dict(request.headers),
            "client": request.client.host if request.client else None,
        }

        if response:
            log_data["status_code"] = response.status_code
            log_data["response_time"] = getattr(response, "response_time", None)

        if error:
            log_data["error"] = str(error)
            self.logger.error("Request failed", extra=log_data)
        else:
            self.logger.info("Request processed", extra=log_data)


# Global logger instance
logger = logging.getLogger("app")