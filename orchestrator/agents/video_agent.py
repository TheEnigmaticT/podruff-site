"""Video agent for generating videos via ComfyUI/AnimateDiff/LTX."""

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
    VideoGenerationRequest,
    VideoGenerationResponse,
)
from ..config import OrchestratorConfig, VIDEO_AGENT_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class VideoBackend(str, Enum):
    """Available video generation backends."""
    ANIMATEDIFF = "animatediff"
    LTX_I2V = "ltx_i2v"  # LTX-2 Image-to-Video
    SVD = "svd"  # Stable Video Diffusion
    KLING = "kling"  # Kling for video puppetry/face substitution


class MotionStyle(str, Enum):
    """Motion styles for AnimateDiff."""
    SMOOTH = "smooth"
    DYNAMIC = "dynamic"
    SUBTLE = "subtle"
    CINEMATIC = "cinematic"
    LOOP = "loop"


class VideoLength(str, Enum):
    """Predefined video lengths."""
    SHORT = "short"     # 2 seconds, 16 frames
    MEDIUM = "medium"   # 4 seconds, 32 frames
    LONG = "long"       # 8 seconds, 64 frames


VIDEO_LENGTH_FRAMES = {
    VideoLength.SHORT: (16, 8),    # frames, fps
    VideoLength.MEDIUM: (32, 8),
    VideoLength.LONG: (64, 8),
}


class VideoAgent(BaseAgent):
    """Specialized agent for video generation using ComfyUI/AnimateDiff/LTX."""

    task_type = TaskType.VIDEO

    def __init__(self, config: Optional[OrchestratorConfig] = None):
        super().__init__(config)
        self.comfyui_url = self.config.comfyui_base_url
        self.output_dir = Path(self.config.output_dir) / "videos"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.default_backend = VideoBackend.ANIMATEDIFF

        # Lazy import of ComfyUI client
        self._comfyui_client = None

    def _get_model_config(self) -> ModelConfig:
        """Return the model config (uses write model for prompt enhancement)."""
        return ModelConfig(
            name="Video Prompt Enhancer",
            ollama_name=self.config.write_model.ollama_name,
            context_length=2048,
            temperature=0.7,
            system_prompt=VIDEO_AGENT_SYSTEM_PROMPT,
            description="Uses language model to enhance video prompts"
        )

    @property
    def comfyui_client(self):
        """Lazy load the ComfyUI client."""
        if self._comfyui_client is None:
            from ..comfyui.client import ComfyUIClient
            self._comfyui_client = ComfyUIClient(self.comfyui_url)
        return self._comfyui_client

    async def process(self, request: OrchestratorRequest) -> AgentResponse:
        """Process a video generation request."""
        start_time = time.time()

        # Analyze the request to determine backend and parameters
        analysis = await self._analyze_request(request.prompt)

        # Enhance the prompt for video generation
        enhanced_prompt = await self._enhance_prompt(request.prompt, analysis)

        # Determine backend and create request
        backend = self._select_backend(request.prompt, analysis)

        # Create video generation request
        frames, fps = VIDEO_LENGTH_FRAMES[VideoLength.SHORT]
        video_request = VideoGenerationRequest(
            prompt=enhanced_prompt,
            negative_prompt="blurry, bad quality, distorted, static, no motion, frozen",
            frames=frames,
            fps=fps,
        )

        # Generate video
        try:
            result = await self.generate_video(video_request, backend)
            generation_time = time.time() - start_time

            return AgentResponse(
                task_type=self.task_type,
                content=f"Generated video saved to: {result.video_path}",
                model_used=f"{backend.value} via ComfyUI",
                generation_time=generation_time,
                metadata={
                    "video_path": result.video_path,
                    "enhanced_prompt": enhanced_prompt,
                    "original_prompt": request.prompt,
                    "backend": backend.value,
                    "frames": result.frames_generated,
                    "fps": result.fps,
                    "seed": result.seed_used,
                }
            )
        except Exception as e:
            logger.error(f"Video generation failed: {e}")
            return AgentResponse(
                task_type=self.task_type,
                content=f"Video generation failed. Enhanced prompt was: {enhanced_prompt}\n\nError: {str(e)}",
                model_used=f"{backend.value} (failed)",
                generation_time=time.time() - start_time,
                metadata={
                    "error": str(e),
                    "enhanced_prompt": enhanced_prompt,
                    "original_prompt": request.prompt,
                    "backend": backend.value,
                }
            )

    async def _analyze_request(self, prompt: str) -> dict:
        """Analyze the request to determine optimal parameters."""
        analysis_prompt = f"""Analyze this video generation request and provide parameters.
Return JSON with: motion_intensity (low/medium/high), suggested_frames (16/32/64), needs_i2v (true/false)

Request: {prompt}

JSON:"""

        try:
            response = await self._generate(OrchestratorRequest(
                prompt=analysis_prompt,
                max_tokens=100,
                temperature=0.3
            ))

            import json
            import re
            json_match = re.search(r'\{[^{}]*\}', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
        except Exception as e:
            logger.warning(f"Request analysis failed: {e}")

        return {
            "motion_intensity": "medium",
            "suggested_frames": 16,
            "needs_i2v": False
        }

    async def _enhance_prompt(self, prompt: str, analysis: dict) -> str:
        """Enhance the prompt for video generation."""
        motion_hints = {
            "low": "subtle motion, gentle movement",
            "medium": "smooth motion, natural movement",
            "high": "dynamic motion, energetic movement"
        }

        motion = motion_hints.get(analysis.get("motion_intensity", "medium"), motion_hints["medium"])

        enhancement_prompt = f"""Enhance this video generation prompt to be more effective.
Add motion description, temporal consistency hints, and quality modifiers.
Include: {motion}
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
            logger.warning(f"Prompt enhancement failed: {e}")
            return f"{prompt}, {motion}, smooth video, high quality"

    def _select_backend(self, prompt: str, analysis: dict) -> VideoBackend:
        """Select the best backend for the request."""
        prompt_lower = prompt.lower()

        # Check for explicit backend requests
        if "ltx" in prompt_lower or "i2v" in prompt_lower or analysis.get("needs_i2v"):
            return VideoBackend.LTX_I2V
        if "svd" in prompt_lower or "stable video" in prompt_lower:
            return VideoBackend.SVD

        # Default to AnimateDiff for general requests
        return self.default_backend

    async def generate_video(
        self,
        request: VideoGenerationRequest,
        backend: VideoBackend = VideoBackend.ANIMATEDIFF
    ) -> VideoGenerationResponse:
        """Generate a video using the specified backend."""
        return await self.comfyui_client.generate_video(request, backend.value)

    async def generate_with_preset(
        self,
        prompt: str,
        length: VideoLength = VideoLength.SHORT,
        motion_style: MotionStyle = MotionStyle.SMOOTH,
        backend: VideoBackend = VideoBackend.ANIMATEDIFF,
        seed: int = -1
    ) -> VideoGenerationResponse:
        """Generate a video with preset configurations."""
        frames, fps = VIDEO_LENGTH_FRAMES[length]

        # Add motion style to prompt
        motion_modifiers = {
            MotionStyle.SMOOTH: "smooth motion, fluid movement",
            MotionStyle.DYNAMIC: "dynamic motion, energetic, fast-paced",
            MotionStyle.SUBTLE: "subtle motion, barely moving, peaceful",
            MotionStyle.CINEMATIC: "cinematic motion, dramatic, movie-like",
            MotionStyle.LOOP: "seamless loop, perfect loop, continuous motion",
        }

        styled_prompt = f"{prompt}, {motion_modifiers[motion_style]}"

        request = VideoGenerationRequest(
            prompt=styled_prompt,
            frames=frames,
            fps=fps,
            seed=seed
        )

        return await self.generate_video(request, backend)

    async def image_to_video(
        self,
        image_path: str,
        prompt: str,
        frames: int = 16,
        fps: int = 8,
        motion_strength: float = 0.5
    ) -> VideoGenerationResponse:
        """Generate video from a source image (Image-to-Video)."""
        return await self.comfyui_client.image_to_video(
            image_path=image_path,
            prompt=prompt,
            frames=frames,
            fps=fps,
            motion_strength=motion_strength
        )

    async def check_backends_status(self) -> dict:
        """Check which video backends are available."""
        return await self.comfyui_client.check_video_backends()

    async def video_puppetry(
        self,
        source_video_path: str,
        face_image_path: str,
        motion_strength: float = 0.8,
        preserve_audio: bool = True
    ) -> AgentResponse:
        """
        Apply video puppetry using Kling - substitute a face into an existing video.

        This is useful for:
        - Creating talking head videos from a single photo
        - Substituting faces in existing video content
        - Creating avatar-based video content

        Args:
            source_video_path: Path to the source video to use as motion reference
            face_image_path: Path to the face image to substitute in
            motion_strength: How closely to follow the source motion (0.0-1.0)
            preserve_audio: Whether to keep the original audio track

        Returns:
            AgentResponse with the output video path
        """
        start_time = time.time()

        try:
            result = await self._kling_puppetry(
                source_video_path=source_video_path,
                face_image_path=face_image_path,
                motion_strength=motion_strength,
                preserve_audio=preserve_audio
            )

            return AgentResponse(
                task_type=self.task_type,
                content=f"Video puppetry complete. Output saved to: {result['output_path']}",
                model_used="Kling Video Puppetry",
                generation_time=time.time() - start_time,
                metadata={
                    "output_path": result["output_path"],
                    "source_video": source_video_path,
                    "face_image": face_image_path,
                    "motion_strength": motion_strength,
                }
            )
        except Exception as e:
            logger.error(f"Video puppetry failed: {e}")
            return AgentResponse(
                task_type=self.task_type,
                content=f"Video puppetry failed: {str(e)}\n\nKling integration may require API setup.",
                model_used="Kling (failed)",
                generation_time=time.time() - start_time,
                metadata={
                    "error": str(e),
                    "source_video": source_video_path,
                    "face_image": face_image_path,
                }
            )

    async def _kling_puppetry(
        self,
        source_video_path: str,
        face_image_path: str,
        motion_strength: float,
        preserve_audio: bool
    ) -> dict:
        """
        Internal method for Kling video puppetry.

        Note: Kling integration requires either:
        1. Kling API access (cloud-based)
        2. ComfyUI custom nodes for Kling
        3. Local Kling installation

        This is a placeholder that should be configured based on your setup.
        """
        # Check if we have Kling API configured
        kling_api_key = getattr(self.config, 'kling_api_key', None)

        if kling_api_key:
            # Use Kling API
            return await self._kling_api_puppetry(
                source_video_path, face_image_path, motion_strength, preserve_audio
            )
        else:
            # Try ComfyUI integration or raise not implemented
            raise NotImplementedError(
                "Kling video puppetry requires configuration. Options:\n"
                "1. Set kling_api_key in config for API access\n"
                "2. Install Kling custom nodes in ComfyUI\n"
                "3. Use Kling desktop application and integrate via file watching"
            )

    async def _kling_api_puppetry(
        self,
        source_video_path: str,
        face_image_path: str,
        motion_strength: float,
        preserve_audio: bool
    ) -> dict:
        """Kling API-based puppetry (placeholder for actual API integration)."""
        import base64

        # Read and encode files
        with open(source_video_path, 'rb') as f:
            video_b64 = base64.b64encode(f.read()).decode()
        with open(face_image_path, 'rb') as f:
            face_b64 = base64.b64encode(f.read()).decode()

        # This would call the actual Kling API
        # Placeholder for API integration
        raise NotImplementedError(
            "Kling API integration pending. "
            "Please configure kling_api_key and kling_base_url in config."
        )

    async def face_swap(
        self,
        target_video_path: str,
        source_face_path: str,
        output_name: Optional[str] = None
    ) -> AgentResponse:
        """
        Simple face swap in a video using Kling.

        Args:
            target_video_path: Video containing the face to replace
            source_face_path: Image of the face to insert
            output_name: Optional name for output file

        Returns:
            AgentResponse with output path
        """
        return await self.video_puppetry(
            source_video_path=target_video_path,
            face_image_path=source_face_path,
            motion_strength=0.9,  # High for face swap
            preserve_audio=True
        )
