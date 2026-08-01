"""
Auth routes for TradeAlgo Pro — signup, signin, logout, me.

Uses Supabase Auth REST API directly (same credentials as supabase_db.py).
No broker credentials involved — this is just access control.
"""

import httpx
from fastapi import APIRouter, HTTPException, Header, Cookie, Response
from pydantic import BaseModel, EmailStr

from advance_orb.supabase_db import _SUPABASE_URL, _SUPABASE_KEY

router = APIRouter(prefix="/auth", tags=["auth"])

# ================================================================
# REQUEST / RESPONSE SCHEMAS
# ================================================================

class SignupRequest(BaseModel):
    email: str
    password: str

class SigninRequest(BaseModel):
    email: str
    password: str

class AuthResponse(BaseModel):
    ok: bool
    user: dict | None = None
    access_token: str | None = None
    error: str | None = None


# ================================================================
# SUPABASE AUTH HELPERS
# ================================================================

_AUTH_HEADERS = {
    "apikey": _SUPABASE_KEY,
    "Content-Type": "application/json",
}


def _supabase_signup(email: str, password: str) -> dict:
    """Create a new user via Supabase Auth REST API."""
    resp = httpx.post(
        f"{_SUPABASE_URL}/auth/v1/signup",
        headers=_AUTH_HEADERS,
        json={"email": email, "password": password},
        timeout=15,
    )
    return {"status": resp.status_code, "body": resp.json()}


def _supabase_signin(email: str, password: str) -> dict:
    """Sign in and get an access/refresh token pair."""
    resp = httpx.post(
        f"{_SUPABASE_URL}/auth/v1/token?grant_type=password",
        headers=_AUTH_HEADERS,
        json={"email": email, "password": password},
        timeout=15,
    )
    return {"status": resp.status_code, "body": resp.json()}


def _supabase_get_user(access_token: str) -> dict:
    """Get the currently logged-in user from the access token."""
    resp = httpx.get(
        f"{_SUPABASE_URL}/auth/v1/user",
        headers={
            "apikey": _SUPABASE_KEY,
            "Authorization": f"Bearer {access_token}",
        },
        timeout=10,
    )
    return {"status": resp.status_code, "body": resp.json()}


# ================================================================
# ROUTES
# ================================================================

@router.post("/signup", response_model=AuthResponse)
def signup(req: SignupRequest):
    """Register a new account."""
    result = _supabase_signup(req.email, req.password)

    if result["status"] == 200:
        body = result["body"]
        # On success, Supabase returns the user + auto-confirms for our project
        user_data = {
            "id": body["user"]["id"],
            "email": body["user"].get("email", req.email),
        }
        return AuthResponse(
            ok=True,
            user=user_data,
            access_token=body.get("access_token", ""),
        )

    # Extract readable error
    body = result["body"]
    msg = body.get("msg") or body.get("error_description") or body.get("error") or "Signup failed"
    if "User already registered" in str(body):
        msg = "This email is already registered. Please log in instead."

    return AuthResponse(ok=False, error=msg)


@router.post("/signin", response_model=AuthResponse)
def signin(req: SigninRequest):
    """Log in with email and password."""
    result = _supabase_signin(req.email, req.password)

    if result["status"] == 200:
        body = result["body"]
        user_data = {
            "id": body["user"]["id"],
            "email": body["user"].get("email", req.email),
        }
        return AuthResponse(
            ok=True,
            user=user_data,
            access_token=body.get("access_token", ""),
        )

    # Extract readable error
    body = result["body"]
    msg = body.get("msg") or body.get("error_description") or body.get("error") or "Login failed"
    if "Invalid login credentials" in str(body):
        msg = "Wrong email or password."

    return AuthResponse(ok=False, error=msg)


@router.get("/me", response_model=AuthResponse)
def me(authorization: str = Header(None)):
    """Get current user info from the access token."""
    if not authorization or not authorization.startswith("Bearer "):
        return AuthResponse(ok=False, error="No auth token provided.")

    token = authorization.replace("Bearer ", "", 1)
    result = _supabase_get_user(token)

    if result["status"] == 200:
        body = result["body"]
        user_data = {
            "id": body["id"],
            "email": body.get("email", ""),
        }
        return AuthResponse(ok=True, user=user_data)

    return AuthResponse(ok=False, error="Invalid or expired token.")


@router.post("/logout")
def logout():
    """Logout — client-side token discard is sufficient for our setup.
    
    Supabase tokens are short-lived; no server-side session to revoke.
    The frontend just drops the stored token.
    """
    return {"ok": True, "message": "Logged out."}
