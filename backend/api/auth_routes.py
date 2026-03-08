"""
Authentication endpoints — login with username/password, returns a JWT.

Configure in backend/.env:
    ADMIN_USERNAME=admin
    ADMIN_PASSWORD_HASH=<bcrypt hash>    ← generate with: python generate-password-hash.py
    JWT_SECRET=<random hex>             ← generate with: python -c "import secrets; print(secrets.token_hex(32))"

If ADMIN_PASSWORD_HASH is not set this router returns 503 (login system disabled).
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.auth import create_access_token, verify_password, get_admin_credentials

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(body: LoginRequest):
    admin_user, admin_hash = get_admin_credentials()
    if not admin_hash:
        raise HTTPException(
            status_code=503,
            detail="Login system not configured. Set ADMIN_PASSWORD_HASH in backend/.env",
        )
    if body.username != admin_user or not verify_password(body.password, admin_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = create_access_token({"sub": body.username})
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": 86400,
    }


@router.get("/status")
async def auth_status():
    """Returns whether the login system is configured — used by frontend to decide whether to show login page."""
    _, admin_hash = get_admin_credentials()
    return {
        "login_enabled": bool(admin_hash),
        "auth_required": bool(admin_hash),
    }
