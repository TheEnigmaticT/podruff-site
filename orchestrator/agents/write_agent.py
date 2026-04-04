"""Write agent specialized for writing and content tasks."""

import logging
from typing import Optional
from enum import Enum

from .base import BaseAgent
from ..models import TaskType, OrchestratorRequest, AgentResponse, ModelConfig
from ..config import OrchestratorConfig

logger = logging.getLogger(__name__)


class WritingStyle(str, Enum):
    """Available writing styles."""
    PROFESSIONAL = "professional"
    CASUAL = "casual"
    ACADEMIC = "academic"
    CREATIVE = "creative"
    TECHNICAL = "technical"
    PERSUASIVE = "persuasive"


class ContentType(str, Enum):
    """Types of content the write agent can produce."""
    ESSAY = "essay"
    ARTICLE = "article"
    BLOG_POST = "blog_post"
    EMAIL = "email"
    STORY = "story"
    POEM = "poem"
    DOCUMENTATION = "documentation"
    SUMMARY = "summary"
    REVIEW = "review"
    SCRIPT = "script"


class WriteAgent(BaseAgent):
    """Specialized agent for writing and content creation tasks."""

    task_type = TaskType.WRITING

    def __init__(self, config: Optional[OrchestratorConfig] = None):
        super().__init__(config)

    def _get_model_config(self) -> ModelConfig:
        """Return the writing model configuration."""
        return self.config.write_model

    async def process(self, request: OrchestratorRequest) -> AgentResponse:
        """Process a writing request."""
        # Detect content type and adjust temperature if needed
        content_type = self._detect_content_type(request.prompt)

        # Adjust temperature based on content type
        adjusted_request = self._adjust_for_content_type(request, content_type)

        response = await super().process(adjusted_request)
        response.metadata["content_type"] = content_type.value if content_type else "general"
        response.metadata["word_count"] = len(response.content.split())

        return response

    def _detect_content_type(self, prompt: str) -> Optional[ContentType]:
        """Detect the type of content being requested."""
        prompt_lower = prompt.lower()

        type_keywords = {
            ContentType.EMAIL: ["email", "mail", "message to", "reply to"],
            ContentType.ESSAY: ["essay", "thesis", "argument"],
            ContentType.ARTICLE: ["article", "news", "report"],
            ContentType.BLOG_POST: ["blog", "post", "blog post"],
            ContentType.STORY: ["story", "narrative", "tale", "fiction"],
            ContentType.POEM: ["poem", "poetry", "haiku", "sonnet", "verse"],
            ContentType.DOCUMENTATION: ["documentation", "docs", "readme", "guide", "manual"],
            ContentType.SUMMARY: ["summarize", "summary", "tldr", "brief"],
            ContentType.REVIEW: ["review", "critique", "evaluate"],
            ContentType.SCRIPT: ["script", "screenplay", "dialogue"],
        }

        for content_type, keywords in type_keywords.items():
            if any(kw in prompt_lower for kw in keywords):
                return content_type

        return None

    def _adjust_for_content_type(
        self, request: OrchestratorRequest, content_type: Optional[ContentType]
    ) -> OrchestratorRequest:
        """Adjust request parameters based on content type."""
        if content_type in [ContentType.POEM, ContentType.STORY, ContentType.SCRIPT]:
            # Higher temperature for creative content
            return OrchestratorRequest(
                prompt=request.prompt,
                context=request.context,
                max_tokens=request.max_tokens,
                temperature=max(0.8, request.temperature),
                stream=request.stream
            )
        elif content_type in [ContentType.DOCUMENTATION, ContentType.SUMMARY]:
            # Lower temperature for technical/factual content
            return OrchestratorRequest(
                prompt=request.prompt,
                context=request.context,
                max_tokens=request.max_tokens,
                temperature=min(0.5, request.temperature),
                stream=request.stream
            )
        return request

    async def write(
        self,
        topic: str,
        content_type: ContentType = ContentType.ARTICLE,
        style: WritingStyle = WritingStyle.PROFESSIONAL,
        length: str = "medium",
        additional_instructions: Optional[str] = None
    ) -> AgentResponse:
        """Write content with specific parameters."""
        length_guidance = {
            "short": "Keep it concise, around 200-300 words.",
            "medium": "Aim for 500-800 words.",
            "long": "Write a comprehensive piece of 1000-1500 words.",
        }

        prompt = f"""Write a {content_type.value} about: {topic}

Style: {style.value}
Length: {length_guidance.get(length, length_guidance['medium'])}
{f'Additional instructions: {additional_instructions}' if additional_instructions else ''}"""

        request = OrchestratorRequest(prompt=prompt)
        return await self.process(request)

    async def summarize(
        self,
        text: str,
        max_length: int = 200,
        style: str = "concise"
    ) -> AgentResponse:
        """Summarize provided text."""
        prompt = f"""Summarize the following text in a {style} manner.
Keep the summary under {max_length} words.

Text to summarize:
{text}"""
        request = OrchestratorRequest(prompt=prompt)
        return await self.process(request)

    async def rewrite(
        self,
        text: str,
        style: WritingStyle = WritingStyle.PROFESSIONAL,
        preserve_meaning: bool = True
    ) -> AgentResponse:
        """Rewrite text in a different style."""
        preservation_note = "Preserve the original meaning exactly." if preserve_meaning else ""
        prompt = f"""Rewrite the following text in a {style.value} style.
{preservation_note}

Original text:
{text}"""
        request = OrchestratorRequest(prompt=prompt)
        return await self.process(request)

    async def proofread(self, text: str) -> AgentResponse:
        """Proofread and correct text."""
        prompt = f"""Proofread the following text and provide:
1. Corrected version with all fixes applied
2. List of corrections made (grammar, spelling, punctuation, clarity)

Text to proofread:
{text}"""
        request = OrchestratorRequest(prompt=prompt)
        return await self.process(request)

    async def expand(
        self,
        text: str,
        target_length: int = 500,
        areas_to_expand: Optional[str] = None
    ) -> AgentResponse:
        """Expand brief text into longer content."""
        areas_note = f"Focus on expanding: {areas_to_expand}" if areas_to_expand else ""
        prompt = f"""Expand the following text to approximately {target_length} words.
Add detail, examples, and elaboration while maintaining the original tone and message.
{areas_note}

Original text:
{text}"""
        request = OrchestratorRequest(prompt=prompt)
        return await self.process(request)

    async def translate(
        self,
        text: str,
        target_language: str,
        preserve_tone: bool = True
    ) -> AgentResponse:
        """Translate text to another language."""
        tone_note = "Preserve the original tone and style." if preserve_tone else ""
        prompt = f"""Translate the following text to {target_language}.
{tone_note}

Text to translate:
{text}"""
        request = OrchestratorRequest(prompt=prompt)
        return await self.process(request)
