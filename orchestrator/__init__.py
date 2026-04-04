"""
Local AI Orchestrator System

A Python-based orchestration system that uses a lightweight "router" model
to classify incoming tasks and dispatch them to specialized local models.

Supported task types:
- coding: Programming tasks via qwen2.5-coder
- writing: Content creation via llama3.2
- image: Image generation via ComfyUI/SDXL
- video: Video generation via AnimateDiff/LTX

Usage:
    from orchestrator import Orchestrator

    # Create orchestrator
    orch = Orchestrator()

    # Process a request (auto-routes to appropriate agent)
    response = await orch.process("Write a Python function to sort a list")

    # Or use specific agents directly
    response = await orch.code("Debug this function...")
    response = await orch.write("Write a blog post about...")
    response = await orch.image("Generate an image of...")
"""

import logging
from typing import Optional, AsyncIterator

from .models import (
    TaskType,
    OrchestratorRequest,
    AgentResponse,
    RouterDecision,
    ImageGenerationRequest,
    ImageGenerationResponse,
    VideoGenerationRequest,
    VideoGenerationResponse,
)
from .config import OrchestratorConfig, get_default_config, load_config
from .router import Router
from .agents import CodeAgent, WriteAgent, ImageAgent, VideoAgent

__version__ = "0.1.0"
__all__ = [
    "Orchestrator",
    "TaskType",
    "OrchestratorRequest",
    "AgentResponse",
    "RouterDecision",
    "ImageGenerationRequest",
    "ImageGenerationResponse",
    "VideoGenerationRequest",
    "VideoGenerationResponse",
]

logger = logging.getLogger(__name__)


class Orchestrator:
    """
    Main orchestrator that routes requests to specialized agents.

    The orchestrator uses a lightweight router model to classify incoming
    requests and dispatch them to the appropriate specialized agent.
    """

    def __init__(self, config: Optional[OrchestratorConfig] = None):
        """
        Initialize the orchestrator.

        Args:
            config: Optional configuration. If not provided, uses defaults.
        """
        self.config = config or get_default_config()

        # Initialize router
        self.router = Router(self.config)

        # Initialize agents (lazy loading)
        self._code_agent: Optional[CodeAgent] = None
        self._write_agent: Optional[WriteAgent] = None
        self._image_agent: Optional[ImageAgent] = None
        self._video_agent: Optional[VideoAgent] = None

        # Setup logging
        logging.basicConfig(
            level=getattr(logging, self.config.log_level),
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )

    @property
    def code_agent(self) -> CodeAgent:
        """Lazy-load the code agent."""
        if self._code_agent is None:
            self._code_agent = CodeAgent(self.config)
        return self._code_agent

    @property
    def write_agent(self) -> WriteAgent:
        """Lazy-load the write agent."""
        if self._write_agent is None:
            self._write_agent = WriteAgent(self.config)
        return self._write_agent

    @property
    def image_agent(self) -> ImageAgent:
        """Lazy-load the image agent."""
        if self._image_agent is None:
            self._image_agent = ImageAgent(self.config)
        return self._image_agent

    @property
    def video_agent(self) -> VideoAgent:
        """Lazy-load the video agent."""
        if self._video_agent is None:
            self._video_agent = VideoAgent(self.config)
        return self._video_agent

    async def process(
        self,
        prompt: str,
        context: Optional[str] = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        stream: bool = False
    ) -> AgentResponse:
        """
        Process a request by routing it to the appropriate agent.

        Args:
            prompt: The user's request
            context: Optional additional context
            max_tokens: Maximum tokens for response
            temperature: Sampling temperature
            stream: Whether to stream the response

        Returns:
            AgentResponse from the selected agent
        """
        request = OrchestratorRequest(
            prompt=prompt,
            context=context,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=stream
        )

        # Route the request
        decision = await self.router.classify(request)
        logger.info(
            f"Routed to {decision.task_type.value} "
            f"(confidence: {decision.confidence:.2f}): {decision.reasoning}"
        )

        # Dispatch to appropriate agent
        agent = self._get_agent(decision.task_type)
        response = await agent.process(request)

        # Add routing info to metadata
        response.metadata["routing"] = {
            "task_type": decision.task_type.value,
            "confidence": decision.confidence,
            "reasoning": decision.reasoning,
        }

        return response

    def _get_agent(self, task_type: TaskType):
        """Get the agent for a given task type."""
        agents = {
            TaskType.CODING: self.code_agent,
            TaskType.WRITING: self.write_agent,
            TaskType.IMAGE: self.image_agent,
            TaskType.VIDEO: self.video_agent,
            TaskType.GENERAL: self.write_agent,  # Use write agent for general tasks
        }
        return agents.get(task_type, self.write_agent)

    async def route(self, prompt: str) -> RouterDecision:
        """
        Route a prompt without processing it.

        Useful for checking how a request would be classified.

        Args:
            prompt: The request to classify

        Returns:
            RouterDecision with task_type, confidence, and reasoning
        """
        request = OrchestratorRequest(prompt=prompt)
        return await self.router.classify(request)

    # Direct agent access methods

    async def code(
        self,
        prompt: str,
        context: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.3
    ) -> AgentResponse:
        """Direct access to the code agent."""
        request = OrchestratorRequest(
            prompt=prompt,
            context=context,
            max_tokens=max_tokens,
            temperature=temperature
        )
        return await self.code_agent.process(request)

    async def write(
        self,
        prompt: str,
        context: Optional[str] = None,
        max_tokens: int = 2048,
        temperature: float = 0.7
    ) -> AgentResponse:
        """Direct access to the write agent."""
        request = OrchestratorRequest(
            prompt=prompt,
            context=context,
            max_tokens=max_tokens,
            temperature=temperature
        )
        return await self.write_agent.process(request)

    async def image(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 1024,
        steps: int = 20,
        seed: int = -1
    ) -> AgentResponse:
        """Direct access to the image agent."""
        request = OrchestratorRequest(prompt=prompt)
        return await self.image_agent.process(request)

    async def video(
        self,
        prompt: str,
        frames: int = 16,
        fps: int = 8,
        seed: int = -1
    ) -> AgentResponse:
        """Direct access to the video agent."""
        request = OrchestratorRequest(prompt=prompt)
        return await self.video_agent.process(request)

    # Utility methods

    async def check_status(self) -> dict:
        """Check the status of all services."""
        import httpx

        status = {
            "ollama": {"status": "unknown"},
            "comfyui": {"status": "unknown"},
            "models_loaded": [],
        }

        # Check Ollama
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.config.ollama_base_url}/api/tags")
                if response.status_code == 200:
                    data = response.json()
                    status["ollama"] = {"status": "online"}
                    status["models_loaded"] = [m["name"] for m in data.get("models", [])]
        except Exception as e:
            status["ollama"] = {"status": "offline", "error": str(e)}

        # Check ComfyUI
        status["comfyui"] = await self.image_agent.check_comfyui_status()

        return status

    def process_sync(
        self,
        prompt: str,
        context: Optional[str] = None,
        max_tokens: int = 2048,
        temperature: float = 0.7
    ) -> AgentResponse:
        """Synchronous version of process for non-async contexts."""
        import asyncio
        return asyncio.run(
            self.process(prompt, context, max_tokens, temperature)
        )


# Convenience function for quick usage
async def ask(prompt: str, config: Optional[OrchestratorConfig] = None) -> str:
    """
    Quick function to process a prompt and return the response content.

    Args:
        prompt: The request to process
        config: Optional configuration

    Returns:
        The response content as a string
    """
    orchestrator = Orchestrator(config)
    response = await orchestrator.process(prompt)
    return response.content
