"""
Authentication API tests for Asgard API.
"""
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.auth import verify_password, create_access_token, decode_token


class TestUserRegistration:
    """Test user registration functionality."""

    @pytest.mark.asyncio
    async def test_register_success(self, client: AsyncClient):
        """Test successful user registration."""
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "newuser@example.com",
                "password": "securepassword123",
                "full_name": "New User"
            }
        )
        assert response.status_code == 201
        data = response.json()
        assert data["email"] == "newuser@example.com"
        assert data["full_name"] == "New User"
        assert data["balance"] == 0.0
        assert data["is_active"] is True
        assert "uuid" in data
        assert "created_at" in data

    @pytest.mark.asyncio
    async def test_register_duplicate_email(self, client: AsyncClient, test_user: User):
        """Test registration with already registered email."""
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": test_user.email,
                "password": "securepassword123",
                "full_name": "Duplicate User"
            }
        )
        assert response.status_code == 400
        assert "Email already registered" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_register_invalid_email(self, client: AsyncClient):
        """Test registration with invalid email format."""
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "not-an-email",
                "password": "securepassword123"
            }
        )
        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_register_short_password(self, client: AsyncClient):
        """Test registration with password too short."""
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "test@example.com",
                "password": "short"
            }
        )
        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_register_missing_fields(self, client: AsyncClient):
        """Test registration with missing required fields."""
        response = await client.post(
            "/api/v1/auth/register",
            json={"email": "test@example.com"}
        )
        assert response.status_code == 422  # Validation error


class TestUserLogin:
    """Test user login functionality."""

    @pytest.mark.asyncio
    async def test_login_success(self, client: AsyncClient, test_user: User):
        """Test successful user login."""
        response = await client.post(
            "/api/v1/auth/login",
            params={
                "email": test_user.email,
                "password": "testpassword123"
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        # Token should be a valid JWT
        payload = decode_token(data["access_token"])
        assert payload["sub"] == str(test_user.id)
        assert payload["email"] == test_user.email

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, client: AsyncClient, test_user: User):
        """Test login with wrong password."""
        response = await client.post(
            "/api/v1/auth/login",
            params={
                "email": test_user.email,
                "password": "wrongpassword"
            }
        )
        assert response.status_code == 401
        assert "Invalid credentials" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_login_nonexistent_user(self, client: AsyncClient):
        """Test login with non-existent user."""
        response = await client.post(
            "/api/v1/auth/login",
            params={
                "email": "nonexistent@example.com",
                "password": "somepassword"
            }
        )
        assert response.status_code == 401
        assert "Invalid credentials" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_login_inactive_user(self, client: AsyncClient, test_inactive_user: User):
        """Test login with inactive user account."""
        response = await client.post(
            "/api/v1/auth/login",
            params={
                "email": test_inactive_user.email,
                "password": "testpassword123"
            }
        )
        assert response.status_code == 403
        assert "inactive" in response.json()["detail"].lower()


class TestTokenValidation:
    """Test JWT token validation functionality."""

    @pytest.mark.asyncio
    async def test_get_current_user(self, client: AsyncClient, auth_headers: dict, test_user: User):
        """Test getting current user info with valid token."""
        response = await client.get("/api/v1/auth/me", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == test_user.email
        assert data["full_name"] == test_user.full_name

    @pytest.mark.asyncio
    async def test_get_current_user_no_token(self, client: AsyncClient):
        """Test getting current user without token."""
        response = await client.get("/api/v1/auth/me")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_current_user_invalid_token(self, client: AsyncClient):
        """Test getting current user with invalid token."""
        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer invalid-token"}
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_get_current_user_expired_token(self, client: AsyncClient, test_user: User):
        """Test getting current user with expired token."""
        # Create token that expires immediately
        token = create_access_token(
            data={"sub": str(test_user.id), "email": test_user.email},
            expires_delta=-1  # Already expired
        )
        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401
        assert "expired" in response.json()["detail"].lower()


class TestPasswordHashing:
    """Test password hashing functions."""

    def test_password_hash(self):
        """Test password hashing."""
        password = "testpassword123"
        hashed = verify_password(password, get_password_hash(password))
        assert hashed is True

    def test_password_hash_wrong(self):
        """Test password hashing with wrong password."""
        password = "testpassword123"
        hashed = verify_password("wrongpassword", get_password_hash(password))
        assert hashed is False


class TestJWTToken:
    """Test JWT token functions."""

    def test_create_access_token(self, test_user: User):
        """Test JWT token creation."""
        token = create_access_token(data={"sub": str(test_user.id)})
        assert token is not None
        assert isinstance(token, str)
        assert len(token) > 0

    def test_decode_valid_token(self, test_user: User):
        """Test decoding valid JWT token."""
        token = create_access_token(data={"sub": str(test_user.id), "email": test_user.email})
        payload = decode_token(token)
        assert payload["sub"] == str(test_user.id)
        assert payload["email"] == test_user.email

    def test_decode_invalid_token(self):
        """Test decoding invalid JWT token."""
        with pytest.raises(Exception) as exc_info:
            decode_token("invalid.token.here")
        assert exc_info.value.status_code == 401
