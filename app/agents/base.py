from abc import ABC, abstractmethod
from typing import List, AsyncGenerator, Dict, Any
from app.schemas import Message


class AgentEngine(ABC):
    """Base class for all Agent implementations"""

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description

    @abstractmethod
    async def run(
        self,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs
    ) -> str:
        """Execute agent and return response"""
        pass

    async def run_streaming(
        self,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Execute agent with streaming response"""
        response = await self.run(messages, temperature, max_tokens, **kwargs)

        # Tokenize response (simple word-by-word streaming)
        words = response.split()
        for i, word in enumerate(words):
            chunk = {
                "id": f"chunk-{i}",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": self.name,
                "choices": [{
                    "index": 0,
                    "delta": {"content": word + " "},
                    "finish_reason": None if i < len(words) - 1 else "stop"
                }]
            }
            yield chunk
            await self._simulate_delay()

    async def _simulate_delay(self):
        """Simulate streaming delay"""
        import asyncio
        await asyncio.sleep(0.01)

    def _format_messages(self, messages: List[Message]) -> str:
        """Format messages for processing"""
        formatted = []
        for msg in messages:
            formatted.append(f"[{msg.role.upper()}]\n{msg.content}")
        return "\n\n".join(formatted)


class PromptTemplateAgent(AgentEngine):
    """Base class for prompt-based agents"""

    def __init__(
        self,
        name: str,
        system_prompt: str,
        description: str = ""
    ):
        super().__init__(name, description)
        self.system_prompt = system_prompt

    async def run(
        self,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs
    ) -> str:
        """Execute prompt-based agent"""
        # Find system message or add default
        conversation = []
        conversation.append(f"SYSTEM PROMPT: {self.system_prompt}")
        conversation.append("")
        conversation.append("CONVERSATION:")
        conversation.append(self._format_messages(messages))

        prompt = "\n".join(conversation)

        # In production, this would call an LLM
        # For MVP, we simulate the response
        return await self._process_with_llm(prompt, temperature, max_tokens)

    async def _process_with_llm(
        self,
        prompt: str,
        temperature: float,
        max_tokens: int
    ) -> str:
        """Process prompt with LLM (to be implemented with actual LLM call)"""
        # Placeholder - in production, integrate with OpenAI/Anthropic API
        return await self._simulate_llm_response(prompt)

    async def _simulate_llm_response(self, prompt: str) -> str:
        """Simulate LLM response for MVP"""
        await self._simulate_delay()
        return f"[{self.name}] Processed: {prompt[:100]}..."


class StructuredAgent(PromptTemplateAgent):
    """Agent that returns structured output"""

    def __init__(
        self,
        name: str,
        system_prompt: str,
        output_format: str,
        description: str = ""
    ):
        super().__init__(name, system_prompt, description)
        self.output_format = output_format

    async def run(
        self,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs
    ) -> str:
        response = await super().run(messages, temperature, max_tokens, **kwargs)
        return self._format_output(response)

    def _format_output(self, content: str) -> str:
        """Format output according to defined format"""
        return content
