from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from app.config import settings
from app.services.auth import authenticate_admin_credentials, create_admin_token, require_admin, verify_admin_token

router = APIRouter(prefix="/api/auth", tags=["Admin Authentication"])

class LoginRequest(BaseModel):
    username: str
    password: str

class VerifyTokenRequest(BaseModel):
    token: str

@router.post("/login")
async def admin_login(req: LoginRequest):
    """
    Authenticates the Administrator and issues a secure session Bearer token.
    """
    if not authenticate_admin_credentials(req.username, req.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin username or password. Access restricted to administrator."
        )

    token = create_admin_token(req.username.strip())
    return {
        "status": "success",
        "message": "Admin authenticated successfully",
        "token": token,
        "token_type": "bearer",
        "username": settings.admin_username,
        "role": "admin",
        "expires_in_hours": settings.admin_session_expiry_hours
    }

@router.get("/me")
async def get_admin_profile(admin: str = Depends(require_admin)):
    """
    Verifies that the caller holds active Administrator privileges.
    """
    return {
        "status": "authenticated",
        "username": admin,
        "role": "admin",
        "permissions": ["all_settings", "modify_thresholds", "manage_alerts", "trigger_simulations"]
    }

@router.post("/verify")
async def verify_token_endpoint(req: VerifyTokenRequest):
    is_valid = verify_admin_token(req.token)
    return {"valid": is_valid, "username": settings.admin_username if is_valid else None}
