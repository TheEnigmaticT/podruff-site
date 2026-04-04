"""Configuration management for the AI Orchestrator."""

import os
from pathlib import Path
from typing import Optional
from pydantic import BaseModel
from .models import ModelConfig, OrchestratorConfig


# Base paths
PROJECT_ROOT = Path(__file__).parent
CONFIGS_DIR = PROJECT_ROOT / "configs"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"


# Default system prompts
ROUTER_SYSTEM_PROMPT = """You are a task classification assistant. Your job is to analyze user requests and classify them into exactly one category.

Categories:
- coding: Programming tasks, debugging, code review, technical implementation, scripts, APIs
- writing: Essays, documentation, creative writing, emails, summaries, translations
- image: Image generation, artwork creation, visual content, photos, illustrations
- video: Video generation, animation, motion graphics, video editing requests
- general: Questions, explanations, conversations that don't fit other categories

Respond ONLY with valid JSON in this exact format:
{"task_type": "<category>", "confidence": <0.0-1.0>, "reasoning": "<brief explanation>"}

Examples:
User: "Write a Python function to sort a list"
{"task_type": "coding", "confidence": 0.95, "reasoning": "Request for Python code implementation"}

User: "Generate an image of a sunset over mountains"
{"task_type": "image", "confidence": 0.98, "reasoning": "Explicit request for image generation"}

User: "Write a haiku about programming"
{"task_type": "writing", "confidence": 0.9, "reasoning": "Creative writing request"}

User: "Create a short video of a bouncing ball"
{"task_type": "video", "confidence": 0.95, "reasoning": "Request for video/animation generation"}

User: "What is the capital of France?"
{"task_type": "general", "confidence": 0.85, "reasoning": "General knowledge question"}
"""

CODE_AGENT_SYSTEM_PROMPT = """You are an expert software engineer and coding assistant. You excel at:
- Writing clean, efficient, well-documented code
- Debugging and fixing issues
- Code review and optimization
- Explaining technical concepts
- Implementing algorithms and data structures

Guidelines:
- Always provide working, tested code when possible
- Include comments for complex logic
- Follow best practices and coding standards
- Consider edge cases and error handling
- Use appropriate design patterns
- Prefer readability over cleverness

When writing code:
1. Start with a brief explanation of your approach
2. Provide the implementation
3. Explain any non-obvious decisions
4. Suggest tests or improvements if relevant
"""

WRITE_AGENT_SYSTEM_PROMPT = """You are a skilled writer and communicator. You excel at:
- Creative writing (stories, poetry, scripts)
- Technical documentation
- Professional communication (emails, proposals)
- Content editing and refinement
- Adapting tone and style for different audiences

Guidelines:
- Match the requested tone and style
- Structure content logically
- Use clear, engaging language
- Be concise but thorough
- Proofread for errors
- Consider the target audience

For creative writing:
- Develop vivid descriptions
- Create engaging narratives
- Use literary devices appropriately

For technical writing:
- Be precise and accurate
- Use proper terminology
- Include examples when helpful
"""

IMAGE_AGENT_SYSTEM_PROMPT = """You are an expert at crafting prompts for AI image generation. Your job is to:
- Enhance and expand user image requests into detailed prompts
- Add artistic style, lighting, composition details
- Suggest appropriate negative prompts
- Recommend suitable parameters (steps, CFG, etc.)

When creating prompts:
- Be specific about subject, style, lighting, composition
- Include quality modifiers (highly detailed, professional, etc.)
- Mention artistic influences when relevant
- Structure: subject, environment, style, lighting, camera angle, quality
"""

VIDEO_AGENT_SYSTEM_PROMPT = """You are an expert at video generation and animation. You help users create:
- Short animated clips
- Motion graphics
- Video transitions
- AI-generated video content

When processing requests:
- Determine appropriate video length and frame count
- Suggest motion styles and effects
- Optimize prompts for video generation models
- Consider frame interpolation for smoother output

Supported backends:
- AnimateDiff (via ComfyUI) - good for stylized animation
- LTX-2 I2V - excellent for image-to-video generation
- Stable Video Diffusion - high quality but resource intensive
"""


def get_default_config() -> OrchestratorConfig:
    """Get the default orchestrator configuration."""
    return OrchestratorConfig(
        router_model=ModelConfig(
            name="Router",
            ollama_name="phi3:mini",
            context_length=4096,
            temperature=0.1,  # Low temperature for consistent classification
            system_prompt=ROUTER_SYSTEM_PROMPT,
            description="Lightweight model for fast task classification"
        ),
        code_model=ModelConfig(
            name="Code Agent",
            ollama_name="qwen2.5-coder:14b",
            context_length=16384,
            temperature=0.3,  # Lower temperature for more deterministic code
            system_prompt=CODE_AGENT_SYSTEM_PROMPT,
            description="Specialized coding model for software development tasks"
        ),
        write_model=ModelConfig(
            name="Write Agent",
            ollama_name="llama3.2:latest",
            context_length=4096,
            temperature=0.7,  # Higher temperature for creative writing
            system_prompt=WRITE_AGENT_SYSTEM_PROMPT,
            description="General purpose model optimized for writing tasks"
        ),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        comfyui_base_url=os.getenv("COMFYUI_BASE_URL", "http://localhost:8188"),
        output_dir=str(OUTPUTS_DIR),
        log_level=os.getenv("LOG_LEVEL", "INFO")
    )


def load_config(config_path: Optional[Path] = None) -> OrchestratorConfig:
    """Load configuration from file or return defaults."""
    if config_path and config_path.exists():
        import json
        with open(config_path) as f:
            data = json.load(f)
        return OrchestratorConfig(**data)
    return get_default_config()


def save_config(config: OrchestratorConfig, config_path: Path) -> None:
    """Save configuration to file."""
    import json
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(config.model_dump(), f, indent=2)


# Model alternatives for different use cases
ALTERNATIVE_MODELS = {
    "router": [
        ("phi3:mini", "2.3GB - Fast, good classification"),
        ("llama3.2:1b", "1.3GB - Fastest, simpler tasks"),
        ("qwen2.5:3b", "2GB - Good balance"),
    ],
    "coding": [
        ("qwen2.5-coder:7b", "4.7GB - Excellent code quality"),
        ("deepseek-coder:6.7b", "4GB - Good alternative"),
        ("codellama:7b", "4GB - Meta's code model"),
        ("qwen2.5-coder:14b", "9GB - Higher quality, more RAM"),
    ],
    "writing": [
        ("llama3.2:latest", "2GB - Good general writing"),
        ("mistral:7b", "4GB - Strong reasoning"),
        ("llama3.2:3b", "2GB - Faster, good quality"),
        ("phi3:medium", "8GB - Better for complex tasks"),
    ],
    "video": [
        ("animatediff", "AnimateDiff via ComfyUI"),
        ("ltx-2-i2v", "LTX-2 Image-to-Video - excellent quality"),
        ("svd", "Stable Video Diffusion - resource intensive"),
    ]
}


# ComfyUI workflow templates
COMFYUI_WORKFLOWS = {
    "sdxl_basic": "basic_sdxl_workflow.json",
    "sdxl_refiner": "sdxl_with_refiner.json",
    "animatediff_basic": "animatediff_basic.json",
    "animatediff_advanced": "animatediff_advanced.json",
    "ltx_i2v": "ltx_image_to_video.json",
}
