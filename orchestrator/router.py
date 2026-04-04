"""Router model for task classification."""

import json
import re
import logging
from typing import Optional

import httpx

from .models import TaskType, RouterDecision, OrchestratorRequest
from .config import get_default_config, OrchestratorConfig

logger = logging.getLogger(__name__)


class Router:
    """Routes incoming requests to the appropriate agent based on task classification."""

    def __init__(self, config: Optional[OrchestratorConfig] = None):
        self.config = config or get_default_config()
        self.model_config = self.config.router_model
        self.ollama_url = f"{self.config.ollama_base_url}/api/generate"

        # Keyword-based fallback patterns for quick classification
        self._patterns = {
            TaskType.CODING: [
                r'\b(code|program|function|script|debug|fix|implement|api|database|sql|python|javascript|java|rust|go|typescript|react|vue|angular)\b',
                r'\b(algorithm|data structure|class|method|variable|loop|array|list|dict)\b',
                r'\b(git|github|deploy|docker|kubernetes|ci/cd|test|unittest)\b',
            ],
            TaskType.IMAGE: [
                r'\b(image|picture|photo|artwork|illustration|draw|paint|visual|render)\b',
                r'\b(generate|create|make).{0,20}(image|picture|art|illustration)\b',
                r'\b(portrait|landscape|scene|character|logo|icon)\b',
            ],
            TaskType.VIDEO: [
                r'\b(video|animation|animate|motion|clip|movie|film)\b',
                r'\b(generate|create|make).{0,20}(video|animation|clip)\b',
                r'\b(frames|fps|gif|mp4|animatediff|ltx)\b',
            ],
            TaskType.WRITING: [
                r'\b(write|essay|article|blog|story|poem|haiku|letter|email|document)\b',
                r'\b(summarize|explain|describe|rewrite|edit|proofread)\b',
                r'\b(creative|fiction|narrative|content|copy)\b',
            ],
        }

    async def classify(self, request: OrchestratorRequest) -> RouterDecision:
        """Classify a request and return routing decision."""
        prompt = request.prompt.lower()

        # Try quick pattern matching first for obvious cases
        quick_result = self._quick_classify(prompt)
        if quick_result and quick_result.confidence >= 0.9:
            quick_result.original_request = request.prompt
            return quick_result

        # Use LLM for classification
        try:
            llm_result = await self._llm_classify(request)
            return llm_result
        except Exception as e:
            logger.warning(f"LLM classification failed: {e}, falling back to pattern matching")
            # Fall back to pattern matching or general
            if quick_result:
                quick_result.original_request = request.prompt
                return quick_result
            return RouterDecision(
                task_type=TaskType.GENERAL,
                confidence=0.5,
                reasoning="Fallback classification due to LLM error",
                original_request=request.prompt
            )

    def _quick_classify(self, prompt: str) -> Optional[RouterDecision]:
        """Quick classification using regex patterns."""
        scores = {task_type: 0 for task_type in TaskType}

        for task_type, patterns in self._patterns.items():
            for pattern in patterns:
                matches = re.findall(pattern, prompt, re.IGNORECASE)
                scores[task_type] += len(matches)

        max_score = max(scores.values())
        if max_score == 0:
            return None

        best_type = max(scores, key=scores.get)
        total_matches = sum(scores.values())
        confidence = min(0.95, 0.5 + (max_score / total_matches) * 0.5) if total_matches > 0 else 0.5

        return RouterDecision(
            task_type=best_type,
            confidence=confidence,
            reasoning=f"Pattern matching: {max_score} keyword matches for {best_type.value}",
            original_request=""
        )

    async def _llm_classify(self, request: OrchestratorRequest) -> RouterDecision:
        """Use the router LLM to classify the request."""
        messages = [
            {"role": "system", "content": self.model_config.system_prompt},
            {"role": "user", "content": request.prompt}
        ]

        payload = {
            "model": self.model_config.ollama_name,
            "prompt": f"System: {self.model_config.system_prompt}\n\nUser: {request.prompt}",
            "stream": False,
            "options": {
                "temperature": self.model_config.temperature,
                "num_ctx": self.model_config.context_length,
            }
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(self.ollama_url, json=payload)
            response.raise_for_status()

        result = response.json()
        response_text = result.get("response", "")

        # Parse JSON from response
        decision = self._parse_llm_response(response_text, request.prompt)
        return decision

    def _parse_llm_response(self, response: str, original_request: str) -> RouterDecision:
        """Parse the LLM's JSON response into a RouterDecision."""
        # Try to extract JSON from the response
        json_match = re.search(r'\{[^{}]*\}', response, re.DOTALL)

        if json_match:
            try:
                data = json.loads(json_match.group())
                task_type_str = data.get("task_type", "general").lower()

                # Map string to TaskType enum
                task_type_map = {
                    "coding": TaskType.CODING,
                    "writing": TaskType.WRITING,
                    "image": TaskType.IMAGE,
                    "video": TaskType.VIDEO,
                    "general": TaskType.GENERAL,
                }

                task_type = task_type_map.get(task_type_str, TaskType.GENERAL)
                confidence = float(data.get("confidence", 0.7))
                reasoning = data.get("reasoning", "LLM classification")

                return RouterDecision(
                    task_type=task_type,
                    confidence=min(1.0, max(0.0, confidence)),
                    reasoning=reasoning,
                    original_request=original_request
                )
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                logger.warning(f"Failed to parse LLM response JSON: {e}")

        # Fallback: try to detect task type from response text
        response_lower = response.lower()
        if "coding" in response_lower or "code" in response_lower:
            return RouterDecision(
                task_type=TaskType.CODING,
                confidence=0.6,
                reasoning="Detected 'coding' in LLM response",
                original_request=original_request
            )
        elif "image" in response_lower:
            return RouterDecision(
                task_type=TaskType.IMAGE,
                confidence=0.6,
                reasoning="Detected 'image' in LLM response",
                original_request=original_request
            )
        elif "video" in response_lower:
            return RouterDecision(
                task_type=TaskType.VIDEO,
                confidence=0.6,
                reasoning="Detected 'video' in LLM response",
                original_request=original_request
            )
        elif "writing" in response_lower or "write" in response_lower:
            return RouterDecision(
                task_type=TaskType.WRITING,
                confidence=0.6,
                reasoning="Detected 'writing' in LLM response",
                original_request=original_request
            )

        return RouterDecision(
            task_type=TaskType.GENERAL,
            confidence=0.5,
            reasoning="Could not parse LLM response, defaulting to general",
            original_request=original_request
        )

    def classify_sync(self, request: OrchestratorRequest) -> RouterDecision:
        """Synchronous version of classify for non-async contexts."""
        import asyncio
        return asyncio.run(self.classify(request))


# Convenience function for quick classification
async def route_request(prompt: str, config: Optional[OrchestratorConfig] = None) -> RouterDecision:
    """Quick function to route a prompt string."""
    router = Router(config)
    request = OrchestratorRequest(prompt=prompt)
    return await router.classify(request)
