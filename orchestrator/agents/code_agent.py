"""Code agent specialized for programming tasks."""

import re
import logging
from typing import Optional

from .base import BaseAgent
from ..models import TaskType, OrchestratorRequest, AgentResponse, ModelConfig
from ..config import OrchestratorConfig, get_default_config, CODE_AGENT_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class CodeAgent(BaseAgent):
    """Specialized agent for coding and software development tasks."""

    task_type = TaskType.CODING

    def __init__(self, config: Optional[OrchestratorConfig] = None):
        super().__init__(config)

    def _get_model_config(self) -> ModelConfig:
        """Return the coding model configuration."""
        return self.config.code_model

    async def process(self, request: OrchestratorRequest) -> AgentResponse:
        """Process a coding request with specialized handling."""
        # Enhance the request with coding-specific context
        enhanced_request = self._enhance_request(request)
        response = await super().process(enhanced_request)

        # Post-process the response
        response.content = self._post_process(response.content)
        response.metadata["code_blocks"] = self._extract_code_blocks(response.content)

        return response

    def _enhance_request(self, request: OrchestratorRequest) -> OrchestratorRequest:
        """Enhance the request with coding-specific context."""
        prompt = request.prompt

        # Detect language from the request
        language = self._detect_language(prompt)
        if language:
            context = request.context or ""
            context += f"\nPrimary language: {language}"
            request = OrchestratorRequest(
                prompt=prompt,
                context=context.strip(),
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                stream=request.stream
            )

        return request

    def _detect_language(self, prompt: str) -> Optional[str]:
        """Detect the programming language from the prompt."""
        prompt_lower = prompt.lower()

        language_patterns = {
            "python": r'\b(python|py|django|flask|fastapi|pandas|numpy)\b',
            "javascript": r'\b(javascript|js|node|nodejs|react|vue|angular|typescript|ts)\b',
            "rust": r'\b(rust|cargo|rustc)\b',
            "go": r'\b(golang|go\s+language)\b',
            "java": r'\b(java|spring|maven|gradle)\b(?!script)',
            "c++": r'\b(c\+\+|cpp|cmake)\b',
            "c": r'\b(c\s+language|c\s+code)\b',
            "ruby": r'\b(ruby|rails|rake)\b',
            "php": r'\b(php|laravel|symfony)\b',
            "swift": r'\b(swift|swiftui|ios)\b',
            "kotlin": r'\b(kotlin|android)\b',
            "sql": r'\b(sql|mysql|postgresql|postgres|sqlite|database|query)\b',
            "bash": r'\b(bash|shell|sh|zsh|script)\b',
        }

        for lang, pattern in language_patterns.items():
            if re.search(pattern, prompt_lower):
                return lang

        return None

    def _post_process(self, content: str) -> str:
        """Post-process the generated code response."""
        # Ensure code blocks are properly formatted
        # Fix common issues with code block formatting
        content = re.sub(r'```(\w+)\n\n', r'```\1\n', content)
        content = re.sub(r'\n\n```', r'\n```', content)

        return content

    def _extract_code_blocks(self, content: str) -> list[dict]:
        """Extract code blocks from the response."""
        pattern = r'```(\w*)\n(.*?)```'
        matches = re.findall(pattern, content, re.DOTALL)

        blocks = []
        for lang, code in matches:
            blocks.append({
                "language": lang or "text",
                "code": code.strip(),
                "lines": len(code.strip().split('\n'))
            })

        return blocks

    async def review_code(self, code: str, language: str = "python") -> AgentResponse:
        """Review provided code and suggest improvements."""
        prompt = f"""Please review the following {language} code and provide:
1. A brief assessment of code quality
2. Potential bugs or issues
3. Suggestions for improvement
4. Security considerations (if applicable)

```{language}
{code}
```"""
        request = OrchestratorRequest(prompt=prompt)
        return await self.process(request)

    async def debug(self, code: str, error: str, language: str = "python") -> AgentResponse:
        """Help debug code given an error message."""
        prompt = f"""Please help debug this {language} code that produces the following error:

Error:
{error}

Code:
```{language}
{code}
```

Please:
1. Explain what's causing the error
2. Provide the corrected code
3. Explain the fix"""
        request = OrchestratorRequest(prompt=prompt)
        return await self.process(request)

    async def explain(self, code: str, language: str = "python") -> AgentResponse:
        """Explain what a piece of code does."""
        prompt = f"""Please explain what the following {language} code does:

```{language}
{code}
```

Provide:
1. A high-level overview
2. Step-by-step explanation
3. Time/space complexity (if relevant)"""
        request = OrchestratorRequest(prompt=prompt)
        return await self.process(request)

    async def refactor(
        self, code: str, language: str = "python", goals: Optional[str] = None
    ) -> AgentResponse:
        """Refactor code with optional specific goals."""
        goals_text = f"\nSpecific goals: {goals}" if goals else ""
        prompt = f"""Please refactor the following {language} code to improve it:{goals_text}

```{language}
{code}
```

Provide:
1. The refactored code
2. Explanation of changes
3. Benefits of the refactoring"""
        request = OrchestratorRequest(prompt=prompt)
        return await self.process(request)
