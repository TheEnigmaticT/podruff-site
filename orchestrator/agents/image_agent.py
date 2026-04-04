"""Image agent for generating images via ComfyUI."""

import logging
import time
from pathlib import Path
from typing import Optional
from enum import Enum

from .base import BaseAgent
from ..models import (
    TaskType,
    OrchestratorRequest,
    AgentResponse,
    ModelConfig,
    ImageGenerationRequest,
    ImageGenerationResponse,
)
from ..config import OrchestratorConfig, get_default_config, IMAGE_AGENT_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class ImageStyle(str, Enum):
    """Predefined image styles."""
    PHOTOREALISTIC = "photorealistic"
    DIGITAL_ART = "digital_art"
    ANIME = "anime"
    OIL_PAINTING = "oil_painting"
    WATERCOLOR = "watercolor"
    SKETCH = "sketch"
    CINEMATIC = "cinematic"
    FANTASY = "fantasy"
    MINIMALIST = "minimalist"


class ImageAspectRatio(str, Enum):
    """Common aspect ratios."""
    SQUARE = "1:1"       # 1024x1024
    LANDSCAPE = "16:9"   # 1024x576
    PORTRAIT = "9:16"    # 576x1024
    WIDE = "21:9"        # 1024x439
    PHOTO = "4:3"        # 1024x768


ASPECT_RATIO_SIZES = {
    ImageAspectRatio.SQUARE: (1024, 1024),
    ImageAspectRatio.LANDSCAPE: (1024, 576),
    ImageAspectRatio.PORTRAIT: (576, 1024),
    ImageAspectRatio.WIDE: (1024, 440),
    ImageAspectRatio.PHOTO: (1024, 768),
}


STYLE_MODIFIERS = {
    ImageStyle.PHOTOREALISTIC: "highly detailed photograph, photorealistic, 8k, sharp focus, professional photography",
    ImageStyle.DIGITAL_ART: "digital art, highly detailed, vibrant colors, artstation trending",
    ImageStyle.ANIME: "anime style, studio ghibli, detailed, vibrant, clean lines",
    ImageStyle.OIL_PAINTING: "oil painting, classical art style, masterpiece, detailed brushwork",
    ImageStyle.WATERCOLOR: "watercolor painting, soft colors, artistic, delicate",
    ImageStyle.SKETCH: "pencil sketch, detailed linework, artistic, black and white",
    ImageStyle.CINEMATIC: "cinematic shot, dramatic lighting, movie still, 35mm film",
    ImageStyle.FANTASY: "fantasy art, magical, ethereal, detailed, imaginative",
    ImageStyle.MINIMALIST: "minimalist, clean, simple, elegant design",
}


class ImageAgent(BaseAgent):
    """Specialized agent for image generation using ComfyUI."""

    task_type = TaskType.IMAGE

    def __init__(self, config: Optional[OrchestratorConfig] = None):
        super().__init__(config)
        self.comfyui_url = self.config.comfyui_base_url
        self.output_dir = Path(self.config.output_dir) / "images"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Lazy import of ComfyUI client
        self._comfyui_client = None

    def _get_model_config(self) -> ModelConfig:
        """Return the model config (uses write model for prompt enhancement)."""
        return ModelConfig(
            name="Image Prompt Enhancer",
            ollama_name=self.config.write_model.ollama_name,
            context_length=2048,
            temperature=0.7,
            system_prompt=IMAGE_AGENT_SYSTEM_PROMPT,
            description="Uses language model to enhance image prompts"
        )

    @property
    def comfyui_client(self):
        """Lazy load the ComfyUI client."""
        if self._comfyui_client is None:
            from ..comfyui.client import ComfyUIClient
            self._comfyui_client = ComfyUIClient(self.comfyui_url)
        return self._comfyui_client

    async def process(self, request: OrchestratorRequest) -> AgentResponse:
        """Process an image generation request."""
        start_time = time.time()

        # Enhance the prompt using LLM
        enhanced_prompt = await self._enhance_prompt(request.prompt)

        # Create image generation request
        image_request = ImageGenerationRequest(
            prompt=enhanced_prompt,
            negative_prompt="blurry, bad quality, distorted, ugly, deformed, disfigured, low quality, pixelated",
        )

        # Generate image via ComfyUI
        try:
            result = await self.generate_image(image_request)
            generation_time = time.time() - start_time

            return AgentResponse(
                task_type=self.task_type,
                content=f"Generated image saved to: {', '.join(result.image_paths)}",
                model_used="SDXL via ComfyUI",
                generation_time=generation_time,
                metadata={
                    "image_paths": result.image_paths,
                    "enhanced_prompt": enhanced_prompt,
                    "original_prompt": request.prompt,
                    "seed": result.seed_used,
                }
            )
        except Exception as e:
            logger.error(f"Image generation failed: {e}")
            # Return the enhanced prompt even if generation fails
            return AgentResponse(
                task_type=self.task_type,
                content=f"Image generation failed. Enhanced prompt was: {enhanced_prompt}\n\nError: {str(e)}",
                model_used="ComfyUI (failed)",
                generation_time=time.time() - start_time,
                metadata={
                    "error": str(e),
                    "enhanced_prompt": enhanced_prompt,
                    "original_prompt": request.prompt,
                }
            )

    async def _enhance_prompt(self, prompt: str) -> str:
        """Enhance the user's prompt for better image generation."""
        enhancement_prompt = f"""Enhance this image generation prompt to be more detailed and effective.
Add artistic style, lighting, composition, and quality modifiers.
Keep the core subject but make it more descriptive for AI image generation.
Return ONLY the enhanced prompt, nothing else.

Original prompt: {prompt}

Enhanced prompt:"""

        try:
            enhanced = await self._generate(OrchestratorRequest(
                prompt=enhancement_prompt,
                max_tokens=300,
                temperature=0.7
            ))
            return enhanced.strip()
        except Exception as e:
            logger.warning(f"Prompt enhancement failed: {e}, using original")
            return prompt

    async def generate_image(
        self,
        request: ImageGenerationRequest
    ) -> ImageGenerationResponse:
        """Generate an image using ComfyUI."""
        return await self.comfyui_client.generate_image(request)

    async def generate_with_style(
        self,
        prompt: str,
        style: ImageStyle = ImageStyle.DIGITAL_ART,
        aspect_ratio: ImageAspectRatio = ImageAspectRatio.SQUARE,
        steps: int = 20,
        cfg_scale: float = 7.0,
        seed: int = -1
    ) -> ImageGenerationResponse:
        """Generate an image with a predefined style."""
        # Add style modifiers to prompt
        styled_prompt = f"{prompt}, {STYLE_MODIFIERS[style]}"

        # Get dimensions for aspect ratio
        width, height = ASPECT_RATIO_SIZES[aspect_ratio]

        request = ImageGenerationRequest(
            prompt=styled_prompt,
            width=width,
            height=height,
            steps=steps,
            cfg_scale=cfg_scale,
            seed=seed
        )

        return await self.generate_image(request)

    async def generate_variations(
        self,
        prompt: str,
        count: int = 4,
        vary_seed: bool = True
    ) -> list[ImageGenerationResponse]:
        """Generate multiple variations of an image."""
        import random
        results = []

        base_seed = random.randint(0, 2**32 - 1) if vary_seed else 42

        for i in range(count):
            seed = base_seed + i if vary_seed else base_seed
            request = ImageGenerationRequest(
                prompt=prompt,
                seed=seed
            )
            result = await self.generate_image(request)
            results.append(result)

        return results

    def build_prompt(
        self,
        subject: str,
        style: Optional[ImageStyle] = None,
        environment: Optional[str] = None,
        lighting: Optional[str] = None,
        camera: Optional[str] = None,
        additional: Optional[str] = None
    ) -> str:
        """Build a detailed image prompt from components."""
        parts = [subject]

        if environment:
            parts.append(f"in {environment}")

        if style:
            parts.append(STYLE_MODIFIERS[style])

        if lighting:
            parts.append(f"{lighting} lighting")

        if camera:
            parts.append(f"{camera}")

        if additional:
            parts.append(additional)

        # Add quality modifiers
        parts.append("highly detailed, best quality")

        return ", ".join(parts)

    async def check_comfyui_status(self) -> dict:
        """Check if ComfyUI is running and available."""
        return await self.comfyui_client.check_status()
