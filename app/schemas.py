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
