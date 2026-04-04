"""ComfyUI API client for image and video generation."""

import json
import logging
import uuid
import time
import random
from pathlib import Path
from typing import Optional, Any
import asyncio

import httpx

from ..models import (
    ImageGenerationRequest,
    ImageGenerationResponse,
    VideoGenerationRequest,
    VideoGenerationResponse,
    TaskType,
)

logger = logging.getLogger(__name__)


class ComfyUIClient:
    """Client for interacting with ComfyUI API."""

    def __init__(self, base_url: str = "http://localhost:8188"):
        self.base_url = base_url.rstrip("/")
        self.ws_url = self.base_url.replace("http", "ws")
        self.client_id = str(uuid.uuid4())
        self.output_dir = Path(__file__).parent.parent.parent / "outputs"
        self.workflows_dir = Path(__file__).parent / "workflows"

    async def check_status(self) -> dict:
        """Check if ComfyUI is running and available."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/system_stats")
                if response.status_code == 200:
                    stats = response.json()
                    return {
                        "status": "online",
                        "system_stats": stats,
                    }
        except Exception as e:
            return {
                "status": "offline",
                "error": str(e),
                "message": "ComfyUI is not running. Start it with: python main.py --listen"
            }

        return {"status": "unknown"}

    async def check_video_backends(self) -> dict:
        """Check which video generation backends are available."""
        backends = {
            "animatediff": False,
            "ltx_i2v": False,
            "svd": False
        }

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                # Check for object info which lists available nodes
                response = await client.get(f"{self.base_url}/object_info")
                if response.status_code == 200:
                    nodes = response.json()
                    # Check for AnimateDiff nodes
                    if any("animatediff" in k.lower() for k in nodes.keys()):
                        backends["animatediff"] = True
                    # Check for LTX nodes
                    if any("ltx" in k.lower() for k in nodes.keys()):
                        backends["ltx_i2v"] = True
                    # Check for SVD nodes
                    if any("svd" in k.lower() or "stablevideo" in k.lower() for k in nodes.keys()):
                        backends["svd"] = True
        except Exception as e:
            logger.warning(f"Could not check video backends: {e}")

        return backends

    def _load_workflow(self, workflow_name: str) -> dict:
        """Load a workflow template from file."""
        workflow_path = self.workflows_dir / f"{workflow_name}.json"
        if workflow_path.exists():
            with open(workflow_path) as f:
                return json.load(f)

        # Return a default SDXL workflow if file not found
        return self._get_default_sdxl_workflow()

    def _get_default_sdxl_workflow(self) -> dict:
        """Return a default SDXL workflow."""
        return {
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "cfg": 7,
                    "denoise": 1,
                    "latent_image": ["5", 0],
                    "model": ["4", 0],
                    "negative": ["7", 0],
                    "positive": ["6", 0],
                    "sampler_name": "euler",
                    "scheduler": "normal",
                    "seed": 0,
                    "steps": 20
                }
            },
            "4": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {
                    "ckpt_name": "sd_xl_base_1.0.safetensors"
                }
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {
                    "batch_size": 1,
                    "height": 1024,
                    "width": 1024
                }
            },
            "6": {
                "class_type": "CLIPTextEncode",
                "inputs": {
                    "clip": ["4", 1],
                    "text": "beautiful sunset"
                }
            },
            "7": {
                "class_type": "CLIPTextEncode",
                "inputs": {
                    "clip": ["4", 1],
                    "text": "bad quality, blurry"
                }
            },
            "8": {
                "class_type": "VAEDecode",
                "inputs": {
                    "samples": ["3", 0],
                    "vae": ["4", 2]
                }
            },
            "9": {
                "class_type": "SaveImage",
                "inputs": {
                    "filename_prefix": "orchestrator",
                    "images": ["8", 0]
                }
            }
        }

    def _get_default_animatediff_workflow(self) -> dict:
        """Return a default AnimateDiff workflow."""
        return {
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {
                    "ckpt_name": "dreamshaper_8.safetensors"
                }
            },
            "2": {
                "class_type": "ADE_LoadAnimateDiffModel",
                "inputs": {
                    "model_name": "mm_sd_v15_v2.ckpt"
                }
            },
            "3": {
                "class_type": "ADE_ApplyAnimateDiffModel",
                "inputs": {
                    "model": ["1", 0],
                    "motion_model": ["2", 0]
                }
            },
            "4": {
                "class_type": "CLIPTextEncode",
                "inputs": {
                    "clip": ["1", 1],
                    "text": "animation prompt"
                }
            },
            "5": {
                "class_type": "CLIPTextEncode",
                "inputs": {
                    "clip": ["1", 1],
                    "text": "bad quality, static"
                }
            },
            "6": {
                "class_type": "EmptyLatentImage",
                "inputs": {
                    "batch_size": 16,
                    "height": 512,
                    "width": 512
                }
            },
            "7": {
                "class_type": "KSampler",
                "inputs": {
                    "cfg": 7,
                    "denoise": 1,
                    "latent_image": ["6", 0],
                    "model": ["3", 0],
                    "negative": ["5", 0],
                    "positive": ["4", 0],
                    "sampler_name": "euler",
                    "scheduler": "normal",
                    "seed": 0,
                    "steps": 20
                }
            },
            "8": {
                "class_type": "VAEDecode",
                "inputs": {
                    "samples": ["7", 0],
                    "vae": ["1", 2]
                }
            },
            "9": {
                "class_type": "VHS_VideoCombine",
                "inputs": {
                    "images": ["8", 0],
                    "frame_rate": 8,
                    "filename_prefix": "orchestrator_video",
                    "format": "video/h264-mp4"
                }
            }
        }

    def _prepare_image_workflow(self, request: ImageGenerationRequest) -> dict:
        """Prepare workflow for image generation."""
        workflow = self._get_default_sdxl_workflow()

        # Update parameters
        seed = request.seed if request.seed >= 0 else random.randint(0, 2**32 - 1)

        # Update KSampler
        workflow["3"]["inputs"]["seed"] = seed
        workflow["3"]["inputs"]["steps"] = request.steps
        workflow["3"]["inputs"]["cfg"] = request.cfg_scale
        workflow["3"]["inputs"]["sampler_name"] = request.sampler
        workflow["3"]["inputs"]["scheduler"] = request.scheduler

        # Update latent size
        workflow["5"]["inputs"]["width"] = request.width
        workflow["5"]["inputs"]["height"] = request.height

        # Update prompts
        workflow["6"]["inputs"]["text"] = request.prompt
        workflow["7"]["inputs"]["text"] = request.negative_prompt

        # Update checkpoint if specified
        if request.checkpoint:
            workflow["4"]["inputs"]["ckpt_name"] = request.checkpoint

        return workflow

    def _prepare_video_workflow(
        self, request: VideoGenerationRequest, backend: str
    ) -> dict:
        """Prepare workflow for video generation."""
        workflow = self._get_default_animatediff_workflow()

        seed = request.seed if request.seed >= 0 else random.randint(0, 2**32 - 1)

        # Update parameters
        workflow["7"]["inputs"]["seed"] = seed
        workflow["7"]["inputs"]["steps"] = request.steps
        workflow["7"]["inputs"]["cfg"] = request.cfg_scale

        # Update latent size (batch = frames)
        workflow["6"]["inputs"]["batch_size"] = request.frames
        workflow["6"]["inputs"]["width"] = request.width
        workflow["6"]["inputs"]["height"] = request.height

        # Update prompts
        workflow["4"]["inputs"]["text"] = request.prompt
        workflow["5"]["inputs"]["text"] = request.negative_prompt

        # Update video output settings
        workflow["9"]["inputs"]["frame_rate"] = request.fps

        # Update motion module if specified
        if request.motion_module:
            workflow["2"]["inputs"]["model_name"] = request.motion_module

        return workflow

    async def _queue_prompt(self, workflow: dict) -> str:
        """Queue a workflow for execution and return the prompt ID."""
        payload = {
            "prompt": workflow,
            "client_id": self.client_id
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/prompt",
                json=payload
            )
            response.raise_for_status()
            result = response.json()
            return result["prompt_id"]

    async def _wait_for_completion(
        self,
        prompt_id: str,
        timeout: float = 300.0
    ) -> dict:
        """Wait for a prompt to complete and return the output."""
        start_time = time.time()

        while time.time() - start_time < timeout:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Check history for completion
                response = await client.get(f"{self.base_url}/history/{prompt_id}")
                if response.status_code == 200:
                    history = response.json()
                    if prompt_id in history:
                        return history[prompt_id]

            await asyncio.sleep(1.0)

        raise TimeoutError(f"Generation timed out after {timeout} seconds")

    async def _get_output_images(self, history: dict) -> list[str]:
        """Extract output image paths from generation history."""
        images = []

        outputs = history.get("outputs", {})
        for node_id, node_output in outputs.items():
            if "images" in node_output:
                for img in node_output["images"]:
                    filename = img.get("filename")
                    subfolder = img.get("subfolder", "")
                    if filename:
                        # Construct full path
                        if subfolder:
                            images.append(f"{subfolder}/{filename}")
                        else:
                            images.append(filename)

        return images

    async def _get_output_video(self, history: dict) -> Optional[str]:
        """Extract output video path from generation history."""
        outputs = history.get("outputs", {})
        for node_id, node_output in outputs.items():
            if "gifs" in node_output:
                for vid in node_output["gifs"]:
                    filename = vid.get("filename")
                    if filename:
                        return filename
            if "videos" in node_output:
                for vid in node_output["videos"]:
                    filename = vid.get("filename")
                    if filename:
                        return filename

        return None

    async def generate_image(
        self, request: ImageGenerationRequest
    ) -> ImageGenerationResponse:
        """Generate an image using ComfyUI."""
        start_time = time.time()

        # Prepare workflow
        workflow = self._prepare_image_workflow(request)

        # Get the seed that will be used
        seed_used = workflow["3"]["inputs"]["seed"]

        # Queue the prompt
        prompt_id = await self._queue_prompt(workflow)
        logger.info(f"Queued image generation: {prompt_id}")

        # Wait for completion
        history = await self._wait_for_completion(prompt_id)

        # Get output images
        image_paths = await self._get_output_images(history)

        generation_time = time.time() - start_time

        return ImageGenerationResponse(
            image_paths=image_paths,
            prompt=request.prompt,
            seed_used=seed_used,
            generation_time=generation_time,
            metadata={
                "prompt_id": prompt_id,
                "steps": request.steps,
                "cfg_scale": request.cfg_scale,
                "width": request.width,
                "height": request.height,
            }
        )

    async def generate_video(
        self,
        request: VideoGenerationRequest,
        backend: str = "animatediff"
    ) -> VideoGenerationResponse:
        """Generate a video using ComfyUI."""
        start_time = time.time()

        # Prepare workflow
        workflow = self._prepare_video_workflow(request, backend)

        # Get the seed that will be used
        seed_used = workflow["7"]["inputs"]["seed"]

        # Queue the prompt
        prompt_id = await self._queue_prompt(workflow)
        logger.info(f"Queued video generation: {prompt_id}")

        # Wait for completion (videos take longer)
        history = await self._wait_for_completion(prompt_id, timeout=600.0)

        # Get output video
        video_path = await self._get_output_video(history)

        generation_time = time.time() - start_time

        return VideoGenerationResponse(
            video_path=video_path or "unknown",
            frames_generated=request.frames,
            fps=request.fps,
            prompt=request.prompt,
            seed_used=seed_used,
            generation_time=generation_time,
            metadata={
                "prompt_id": prompt_id,
                "backend": backend,
                "steps": request.steps,
                "width": request.width,
                "height": request.height,
            }
        )

    async def image_to_video(
        self,
        image_path: str,
        prompt: str,
        frames: int = 16,
        fps: int = 8,
        motion_strength: float = 0.5
    ) -> VideoGenerationResponse:
        """Generate video from a source image using LTX or SVD."""
        # This would use a different workflow for I2V
        # For now, return a placeholder indicating the feature needs I2V workflow setup
        raise NotImplementedError(
            "Image-to-Video requires LTX-2 or SVD workflow configuration. "
            "Please set up the appropriate ComfyUI custom nodes and workflows."
        )

    async def get_available_checkpoints(self) -> list[str]:
        """Get list of available model checkpoints."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.base_url}/object_info/CheckpointLoaderSimple")
                if response.status_code == 200:
                    info = response.json()
                    return info.get("CheckpointLoaderSimple", {}).get("input", {}).get("required", {}).get("ckpt_name", [[]])[0]
        except Exception as e:
            logger.warning(f"Could not get checkpoints: {e}")

        return []

    async def get_queue_status(self) -> dict:
        """Get the current queue status."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/queue")
                if response.status_code == 200:
                    return response.json()
        except Exception as e:
            logger.warning(f"Could not get queue status: {e}")

        return {"queue_running": [], "queue_pending": []}
