"""
Auth routes — signup, signin, and JWT verification for multi-user support.

Uses Supabase Auth REST API directly (no service_role key needed).
Users are created in `auth.users`, then extended into `algo_user` table.
"""

import httpx
import time
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, EmailStr

_SUPABASE_URL = "https://atyqkbrmrosnoczktsmm.supabase.co"
_SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImF0eXFrYnJtcm9zbm9jemt0c21tIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA1NjI4ODcsImV4cCI6MjA5NjEzODg4N30.f-vn85HGFfPMUNeyJLccZSIVTKvZGXp1Ty5Hw08pFsU"

_AUTH_HEADERS = {
    "apikey": _SUPABASE_KEY,
    "Content-Type": "application/json",
}

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ── Schemas ──────────────────────────────────────────────────────

class SignUpPayload(BaseModel):
    email: str
    password: str
    username: str

class SignInPayload(BaseModel):
    email: str
    password: str

class UserOut(BaseModel):
    id: str
    email: str
    username: str
    created_at: str | None = None


# ── Auth helpers ─────────────────────────────────────────────────

def _sb_headers(jwt: str | None = None) -> dict:
    h = dict(_AUTH_HEADERS)
    if jwt:
        h["Authorization"] = f"Bearer {jwt}"
    return h


async def get_current_user(authorization: str | None = Header(None)) -> dict:
    """FastAPI dependency — extracts & verifies JWT from Authorization header.

    Returns user dict with keys: id, email, username, created_at.
    Raises 401 if token is missing or invalid.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    jwt = authorization[7:]

    async with httpx.AsyncClient() as cl:
        resp = await cl.get(
            f"{_SUPABASE_URL}/auth/v1/user",
            headers=_sb_headers(jwt),
            timeout=10,
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    supa_user = resp.json()  # {id, email, user_metadata: {username, ...}, ...}
    uid = supa_user.get("id")
    email = supa_user.get("email", "")
    meta = supa_user.get("user_metadata", {}) or {}

    # Fetch username from algo_user (metadata might not have it)
    async with httpx.AsyncClient() as cl:
        row_resp = await cl.get(
            f"{_SUPABASE_URL}/rest/v1/algo_user",
            params={"id": f"eq.{uid}", "select": "username,created_at"},
            headers=_sb_headers(jwt),
            timeout=10,
        )

    username = meta.get("username", email.split("@")[0])
    created_at = None
    if row_resp.status_code == 200 and row_resp.json():
        row = row_resp.json()[0]
        username = row.get("username", username)
        created_at = row.get("created_at")

    return {
        "id": uid,
        "email": email,
        "username": username,
        "created_at": created_at,
    }


# ── Routes ───────────────────────────────────────────────────────

@router.post("/signup")
async def signup(payload: SignUpPayload):
    """Create a new user via Supabase Auth + insert into algo_user."""
    email = payload.email.strip().lower()
    password = payload.password
    username = payload.username.strip()

    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    if not username or len(username) < 2:
        raise HTTPException(status_code=400, detail="Username must be at least 2 characters")

    # ── Step 1: Create user in Supabase Auth ──────────────────
    async with httpx.AsyncClient() as cl:
        signup_resp = await cl.post(
            f"{_SUPABASE_URL}/auth/v1/signup",
            headers=_sb_headers(),
            json={
                "email": email,
                "password": password,
                "data": {"username": username},
            },
            timeout=15,
        )

    if signup_resp.status_code not in (200, 201):
        detail = signup_resp.json().get("msg", signup_resp.text)[:200]
        if "already registered" in detail.lower():
            raise HTTPException(status_code=409, detail="Email already registered")
        raise HTTPException(status_code=400, detail=f"Signup failed: {detail}")

    supa_user = signup_resp.json().get("user", {}) or signup_resp.json()
    uid = supa_user.get("id")
    access_token = signup_resp.json().get("access_token", "")

    if not uid:
        raise HTTPException(status_code=500, detail="Failed to create user — no user ID returned")

    # ── No algo_user insert ───────────────────────────────────
    # The username & email are stored in Supabase Auth's
    # user_metadata (sent as `data` in the signup request above).
    # get_current_user() reads from user_metadata, so no separate
    # algo_user insert is needed for authentication to work.
    # An algo_user row can be created later (e.g. via Settings)
    # when the user explicitly saves profile data.

    return {
        "ok": True,
        "access_token": access_token,
        "user": {
            "id": uid,
            "email": email,
            "username": username,
        },
    }


@router.post("/signin")
async def signin(payload: SignInPayload):
    """Authenticate user via Supabase Auth."""
    email = payload.email.strip().lower()
    password = payload.password

    async with httpx.AsyncClient() as cl:
        resp = await cl.post(
            f"{_SUPABASE_URL}/auth/v1/token?grant_type=password",
            headers=_sb_headers(),
            json={"email": email, "password": password},
            timeout=15,
        )

    if resp.status_code != 200:
        detail = resp.json().get("msg", resp.text)[:200]
        if "Invalid login credentials" in detail:
            raise HTTPException(status_code=401, detail="Invalid email or password")
        raise HTTPException(status_code=401, detail=f"Login failed: {detail}")

    data = resp.json()
    user_data = data.get("user", {})
    uid = user_data.get("id", "")
    meta = user_data.get("user_metadata", {}) or {}
    username = meta.get("username", email.split("@")[0])

    # Update last_login in algo_user
    async with httpx.AsyncClient() as cl:
        await cl.patch(
            f"{_SUPABASE_URL}/rest/v1/algo_user",
            params={"id": f"eq.{uid}"},
            headers=_sb_headers(data["access_token"]),
            json={"last_login": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())},
            timeout=10,
        )

    return {
        "ok": True,
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", ""),
        "user": {
            "id": uid,
            "email": user_data.get("email", email),
            "username": username,
        },
    }


@router.get("/me")
async def me(current_user: dict = __import__("fastapi").Depends(get_current_user)):
    """Return current authenticated user's profile."""
    return {"ok": True, "user": current_user}
