"""
Phase 2 of multi-tenant architecture: real merchant login. Session mechanism
is a JWT stored in an httponly cookie (not a server-side session store) —
chosen because PyJWT is already a transitive dependency (via `mcp`) and this
needs no refresh-token flow or revocation list for exactly 2 seeded accounts;
a signed cookie holding the session id would need a server-side table to
look it up, which is one more moving part than 2 accounts justify.

MERCHANT_SESSION_SECRET (required, no fallback — see _get_secret) signs and
verifies the token. get_current_merchant is the FastAPI dependency every
/merchant/* route (except /merchant/login) is protected with.
"""

import os
from typing import Optional

import bcrypt
import jwt
from fastapi import HTTPException, Request

COOKIE_NAME = "merchant_session"
ALGORITHM = "HS256"
TOKEN_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days — plenty for a demo, no refresh flow


def _get_secret() -> str:
    secret = os.environ.get("MERCHANT_SESSION_SECRET")
    if not secret:
        raise RuntimeError(
            "MERCHANT_SESSION_SECRET is not set — add it to .env (see .env.example)"
        )
    return secret


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_session_token(merchant_id: int, email: str) -> str:
    import time

    payload = {
        "merchant_id": merchant_id,
        "email": email,
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
    }
    return jwt.encode(payload, _get_secret(), algorithm=ALGORITHM)


def decode_session_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, _get_secret(), algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
    return {"id": payload["merchant_id"], "email": payload["email"]}


def get_current_merchant(request: Request) -> dict:
    """
    FastAPI dependency: reads the httponly session cookie, verifies it, and
    returns {"id": ..., "email": ...} for the logged-in merchant. Raises 401
    if the cookie is missing, malformed, expired, or signed with a different
    secret — every /merchant/* route except /merchant/login depends on this.
    """
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="not authenticated")
    merchant = decode_session_token(token)
    if merchant is None:
        raise HTTPException(status_code=401, detail="invalid or expired session")
    return merchant
