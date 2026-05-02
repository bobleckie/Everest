from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import User
from ..auth import (
    authenticate_user, create_access_token,
    get_current_active_user, get_current_admin_user,
    ACCESS_TOKEN_EXPIRE_MINUTES
)
from ..services import audit
from pydantic import BaseModel

router = APIRouter()

class UserCreate(BaseModel):
    username: str
    email: str
    password: str
    first_name: str | None = None
    last_name: str | None = None
    role: str = "proposal_manager"
    must_change_password: bool = True

class UserUpdate(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    role: str | None = None
    is_active: bool | None = None
    must_change_password: bool | None = None

class AdminResetPasswordRequest(BaseModel):
    new_password: str

class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    first_name: str | None = None
    last_name: str | None = None
    role: str
    is_active: bool
    must_change_password: bool = False

    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str
    must_change_password: bool = False
    # Optional refresh token. Older frontends ignore these fields.
    refresh_token: str | None = None
    refresh_token_expires_in_minutes: int | None = None

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

class RefreshRequest(BaseModel):
    refresh_token: str


class RedeemInviteRequest(BaseModel):
    token: str
    new_password: str


def authenticate_user(db: Session, username: str, password: str):
    """Authenticate a user by username and password."""
    from ..auth import verify_password
    user = db.query(User).filter(User.username == username).first()
    if not user:
        return False
    if not verify_password(password, user.hashed_password):
        return False
    return user

@router.post("/register", response_model=UserResponse)
def register_user(user: UserCreate, request: Request, db: Session = Depends(get_db)):
    """Register a new user."""
    from ..auth import get_password_hash

    # Check if user already exists
    db_user = db.query(User).filter(
        (User.username == user.username) | (User.email == user.email)
    ).first()
    if db_user:
        audit.write_with_username(
            db, action="register_user", username=user.username,
            status="error", request=request, payload={"reason": "duplicate"},
        )
        raise HTTPException(status_code=400, detail="Username or email already registered")

    # Validate role
    valid_roles = ["admin", "proposal_manager", "vendor", "evaluator"]
    if user.role not in valid_roles:
        raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {', '.join(valid_roles)}")

    # Create user
    hashed_password = get_password_hash(user.password)
    db_user = User(
        username=user.username,
        email=user.email,
        hashed_password=hashed_password,
        first_name=user.first_name,
        last_name=user.last_name,
        role=user.role,
        must_change_password=user.must_change_password,
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    audit.write(
        db, action="register_user", target_type="user", target_id=db_user.id,
        request=request,
        payload={"username": db_user.username, "role": db_user.role},
    )
    return db_user

@router.post("/token", response_model=Token)
async def login_for_access_token(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """Login and get access token."""
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        # Audit the failed attempt — note we have no user object yet.
        audit.write_with_username(
            db, action="login_failure", username=form_data.username,
            status="error", request=request,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    # Mint a refresh token alongside the access token. Old frontends
    # ignore the new field; the new flow uses it (#8).
    from ..services import refresh_tokens as _rt
    refresh_plain, _refresh_row = _rt.mint(db, user, request)
    refresh_minutes = _rt._expiry_days() * 24 * 60
    audit.write(
        db, action="login_success", target_type="user", target_id=user.id,
        request=request, current_user=user,
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "must_change_password": bool(getattr(user, "must_change_password", False)),
        "refresh_token": refresh_plain,
        "refresh_token_expires_in_minutes": refresh_minutes,
    }


@router.post("/refresh", response_model=Token)
async def refresh_token_endpoint(
    body: RefreshRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Exchange a refresh token for a new access + refresh pair.

    Implements rotation: the old refresh token is revoked. Replay of a
    revoked token triggers chain-wide revocation (theft detection).
    """
    from ..services import refresh_tokens as _rt
    result = _rt.verify_and_rotate(db, body.refresh_token, request)
    if result is None:
        # Could be unknown / expired / replay. Log and respond uniformly.
        audit.write_with_username(
            db, action="refresh_replay_detected", username="(unknown)",
            status="error", request=request,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )
    new_refresh, user = result
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    refresh_minutes = _rt._expiry_days() * 24 * 60
    audit.write(
        db, action="refresh", target_type="user", target_id=user.id,
        request=request, current_user=user,
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "must_change_password": bool(getattr(user, "must_change_password", False)),
        "refresh_token": new_refresh,
        "refresh_token_expires_in_minutes": refresh_minutes,
    }


@router.post("/logout")
async def logout(
    body: RefreshRequest,
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Revoke the refresh token in `body`. Idempotent (returns ok even
    if the token was already gone)."""
    from ..services import refresh_tokens as _rt
    _rt.revoke(db, body.refresh_token)
    audit.write(
        db, action="logout", target_type="user", target_id=current_user.id,
        request=request, current_user=current_user,
    )
    return {"message": "Logged out"}


@router.post("/logout-all")
async def logout_all(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Revoke EVERY active refresh token belonging to the current user."""
    from ..services import refresh_tokens as _rt
    count = _rt.revoke_all_for_user(db, current_user.id)
    audit.write(
        db, action="logout_all", target_type="user", target_id=current_user.id,
        request=request, current_user=current_user,
        payload={"revoked": count},
    )
    return {"message": f"Revoked {count} refresh token(s)"}

@router.get("/me", response_model=UserResponse)
async def read_users_me(current_user: User = Depends(get_current_active_user)):
    """Get current user information."""
    return current_user

@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Change the current user's password. Clears the must_change_password flag."""
    from ..auth import verify_password, get_password_hash

    if not verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if not body.new_password or len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")
    if body.new_password == body.current_password:
        raise HTTPException(status_code=400, detail="New password must be different from the current password")

    current_user.hashed_password = get_password_hash(body.new_password)
    current_user.must_change_password = False
    db.commit()
    audit.write(
        db, action="change_password", target_type="user", target_id=current_user.id,
        request=request, current_user=current_user,
    )
    return {"message": "Password updated"}

@router.get("/users", response_model=list[UserResponse])
async def get_users(
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """Get all users (admin only)."""
    users = db.query(User).offset(skip).limit(limit).all()
    return users

@router.put("/users/{user_id}/activate")
async def activate_user(
    user_id: int,
    request: Request,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db)
):
    """Activate/deactivate a user (admin only)."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_active = not user.is_active
    db.commit()
    audit.write(
        db,
        action="activate_user" if user.is_active else "deactivate_user",
        target_type="user", target_id=user.id,
        request=request, current_user=current_user,
        payload={"target_username": user.username, "is_active": user.is_active},
    )
    return {"message": f"User {'activated' if user.is_active else 'deactivated'}"}


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    body: UserUpdate,
    request: Request,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    """Update a user's profile / role / status (admin only)."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user_id == current_user.id and body.role is not None and body.role != "admin":
        raise HTTPException(status_code=400, detail="Cannot remove admin role from yourself")
    changes = body.model_dump(exclude_unset=True)
    for field, val in changes.items():
        setattr(user, field, val)
    db.commit()
    db.refresh(user)
    audit.write(
        db, action="update_user", target_type="user", target_id=user.id,
        request=request, current_user=current_user,
        payload={"target_username": user.username, "changes": changes},
    )
    return user


@router.post("/users/{user_id}/reset-password")
async def admin_reset_password(
    user_id: int,
    body: AdminResetPasswordRequest,
    request: Request,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    """Admin sets a new password for any user and flags must_change_password."""
    from ..auth import get_password_hash
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not body.new_password or len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    user.hashed_password = get_password_hash(body.new_password)
    user.must_change_password = True
    db.commit()
    audit.write(
        db, action="admin_reset_password", target_type="user", target_id=user.id,
        request=request, current_user=current_user,
        payload={"target_username": user.username},
    )
    return {"message": "Password reset. User must change it on next login."}

# ── User invitations (#17) ──────────────────────────────────────────
@router.post("/users/{user_id}/invite")
async def create_user_invitation(
    user_id: int,
    request: Request,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    """Generate a one-time-use invitation for the given user.

    Always returns the redeem URL + email body so the admin can copy/paste
    if SMTP isn't configured. If `SMTP_HOST` is set, also sends the email
    automatically; the response says `email_sent: true` on success.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=400, detail="User is deactivated")
    from ..services import invitations as _inv
    from ..services import smtp_mailer as _mail
    plaintext, row = _inv.mint(db, user, invited_by=current_user, request=request)
    url = _inv.redeem_url(plaintext)
    inviter_name = " ".join(filter(None, [current_user.first_name, current_user.last_name])) or current_user.username
    email_body = _mail.render_invite_body(
        redeem_url=url,
        inviter_name=inviter_name,
        expires_in_days=_inv._expiry_days(),
    )
    sent = False
    if _mail.is_configured():
        sent = _mail.send_invite(
            to_email=user.email,
            redeem_url=url,
            inviter_name=inviter_name,
            expires_in_days=_inv._expiry_days(),
        )
    audit.write(
        db, action="invite_created", target_type="user", target_id=user.id,
        request=request, current_user=current_user,
        payload={"target_username": user.username, "email_sent": sent},
    )
    return {
        "user_id": user.id,
        "email": user.email,
        "redeem_url": url,
        "expires_at": row.expires_at.isoformat(),
        "email_sent": sent,
        "smtp_configured": _mail.is_configured(),
        # The admin can copy/paste this into Teams/email if SMTP isn't wired up.
        "email_body": email_body,
    }


@router.post("/redeem-invite", response_model=Token)
async def redeem_invitation(
    body: RedeemInviteRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Redeem an invitation token by setting a new password.

    On success returns a logged-in {access_token, refresh_token} pair so
    the user lands signed in immediately.
    """
    if not body.new_password or len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    from ..services import invitations as _inv
    user = _inv.consume(db, body.token, body.new_password, request=request)
    if user is None:
        audit.write_with_username(
            db, action="invite_redeem_failed", username="(unknown)",
            status="error", request=request,
        )
        raise HTTPException(
            status_code=400,
            detail="Invitation is invalid, expired, or already used",
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    from ..services import refresh_tokens as _rt
    refresh_plain, _refresh_row = _rt.mint(db, user, request)
    refresh_minutes = _rt._expiry_days() * 24 * 60
    audit.write(
        db, action="invite_redeemed", target_type="user", target_id=user.id,
        request=request, current_user=user,
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "must_change_password": False,
        "refresh_token": refresh_plain,
        "refresh_token_expires_in_minutes": refresh_minutes,
    }