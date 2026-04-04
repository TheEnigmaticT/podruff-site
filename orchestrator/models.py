"""Pydantic models for the AI Orchestrator system."""

from enum import Enum
from typing import Optional, Any
from pydantic import BaseModel, Field
from datetime import datetime


class TaskType(str, Enum):
    """Types of tasks the orchestrator can handle."""
    CODING = "coding"
    WRITING = "writing"
    IMAGE = "image"
    VIDEO = "video"
    GENERAL = "general"


class RouterDecision(BaseModel):
    """Decision made by the router model."""
    task_type: TaskType
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    original_request: str


class OrchestratorRequest(BaseModel):
    """Incoming request to the orchestrator."""
    prompt: str
    context: Optional[str] = None
    max_tokens: int = 2048
    temperature: float = 0.7
    stream: bool = False


class AgentResponse(BaseModel):
    """Response from any agent."""
    task_type: TaskType
    content: str
    model_used: str
    tokens_used: Optional[int] = None
    generation_time: Optional[float] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ImageGenerationRequest(BaseModel):
    """Request for image generation via ComfyUI."""
    prompt: str
    negative_prompt: str = "blurry, bad quality, distorted"
    width: int = 1024
    height: int = 1024
    steps: int = 20
    cfg_scale: float = 7.0
    seed: int = -1  # -1 for random
    sampler: str = "euler"
    scheduler: str = "normal"
    checkpoint: str = "sd_xl_base_1.0.safetensors"


class ImageGenerationResponse(BaseModel):
    """Response from image generation."""
    task_type: TaskType = TaskType.IMAGE
    image_paths: list[str]
    prompt: str
    seed_used: int
    generation_time: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class VideoGenerationRequest(BaseModel):
    """Request for video generation via ComfyUI/AnimateDiff."""
    prompt: str
    negative_prompt: str = "blurry, bad quality, distorted"
    width: int = 512
    height: int = 512
    frames: int = 16
    fps: int = 8
    steps: int = 20
    cfg_scale: float = 7.0
    seed: int = -1
    motion_module: str = "mm_sd_v15_v2.ckpt"


class VideoGenerationResponse(BaseModel):
    """Response from video generation."""
    task_type: TaskType = TaskType.VIDEO
    video_path: str
    frames_generated: int
    fps: int
    prompt: str
    seed_used: int
    generation_time: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelConfig(BaseModel):
    """Configuration for a specific model."""
    name: str
    ollama_name: str
    context_length: int = 4096
    temperature: float = 0.7
    system_prompt: str = ""
    description: str = ""


class OrchestratorConfig(BaseModel):
    """Overall orchestrator configuration."""
    router_model: ModelConfig
    code_model: ModelConfig
    write_model: ModelConfig
    ollama_base_url: str = "http://localhost:11434"
    comfyui_base_url: str = "http://localhost:8188"
    output_dir: str = "outputs"
    log_level: str = "INFO"


class ConversationMessage(BaseModel):
    """A message in a conversation."""
    role: str  # "user", "assistant", or "system"
    content: str
    timestamp: datetime = Field(default_factory=datetime.now)


class ConversationHistory(BaseModel):
    """Conversation history for context-aware responses."""
    messages: list[ConversationMessage] = Field(default_factory=list)

    def add_message(self, role: str, content: str) -> None:
        """Add a message to the history."""
        self.messages.append(ConversationMessage(role=role, content=content))

    def get_context(self, max_messages: int = 10) -> list[dict[str, str]]:
        """Get recent messages as context."""
        recent = self.messages[-max_messages:]
        return [{"role": m.role, "content": m.content} for m in recent]

    def clear(self) -> None:
        """Clear the conversation history."""
        self.messages.clear()
