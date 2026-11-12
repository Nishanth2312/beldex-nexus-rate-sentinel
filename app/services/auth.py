import base64
import hashlib
import hmac
import time
from typing import Optional
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from app.config import settings

security = HTTPBearer(auto_error=False)

def _generate_signature(data: str) -> str:
    key = settings.admin_secret_key.encode("utf-8")
    sig = hmac.new(key, data.encode("utf-8"), hashlib.sha256).hexdigest()
    return sig

def create_admin_token(username: str) -> str:
    # Payload: username:epoch_timestamp
    now = int(time.time())
    payload = f"{username}:{now}"
    b64_payload = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("utf-8")
    sig = _generate_signature(b64_payload)
    return f"{b64_payload}.{sig}"

def verify_admin_token(token: str) -> bool:
    if not token or "." not in token:
        return False
    try:
        b64_payload, provided_sig = token.strip().split(".", 1)
        expected_sig = _generate_signature(b64_payload)
        if not hmac.compare_digest(provided_sig, expected_sig):
            return False

        payload = base64.urlsafe_b64decode(b64_payload.encode("utf-8")).decode("utf-8")
        username, ts_str = payload.split(":", 1)
        created_at = int(ts_str)

        # Check expiration
        max_age = settings.admin_session_expiry_hours * 3600
        if (time.time() - created_at) > max_age:
            return False

        return username == settings.admin_username
    except Exception:
        return False

def authenticate_admin_credentials(username: str, password: str) -> bool:
    user_match = hmac.compare_digest(username.strip(), settings.admin_username)
    pass_match = hmac.compare_digest(password.strip(), settings.admin_password)
    return user_match and pass_match

async def require_admin(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security)
) -> str:
    """
    FastAPI security dependency to restrict endpoints strictly to Admin alone.
    """
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin authentication required. Please log in with admin credentials.",
            headers={"WWW-Authenticate": "Bearer"}
        )

    token = credentials.credentials
    if not verify_admin_token(token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or expired admin session token.",
            headers={"WWW-Authenticate": "Bearer"}
        )
    return settings.admin_username
