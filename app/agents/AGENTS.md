# packages/api/app/agents/

> P2 | Parent: ../../AGENTS.md

## Module Overview

Agent engine implementations. Provides abstract base classes defining the agent execution contract, and concrete implementations for various domains (code refactoring, creative writing, business copywriting, unit testing). Supports both non-streaming and streaming responses via SSE.

---

## Member List

__init__.py: Package initialization, exports AgentEngine, PromptTemplateAgent, StructuredAgent and concrete implementations

base.py: Abstract base classes: AgentEngine (run/run_streaming abstract methods), PromptTemplateAgent (system prompt template injection), StructuredAgent (output format specification), Ollama streaming with OpenAI-compatible chunk format

impl.py: Concrete agent implementations: CodeRefactorAgent (code analysis/refactoring), HanHanStyleAgent (Chinese creative writing), BusinessCopywritingAgent (marketing copy), UnitTestAgent (test generation), each with specific system prompts and parameters

---

Rule: Members complete, one item per line, parent links valid, precise terms first