"""Base agent class for all specialized agents."""

import time
import logging
from abc import ABC, abstractmethod
from typing import Optional, AsyncIterator

import httpx

from ..models import (
    TaskType,
    OrchestratorRequest,
    AgentResponse,
    ModelConfig,
    ConversationHistory,
)
from ..config import OrchestratorConfig, get_default_config

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Base class for all specialized agents."""

    task_type: TaskType = TaskType.GENERAL

    def __init__(self, config: Optional[OrchestratorConfig] = None):
        self.config = config or get_default_config()
        self.model_config = self._get_model_config()
        self.ollama_url = f"{self.config.ollama_base_url}/api/generate"
        self.chat_url = f"{self.config.ollama_base_url}/api/chat"
        self.conversation = ConversationHistory()

    @abstractmethod
    def _get_model_config(self) -> ModelConfig:
        """Return the model configuration for this agent."""
        pass

    async def process(self, request: OrchestratorRequest) -> AgentResponse:
        """Process a request and return a response."""
        start_time = time.time()

        try:
            if request.stream:
                # For streaming, collect all chunks
                content = ""
                async for chunk in self.stream(request):
                    content += chunk
            else:
                content = await self._generate(request)

            generation_time = time.time() - start_time

            # Update conversation history
            self.conversation.add_message("user", request.prompt)
            self.conversation.add_message("assistant", content)

            return AgentResponse(
                task_type=self.task_type,
                content=content,
                model_used=self.model_config.ollama_name,
                generation_time=generation_time,
                metadata={
                    "temperature": self.model_config.temperature,
                    "context_length": self.model_config.context_length,
                }
            )
        except Exception as e:
            logger.error(f"Error in {self.__class__.__name__}: {e}")
            raise

    async def _generate(self, request: OrchestratorRequest) -> str:
        """Generate a response using the Ollama API."""
        # Build prompt with context if available
        full_prompt = self._build_prompt(request)

        payload = {
            "model": self.model_config.ollama_name,
            "prompt": full_prompt,
            "system": self.model_config.system_prompt,
            "stream": False,
            "options": {
                "temperature": request.temperature or self.model_config.temperature,
                "num_ctx": self.model_config.context_length,
                "num_predict": request.max_tokens,
            }
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(self.ollama_url, json=payload)
            response.raise_for_status()

        result = response.json()
        return result.get("response", "")

    async def stream(self, request: OrchestratorRequest) -> AsyncIterator[str]:
        """Stream a response using the Ollama API."""
        full_prompt = self._build_prompt(request)

        payload = {
            "model": self.model_config.ollama_name,
            "prompt": full_prompt,
            "system": self.model_config.system_prompt,
            "stream": True,
            "options": {
                "temperature": request.temperature or self.model_config.temperature,
                "num_ctx": self.model_config.context_length,
                "num_predict": request.max_tokens,
            }
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", self.ollama_url, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line:
                        import json
                        try:
                            data = json.loads(line)
                            if "response" in data:
                                yield data["response"]
                        except json.JSONDecodeError:
                            continue

    def _build_prompt(self, request: OrchestratorRequest) -> str:
        """Build the full prompt including context."""
        parts = []

        # Add conversation context if available
        if self.conversation.messages:
            context_messages = self.conversation.get_context(max_messages=6)
            for msg in context_messages:
                parts.append(f"{msg['role'].capitalize()}: {msg['content']}")

        # Add explicit context if provided
        if request.context:
            parts.append(f"Context: {request.context}")

        # Add the current prompt
        parts.append(request.prompt)

        return "\n\n".join(parts)

    async def chat(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 2048,
        temperature: Optional[float] = None
    ) -> str:
        """Chat with the model using conversation format."""
        # Prepend system message
        full_messages = [
            {"role": "system", "content": self.model_config.system_prompt}
        ] + messages

        payload = {
            "model": self.model_config.ollama_name,
            "messages": full_messages,
            "stream": False,
            "options": {
                "temperature": temperature or self.model_config.temperature,
                "num_ctx": self.model_config.context_length,
                "num_predict": max_tokens,
            }
        }

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(self.chat_url, json=payload)
            response.raise_for_status()

        result = response.json()
        return result.get("message", {}).get("content", "")

    def clear_history(self) -> None:
        """Clear the conversation history."""
        self.conversation.clear()

    def process_sync(self, request: OrchestratorRequest) -> AgentResponse:
        """Synchronous version of process for non-async contexts."""
        import asyncio
        return asyncio.run(self.process(request))
