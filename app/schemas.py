from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, EmailStr


# ============ Auth Schemas ============

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    user_id: Optional[int] = None


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: Optional[str] = None


class UserResponse(BaseModel):
    uuid: str
    email: str
    full_name: Optional[str]
    balance: float
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class APIKeyCreate(BaseModel):
    name: Optional[str] = None
    rate_limit: Optional[int] = 60
    quota_limit: Optional[float] = None
    ip_whitelist: Optional[List[str]] = None
    expires_at: Optional[datetime] = None


class APIKeyResponse(BaseModel):
    uuid: str
    key_prefix: str
    name: Optional[str]
    rate_limit: int
    quota_limit: Optional[float]
    used_quota: float
    is_active: bool
    last_used_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


class APIKeyCreateResponse(BaseModel):
    name: Optional[str]
    api_key: str  # Only shown once on creation
    key_prefix: str


# ============ Agent Schemas ============

class AgentResponse(BaseModel):
    uuid: str
    agent_id: str
    name: str
    description: Optional[str]
    category: str
    capabilities: List[str]
    context_window: Optional[str]
    pricing: Optional[float]
    is_active: bool
    is_public: bool
    version: str
    created_at: datetime

    class Config:
        from_attributes = True


class AgentListResponse(BaseModel):
    agents: List[AgentResponse]
    total: int


# ============ Pencil Agent Schemas ============

class PencilAgentCreateRequest(BaseModel):
    """Request body for creating a Pencil Agent instance."""
    name: str = Field(..., description="Agent display name")
    description: Optional[str] = None
    category: str = "writing"
    soul_prompt: Optional[str] = None
    style_tags: Optional[List[str]] = None
    memory_max_turns: int = Field(default=30, ge=1, le=200)
    model_provider: Optional[str] = None
    model_name: Optional[str] = None
    is_public: bool = False
    # doc 16 §7.5 — Agent classification.
    # `kind` defaults to 'custom' for user-from-scratch agents. Platform
    # operators create 'super' templates out-of-band; 'derived' agents are
    # produced by the dedicated POST /pencil/<super_id>/derive endpoint (P2),
    # not through this generic create — so we deliberately accept only
    # 'custom' here. Validation lives in the router.
    kind: Optional[str] = Field(default="custom", description="super | derived | custom (only 'custom' allowed via this endpoint)")


class PencilAgentUpdateRequest(BaseModel):
    """
    Request body for editing a Pencil Agent in place.

    All fields optional — only supplied keys are updated. The agent_id and
    gateway_agent_id never change. Updates go to Gateway via PUT /v1/agents/:id
    so existing chat sessions keep their conversation history.
    """
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    soul_prompt: Optional[str] = None
    style_tags: Optional[List[str]] = None
    memory_max_turns: Optional[int] = Field(default=None, ge=1, le=200)
    model_provider: Optional[str] = None
    model_name: Optional[str] = None
    is_public: Optional[bool] = None
    # `kind` / `parent_template_id` / `soul_policy` are intentionally NOT
    # editable through this endpoint — changing them after creation breaks
    # the super→derived lineage invariants. Use the dedicated lifecycle
    # endpoints (P2 derive, P5 super version bumps) instead.


class PencilAgentCreateResponse(BaseModel):
    """Response after creating a Pencil Agent."""
    agent_id: str
    gateway_agent_id: str
    name: str
    status: str  # ready, error, syncing
    created_at: datetime


class PencilAgentDetail(BaseModel):
    """Full detail of a user's PencilAgent (used by GET /pencil/me and PUT response)."""
    agent_id: str
    gateway_agent_id: str
    name: str
    description: Optional[str]
    category: str
    soul_prompt: Optional[str] = None
    style_tags: List[str] = Field(default_factory=list)
    memory_max_turns: int = 30
    model_provider: Optional[str] = None
    model_name: Optional[str] = None
    is_public: bool
    # doc 16 §7.5 — surfaced to the UI so it can render the right affordances
    # (e.g. lock the Soul editor when kind=super / soul_policy=immutable, show
    # a "derived from X" badge when parent_template_id is set).
    kind: str = "custom"
    parent_template_id: Optional[int] = None
    soul_policy: str = "overridable"
    gateway_status: Optional[str] = None  # ready, error, syncing, delete_error
    gateway_error: Optional[str] = None
    retry_count: int = 0
    last_synced_at: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


# ============ Chat Completion Schemas ============

class Message(BaseModel):
    role: str  # system, user, assistant
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = Field(..., description="Model ID, e.g., asgard/code-refactor")
    messages: List[Message]
    temperature: Optional[float] = Field(default=0.7, ge=0, le=2)
    top_p: Optional[float] = Field(default=1.0, ge=0, le=1)
    max_tokens: Optional[int] = Field(default=4096, ge=1, le=65536)
    stream: Optional[bool] = False
    user: Optional[str] = None
    session_id: Optional[str] = None


class ChatCompletionChoice(BaseModel):
    index: int
    message: Message
    finish_reason: Optional[str] = None


class UsageInfo(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[ChatCompletionChoice]
    usage: UsageInfo


# ============ Usage Stats Schemas ============

class UsageStatsResponse(BaseModel):
    total_requests: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_cost: float
    by_agent: Dict[str, Dict[str, Any]]


class UsageLogResponse(BaseModel):
    uuid: str
    agent_id: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost: float
    status: str
    latency_ms: int
    created_at: datetime

    class Config:
        from_attributes = True


# ============ Balance Schemas ============

class BalanceResponse(BaseModel):
    balance: float
    currency: str = "Credit"


class BalanceTransactionResponse(BaseModel):
    uuid: str
    amount: float
    transaction_type: str
    description: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True
