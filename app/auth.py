from __future__ import annotations

import os
from datetime import datetime, timezone

from authlib.integrations.starlette_client import OAuth
from fastapi import HTTPException, Request
from starlette.config import Config

from app.env import load_env_file
from app.schemas import User
from app.store import JsonStore


load_env_file()

SESSION_SECRET = os.getenv("VMS_SESSION_SECRET", "change-me-in-production")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
DEV_AUTH_EMAIL = os.getenv("VMS_DEV_AUTH_EMAIL", "")

config = Config(environ=os.environ)
oauth = OAuth(config)

if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
    oauth.register(
        name="google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )


def google_auth_configured() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def public_user(user: User) -> dict:
    expires = datetime.fromisoformat(user.trial_expires_at)
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    remaining = max(0, (expires - datetime.now(timezone.utc)).days)
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "picture": user.picture,
        "trial_started_at": user.trial_started_at,
        "trial_expires_at": user.trial_expires_at,
        "trial_active": user.trial_active(),
        "trial_days_remaining": remaining,
    }


async def current_user(request: Request) -> User:
    store: JsonStore = request.app.state.store
    user_id = request.session.get("user_id")
    if user_id:
        user = store.get_user(user_id)
        if user:
            return user

    if DEV_AUTH_EMAIL:
        user = store.upsert_google_user(DEV_AUTH_EMAIL, name="Demo Dev")
        request.session["user_id"] = user.id
        return user

    raise HTTPException(status_code=401, detail="authentication_required")


async def active_trial_user(request: Request) -> User:
    user = await current_user(request)
    if not user.trial_active():
        raise HTTPException(status_code=402, detail="trial_expired")
    return user
