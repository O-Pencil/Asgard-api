# Asgard API

Unified Agent Integration Platform - Backend Service

## Tech Stack

- **Framework**: FastAPI (Python 3.11+)
- **Database**: PostgreSQL + SQLAlchemy (Async)
- **Authentication**: JWT + API Key
- **Deployment**: Docker

## Features

- OpenAI-compatible Chat Completions API
- SSE streaming support
- Agent management
- Usage statistics and quotas
- API Key management

## Quick Start

### 1. Clone and Install

```bash
cd asgard-api
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your settings
```

### 3. Start Database

```bash
# Using Docker
docker-compose up -d db

# Or use local PostgreSQL
```

### 4. Run Application

```bash
uvicorn app.main:app --reload
```

### 5. Access API

- **API Docs**: http://localhost:8000/docs
- **Health Check**: http://localhost:8000/health

## Docker Deployment

```bash
docker-compose up -d
```

## API Usage

### OpenAI Compatible Endpoint

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer asgard_your_api_key" \
  -d '{
    "model": "asgard/code-refactor",
    "messages": [{"role": "user", "content": "Refactor this code..."}],
    "stream": false
  }'
```

## Project Structure

```
asgard-api/
├── app/
│   ├── main.py           # FastAPI application entry
│   ├── config.py         # Settings and configuration
│   ├── models.py         # SQLAlchemy database models
│   ├── database.py       # Database connection
│   ├── auth.py           # Authentication utilities
│   ├── schemas.py        # Pydantic schemas
│   ├── routers/          # API route modules
│   │   ├── auth.py       # Authentication endpoints
│   │   ├── agents.py     # Agent management
│   │   ├── chat.py       # Chat completions (OpenAI compatible)
│   │   └── console.py    # Developer console
│   └── agents/           # Agent implementations
│       ├── base.py       # Agent base classes
│       └── impl.py       # Concrete agent implementations
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

## License

MIT
