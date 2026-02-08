from datetime import datetime, timedelta
from typing import Optional
from passlib.context import CryptContext
from jose import jwt
from fastapi import HTTPException, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import hashlib
import secrets

from app.config import settings
from app.database import get_db
from app.models import User, APIKey


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password"""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash password"""
    return pwd_context.hash(password)


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
    except jwt.JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


def hash_api_key(api_key: str) -> str:
    """Hash API key for storage"""
    return hashlib.sha256(api_key.encode()).hexdigest()


def generate_api_key() -> tuple[str, str]:
    """Generate new API key with prefix"""
    key = f"asgard_{secrets.token_urlsafe(32)}"
    prefix = key[:10] + "..."
    return key, prefix


async def get_api_key_from_header(
    x_api_key: Optional[str] = Header(None, alias="Authorization"),
    db: AsyncSession = Depends(get_db)
) -> APIKey:
    """Validate API key from header"""
    if not x_api_key:
        # Try Bearer token format
        raise HTTPException(status_code=401, detail="API key required")

    # Remove "Bearer " prefix if present
    if x_api_key.startswith("Bearer "):
        api_key = x_api_key[7:]
    else:
        api_key = x_api_key

    # Hash the provided key for comparison
    key_hash = hash_api_key(api_key)

    # Query API key from database
    result = await db.execute(
        select(APIKey).where(APIKey.key_hash == key_hash)
    )
    api_key_obj = result.scalar_one_or_none()

    if not api_key_obj:
        raise HTTPException(status_code=401, detail="Invalid API key")

    if not api_key_obj.is_active:
        raise HTTPException(status_code=403, detail="API key is disabled")

    # Check expiration
    if api_key_obj.expires_at and api_key_obj.expires_at < datetime.utcnow():
        raise HTTPException(status_code=403, detail="API key has expired")

    return api_key_obj


async def get_current_user(
    api_key: APIKey = Depends(get_api_key_from_header),
    db: AsyncSession = Depends(get_db)
) -> User:
    """Get current user from API key"""
    result = await db.execute(
        select(User).where(User.id == api_key.user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="User is inactive")

    return user
