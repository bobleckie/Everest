from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
import os
from dotenv import load_dotenv

from .database import get_db
from .models import User
from .exceptions import AuthenticationError, AuthorizationError, ResourceNotFoundError
from .logging_config import logger

load_dotenv()

# Security configuration
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-here-change-in-production")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
# Default 8h so users aren't kicked out mid-task. Override via env for
# stricter policies. Refresh-token rotation is a separate prod-hardening
# step (see docs/PROD_HARDENING_ROLLBACK.md #8).
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 480))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

def verify_password(plain_password, hashed_password):
    """Verify a password against its hash."""
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    """Hash a password for storing."""
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Create a JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(token: str):
    """Verify and decode a JWT token."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            logger.warning("Token missing subject claim")
            return None
        return username
    except JWTError as e:
        logger.warning(f"JWT validation failed: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error during token verification: {str(e)}")
        return None

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    """Get the current authenticated user."""
    try:
        # Dev bypass — only active when DEBUG=true in .env; never in production
        _debug_mode = os.getenv("DEBUG", "false").lower() in ("1", "true", "yes")
        if _debug_mode and credentials.credentials == "dev-bypass-token":
            user = db.query(User).filter(User.role == "admin").first()
            if user:
                logger.warning("Dev bypass token used — ensure DEBUG=false in production")
                return user
            raise AuthenticationError("No admin user found for dev bypass")

        username = verify_token(credentials.credentials)
        if username is None:
            logger.warning("Invalid token provided")
            raise AuthenticationError("Could not validate credentials")

        user = db.query(User).filter(User.username == username).first()
        if user is None:
            logger.warning(f"User not found: {username}")
            raise AuthenticationError("User not found")

        if not user.is_active:
            logger.warning(f"Inactive user attempted access: {username}")
            raise AuthenticationError("Inactive user")

        return user
    except AuthenticationError:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in get_current_user: {str(e)}")
        raise AuthenticationError("Authentication failed")

async def get_current_active_user(current_user: User = Depends(get_current_user)):
    """Get current active user (alias for get_current_user)."""
    return current_user

def authenticate_user(db: Session, username: str, password: str):
    """Authenticate a user by username and password. Returns the User or False."""
    user = db.query(User).filter(User.username == username).first()
    if not user:
        return False
    if not verify_password(password, user.hashed_password):
        return False
    return user

# Role-based dependencies
async def get_current_admin_user(current_user: User = Depends(get_current_user)):
    """Get current user and verify they are an admin."""
    if current_user.role != "admin":
        logger.warning(f"Access denied for non-admin user: {current_user.username} (role: {current_user.role})")
        raise AuthorizationError("Admin access required")
    return current_user

async def get_current_proposal_manager(current_user: User = Depends(get_current_user)):
    """Get current user and verify they are a proposal manager or admin."""
    if current_user.role not in ["admin", "proposal_manager"]:
        logger.warning(f"Access denied for user: {current_user.username} (role: {current_user.role})")
        raise AuthorizationError("Proposal manager or admin access required")
    return current_user

async def get_current_vendor_or_manager(current_user: User = Depends(get_current_user)):
    """Get current user and verify they are a vendor or manager."""
    if current_user.role not in ["admin", "proposal_manager", "vendor"]:
        logger.warning(f"Access denied for user: {current_user.username} (role: {current_user.role})")
        raise AuthorizationError("Vendor or manager access required")
    return current_user