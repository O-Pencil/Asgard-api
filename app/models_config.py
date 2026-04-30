"""
Default model providers configuration for nanopencil-compatible agents.
Data sourced from Pencil-Agent-Gateway's coding-plan-presets.ts (CODING_PLAN_PRESETS).
These are Gateway-self-maintained providers NOT in the SDK's built-in MODELS catalog.
Each provider maps to the same preset data that the Gateway's nano-adapter uses
when registering custom providers via registerProvider() on the in-memory ModelRegistry.

Keep in sync with Pencil-Agent-Gateway/src/engine/coding-plan-presets.ts.
"""

DEFAULT_MODEL_PROVIDERS = {
    "dashscope-coding": {
        "baseUrl": "https://coding.dashscope.aliyuncs.com/v1",
        "api": "openai-completions",
        "models": [
            {"id": "qwen3.5-plus", "name": "Qwen3.5 Plus", "input": ["text", "image"], "contextWindow": 1000000, "maxTokens": 65536},
            {"id": "qwen3.6-plus", "name": "Qwen3.6 Plus", "input": ["text", "image"], "contextWindow": 1000000, "maxTokens": 65536},
            {"id": "qwen3-max-2026-01-23", "name": "Qwen3 Max", "input": ["text"], "contextWindow": 262144, "maxTokens": 65536},
            {"id": "qwen3-coder-next", "name": "Qwen3 Coder Next", "input": ["text"], "contextWindow": 262144, "maxTokens": 65536},
            {"id": "qwen3-coder-plus", "name": "Qwen3 Coder Plus", "input": ["text"], "contextWindow": 1000000, "maxTokens": 65536},
            {"id": "MiniMax-M2.5", "name": "MiniMax-M2.5", "input": ["text"], "contextWindow": 1000000, "maxTokens": 65536},
            {"id": "glm-5", "name": "GLM-5", "input": ["text"], "contextWindow": 202752, "maxTokens": 16384},
            {"id": "glm-4.7", "name": "GLM-4.7", "input": ["text"], "contextWindow": 202752, "maxTokens": 16384},
            {"id": "kimi-k2.5", "name": "Kimi K2.5", "input": ["text", "image"], "contextWindow": 262144, "maxTokens": 32768},
        ]
    },
    "minimax-coding": {
        "baseUrl": "https://api.minimaxi.com/v1",
        "api": "openai-completions",
        "models": [
            {"id": "MiniMax-M2.7", "name": "MiniMax M2.7", "input": ["text"], "contextWindow": 204800, "maxTokens": 65536},
            {"id": "MiniMax-M2.5", "name": "MiniMax M2.5", "input": ["text"], "contextWindow": 204800, "maxTokens": 65536},
            {"id": "MiniMax-M2.1", "name": "MiniMax M2.1", "input": ["text"], "contextWindow": 204800, "maxTokens": 65536},
            {"id": "MiniMax-M2", "name": "MiniMax M2", "input": ["text"], "contextWindow": 204800, "maxTokens": 65536},
        ]
    },
    "zhipu-coding": {
        "baseUrl": "https://open.bigmodel.cn/api/paas/v4",
        "api": "openai-completions",
        "models": [
            {"id": "glm-5", "name": "GLM-5", "input": ["text"], "contextWindow": 202752, "maxTokens": 16384},
            {"id": "glm-4.7", "name": "GLM-4.7", "input": ["text"], "contextWindow": 202752, "maxTokens": 16384},
        ]
    },
    "qianfan-coding": {
        "baseUrl": "https://qianfan.baidubce.com/v2/coding",
        "api": "openai-completions",
        "models": [
            {"id": "kimi-k2.5", "name": "Kimi K2.5 (Qianfan)", "input": ["text", "image"], "contextWindow": 262144, "maxTokens": 32768},
            {"id": "deepseek-v3.2", "name": "DeepSeek V3.2 (Qianfan)", "input": ["text"], "contextWindow": 262144, "maxTokens": 65536},
            {"id": "glm-5", "name": "GLM-5 (Qianfan)", "input": ["text"], "contextWindow": 202752, "maxTokens": 16384},
            {"id": "MiniMax-M2.5", "name": "MiniMax-M2.5 (Qianfan)", "input": ["text"], "contextWindow": 1000000, "maxTokens": 65536},
            {"id": "glm-4.7", "name": "GLM-4.7 (Qianfan)", "input": ["text"], "contextWindow": 202752, "maxTokens": 16384},
            {"id": "MiniMax-M2.1", "name": "MiniMax-M2.1 (Qianfan)", "input": ["text"], "contextWindow": 1000000, "maxTokens": 65536},
        ]
    },
    "ark-coding": {
        "baseUrl": "https://ark.cn-beijing.volces.com/api/coding/v3",
        "api": "openai-completions",
        "models": [
            {"id": "doubao-seed-2.0-code", "name": "Doubao Seed 2.0 Code (Ark)", "input": ["text"], "contextWindow": 262144, "maxTokens": 65536},
            {"id": "doubao-seed-2.0-pro", "name": "Doubao Seed 2.0 Pro (Ark)", "input": ["text"], "contextWindow": 262144, "maxTokens": 65536},
            {"id": "doubao-seed-2.0-lite", "name": "Doubao Seed 2.0 Lite (Ark)", "input": ["text"], "contextWindow": 262144, "maxTokens": 32768},
            {"id": "doubao-seed-code", "name": "Doubao Seed Code (Ark)", "input": ["text"], "contextWindow": 262144, "maxTokens": 65536},
            {"id": "minimax-m2.5", "name": "MiniMax M2.5 (Ark)", "input": ["text"], "contextWindow": 1000000, "maxTokens": 65536},
            {"id": "glm-4.7", "name": "GLM-4.7 (Ark)", "input": ["text"], "contextWindow": 202752, "maxTokens": 16384},
            {"id": "deepseek-v3.2", "name": "DeepSeek V3.2 (Ark)", "input": ["text"], "contextWindow": 262144, "maxTokens": 65536},
            {"id": "kimi-k2.5", "name": "Kimi K2.5 (Ark)", "input": ["text", "image"], "contextWindow": 262144, "maxTokens": 32768},
        ]
    },
    "anthropic-custom": {
        "baseUrl": "https://api.anthropic.com",
        "api": "anthropic-messages",
        "models": [
            {"id": "claude-sonnet-4-20250514", "name": "Claude Sonnet 4", "input": ["text", "image"], "contextWindow": 200000, "maxTokens": 16384},
            {"id": "claude-opus-4-20250514", "name": "Claude Opus 4", "input": ["text", "image"], "contextWindow": 200000, "maxTokens": 32000},
            {"id": "claude-3-5-sonnet-20241022", "name": "Claude 3.5 Sonnet", "input": ["text", "image"], "contextWindow": 200000, "maxTokens": 8192},
            {"id": "claude-3-5-haiku-20241022", "name": "Claude 3.5 Haiku", "input": ["text", "image"], "contextWindow": 200000, "maxTokens": 8192},
        ]
    },
}