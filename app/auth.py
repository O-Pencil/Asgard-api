"""
[WHO]: Provides authentication utilities: password hashing, JWT token creation/decoding, API Key generation/hashing/validation, unified JWT+APIKey auth dependencies for SINGLE_USER_MODE
[FROM]: Depends on passlib for password hashing, jose for JWT tokens, hashlib for SHA256 hashing, secrets for secure random generation
[TO]: Consumed by routers for protected endpoints, middleware for API key validation, main.py for authentication dependencies and admin bootstrap
[HERE]: packages/api/app/auth.py - Authentication and authorization utilities; supports JWT tokens, API Keys with SHA256 hashing, and SINGLE_USER_MODE admin bypass
"""
from datetime import datetime, timedelta
from typing import Optional, Tuple

from passlib.context import CryptContext
from jose import jwt, JWTError
from fastapi import HTTPException, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import hashlib
import secrets
import logging

from app.config import settings
from app.database import get_db
from app.models import User, APIKey


logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash password"""
    return pwd_context.hash(password)


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.access_token_expire_minutes)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.algorithm)
    return encoded_jwt


def decode_token(token: str) -> dict:
    """Decode JWT token"""
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.algorithm])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


def decode_jwt_to_user_id(token: str) -> Optional[int]:
    """
    Attempt to decode a JWT and extract the user ID from the 'sub' claim.
    Returns None if the token is not a valid JWT or has no 'sub'.
    Does NOT raise — used as a probing function in the unified auth flow.
    """
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.algorithm])
        sub = payload.get("sub")
        if sub is not None:
            return int(sub)
    except (JWTError, ValueError, TypeError):
        pass
    return None


# ---------------------------------------------------------------------------
# API Key helpers
# ---------------------------------------------------------------------------

def hash_api_key(api_key: str) -> str:
    """Hash API key for storage"""
    return hashlib.sha256(api_key.encode()).hexdigest()


def generate_api_key() -> tuple[str, str]:
    """Generate new API key with prefix"""
    key = f"asgard_{secrets.token_urlsafe(32)}"
    prefix = key[:10] + "..."
    return key, prefix


async def _lookup_api_key(api_key_raw: str, db: AsyncSession) -> Optional[APIKey]:
    """Look up an API key by its raw value. Returns None if not found/disabled/expired."""
    key_hash = hash_api_key(api_key_raw)
    result = await db.execute(
        select(APIKey).where(APIKey.key_hash == key_hash)
    )
    api_key_obj = result.scalar_one_or_none()

    if not api_key_obj:
        return None
    if not api_key_obj.is_active:
        return None
    if api_key_obj.expires_at and api_key_obj.expires_at < datetime.utcnow():
        return None
    return api_key_obj


# ---------------------------------------------------------------------------
# Legacy API-Key-only auth (kept for backward compat, used by /v1/models)
# ---------------------------------------------------------------------------

async def get_api_key_from_header(
    x_api_key: Optional[str] = Header(None, alias="Authorization"),
    db: AsyncSession = Depends(get_db)
) -> APIKey:
    """Validate API key from header (legacy, OpenAI-compat clients)"""
    if not x_api_key:
        raise HTTPException(status_code=401, detail="API key required")

    if x_api_key.startswith("Bearer "):
        api_key = x_api_key[7:]
    else:
        api_key = x_api_key

    api_key_obj = await _lookup_api_key(api_key, db)

    if not api_key_obj:
        raise HTTPException(status_code=401, detail="Invalid API key")

    return api_key_obj


# ---------------------------------------------------------------------------
# Unified JWT / API-Key auth dependencies
# ---------------------------------------------------------------------------

async def _extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
    """Extract the raw token from an Authorization header."""
    if not authorization:
        return None
    if authorization.startswith("Bearer "):
        return authorization[7:]
    return None


async def get_user_from_jwt_or_apikey(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: AsyncSession = Depends(get_db)
) -> User:
    """
    Unified auth dependency for management endpoints (/api/v1/*).

    Resolution order:
    1. JWT Bearer token → decode → load user from DB
    2. API Key Bearer → hash lookup → load user via APIKey.user_id
    3. SINGLE_USER_MODE → return admin user (no auth required)

    Raises 401 if none of the above succeed.
    """
    raw_token = await _extract_bearer_token(authorization)

    # --- Try JWT ---
    if raw_token:
        user_id = decode_jwt_to_user_id(raw_token)
        if user_id is not None:
            result = await db.execute(
                select(User).where(User.id == user_id)
            )
            user = result.scalar_one_or_none()
            if user and user.is_active:
                return user

        # --- Try API Key ---
        api_key_obj = await _lookup_api_key(raw_token, db)
        if api_key_obj:
            result = await db.execute(
                select(User).where(User.id == api_key_obj.user_id)
            )
            user = result.scalar_one_or_none()
            if user and user.is_active:
                return user

    # --- SINGLE_USER_MODE fallback ---
    if settings.single_user_mode:
        result = await db.execute(
            select(User).where(User.email == settings.admin_email)
        )
        admin = result.scalar_one_or_none()
        if admin and admin.is_active:
            return admin

    raise HTTPException(status_code=401, detail="Invalid or missing credentials")


async def get_user_and_apikey_for_chat(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: AsyncSession = Depends(get_db)
) -> Tuple[User, APIKey]:
    """
    Auth dependency for /v1/chat/completions.

    Returns (User, APIKey) — the APIKey is needed for quota/usage tracking.

    Resolution order:
    1. JWT → resolve user → find or create an APIKey for that user
    2. API Key → resolve APIKey + user directly
    3. SINGLE_USER_MODE → admin user + admin's default APIKey

    In SINGLE_USER_MODE, if the admin has no API keys, one is auto-created
    (named "single-user-default").
    """
    raw_token = await _extract_bearer_token(authorization)

    user: Optional[User] = None
    api_key: Optional[APIKey] = None

    if raw_token:
        # --- Try JWT ---
        user_id = decode_jwt_to_user_id(raw_token)
        if user_id is not None:
            result = await db.execute(
                select(User).where(User.id == user_id)
            )
            user = result.scalar_one_or_none()

        # --- Try API Key ---
        if user is None:
            api_key = await _lookup_api_key(raw_token, db)
            if api_key:
                result = await db.execute(
                    select(User).where(User.id == api_key.user_id)
                )
                user = result.scalar_one_or_none()

    # --- SINGLE_USER_MODE fallback ---
    if user is None and settings.single_user_mode:
        result = await db.execute(
            select(User).where(User.email == settings.admin_email)
        )
        user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid or missing credentials")

    # --- Resolve APIKey for this user ---
    if api_key is None:
        result = await db.execute(
            select(APIKey).where(
                APIKey.user_id == user.id,
                APIKey.is_active == True,
            )
        )
        api_key = result.scalar_one_or_none()

    # Auto-create a default API key if the user has none (SINGLE_USER_MODE)
    if api_key is None:
        if settings.single_user_mode:
            raw_key, prefix = generate_api_key()
            api_key = APIKey(
                key_hash=hash_api_key(raw_key),
                key_prefix=prefix,
                name="single-user-default",
                user_id=user.id,
                rate_limit=60,
            )
            db.add(api_key)
            await db.commit()
            await db.refresh(api_key)
            logger.info(
                "Auto-created default API key for admin",
                extra={"user_id": user.id, "key_id": api_key.id},
            )
        else:
            raise HTTPException(
                status_code=403,
                detail="No API key found for this user. Create one in the console.",
            )

    return user, api_key


# ---------------------------------------------------------------------------
# Legacy dependency kept for backward compat (used by /v1/models endpoint)
# ---------------------------------------------------------------------------

async def get_current_user(
    api_key: APIKey = Depends(get_api_key_from_header),
    db: AsyncSession = Depends(get_db)
) -> User:
    """Get current user from API key (legacy, prefer get_user_from_jwt_or_apikey)"""
    result = await db.execute(
        select(User).where(User.id == api_key.user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="User is inactive")

    return user
