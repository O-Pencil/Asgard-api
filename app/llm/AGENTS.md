# packages/api/app/llm/

> P2 | Parent: ../../AGENTS.md

## Module Overview

LLM provider integrations. Currently implements Ollama provider for local LLM inference, with support for both non-streaming and streaming chat completions.

---

## Member List

__init__.py: LLM provider package initialization, exports provider factory functions and classes

ollama.py: Ollama provider implementation, provides get_ollama_provider() singleton, OllamaProvider class with chat_once() for single response, chat() for async generator streaming responses, handles connection errors and falls back to simulation when Ollama unavailable, supports temperature and max_tokens parameters

---

Rule: Members complete, one item per line, parent links valid, precise terms first