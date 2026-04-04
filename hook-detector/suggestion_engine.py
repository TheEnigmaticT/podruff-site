"""Unified suggestion engine — shared types and base class for all generators."""

import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

import requests

import config

logger = logging.getLogger(__name__)


class SuggestionType(str, Enum):
    HOOK = "hook"
    TOPIC = "topic"
    FOLLOWUP = "followup"


class SuggestionStatus(str, Enum):
    NEW = "new"
    USED = "used"
    GOOD = "good"
    BAD = "bad"


@dataclass
class Suggestion:
    """A single generated suggestion with metadata."""
    id: str
    type: SuggestionType
    text: str
    timestamp: float  # wall-clock time when generated
    status: SuggestionStatus = SuggestionStatus.NEW

    @staticmethod
    def make_id() -> str:
        return uuid.uuid4().hex[:8]


class BaseGenerator:
    """
    Base class for suggestion generators (hooks, topics, follow-ups).
    Handles Ollama calls, response parsing, storage, and thread safety.
    """

    def __init__(self, suggestion_type: SuggestionType, prompt_template: str, max_in_context: int):
        self._type = suggestion_type
        self._prompt_template = prompt_template
        self._max_in_context = max_in_context
        self._suggestions: List[Suggestion] = []
        self._lock = threading.Lock()

    def check_ollama(self) -> None:
        """Verify Ollama is running and the model is available."""
        try:
            resp = requests.get("http://localhost:11434/api/tags", timeout=5)
            resp.raise_for_status()
        except requests.ConnectionError:
            raise RuntimeError("Ollama not responding. Run `ollama serve`")
        except Exception as e:
            raise RuntimeError(f"Ollama health check failed: {e}")

        models = resp.json().get("models", [])
        model_names = [m.get("name", "") for m in models]
        if not any(config.OLLAMA_MODEL.split(":")[0] in name for name in model_names):
            raise RuntimeError(
                f"Model {config.OLLAMA_MODEL} not found. Run `ollama pull {config.OLLAMA_MODEL}`"
            )

    def generate(self, transcript: str) -> List[Suggestion]:
        """
        Generate suggestions from transcript text.
        Returns list of new Suggestion objects.
        """
        if not transcript.strip():
            return []

        previous = self._format_previous()
        prompt = self._build_prompt(transcript, previous)

        try:
            resp = requests.post(
                config.OLLAMA_URL,
                json={"model": config.OLLAMA_MODEL, "prompt": prompt, "stream": False},
                timeout=config.OLLAMA_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
        except requests.Timeout:
            logger.error("[%s] Ollama timeout — skipping cycle", self._type.value)
            return []
        except requests.ConnectionError:
            logger.error("[%s] Ollama connection error", self._type.value)
            return []
        except Exception as e:
            logger.error("[%s] Ollama request failed: %s", self._type.value, e)
            return []

        raw = resp.json().get("response", "")
        texts = self._parse_response(raw)

        now = time.time()
        new_suggestions = []
        with self._lock:
            for text in texts:
                s = Suggestion(
                    id=Suggestion.make_id(),
                    type=self._type,
                    text=text,
                    timestamp=now,
                )
                self._suggestions.append(s)
                new_suggestions.append(s)

        return new_suggestions

    def _build_prompt(self, transcript: str, previous: str) -> str:
        """Build the prompt from the template. Subclasses can override."""
        # The template uses named placeholders — figure out which ones
        kwargs = {"transcript": transcript}
        if "{previous_hooks}" in self._prompt_template:
            kwargs["previous_hooks"] = previous or "(none yet)"
        if "{previous_topics}" in self._prompt_template:
            kwargs["previous_topics"] = previous or "(none yet)"
        if "{previous_followups}" in self._prompt_template:
            kwargs["previous_followups"] = previous or "(none yet)"
        return self._prompt_template.format(**kwargs)

    def _format_previous(self) -> str:
        """Format recent suggestions for prompt context."""
        with self._lock:
            recent = self._suggestions[-self._max_in_context:]
        return "\n".join(f"- {s.text}" for s in recent)

    @staticmethod
    def _parse_response(raw: str) -> List[str]:
        """Parse raw LLM response into individual text items."""
        lines = raw.strip().split("\n")
        items = []
        for line in lines:
            line = line.strip().strip("-•*").strip()
            line = re.sub(r"^\d+[.)]\s*", "", line)
            if line.startswith('"') and line.endswith('"'):
                line = line[1:-1]
            if line:
                items.append(line)
        return items

    def get_suggestions(self) -> List[Suggestion]:
        """Return all suggestions of this type."""
        with self._lock:
            return list(self._suggestions)

    def tag_suggestion(self, suggestion_id: str, status: SuggestionStatus) -> Optional[Suggestion]:
        """Update the status of a suggestion by ID. Returns the updated suggestion or None."""
        with self._lock:
            for s in self._suggestions:
                if s.id == suggestion_id:
                    s.status = status
                    return s
        return None

    def find_suggestion(self, suggestion_id: str) -> Optional[Suggestion]:
        """Find a suggestion by ID."""
        with self._lock:
            for s in self._suggestions:
                if s.id == suggestion_id:
                    return s
        return None


class SuggestionStore:
    """
    Central store that aggregates suggestions from all generators.
    Provides unified access for the web API.
    """

    def __init__(self):
        self._generators: Dict[SuggestionType, BaseGenerator] = {}

    def register(self, generator: BaseGenerator) -> None:
        self._generators[generator._type] = generator

    def get_all(self, type_filter: Optional[List[SuggestionType]] = None) -> List[Suggestion]:
        """Get all suggestions, optionally filtered by type. Sorted by timestamp."""
        all_suggestions = []
        for gen_type, gen in self._generators.items():
            if type_filter and gen_type not in type_filter:
                continue
            all_suggestions.extend(gen.get_suggestions())
        all_suggestions.sort(key=lambda s: s.timestamp)
        return all_suggestions

    def tag(self, suggestion_id: str, status: SuggestionStatus) -> Optional[Suggestion]:
        """Tag a suggestion across any generator."""
        for gen in self._generators.values():
            result = gen.tag_suggestion(suggestion_id, status)
            if result:
                return result
        return None

    def find(self, suggestion_id: str) -> Optional[Suggestion]:
        """Find a suggestion by ID across any generator."""
        for gen in self._generators.values():
            result = gen.find_suggestion(suggestion_id)
            if result:
                return result
        return None

    def get_generator(self, suggestion_type: SuggestionType) -> Optional[BaseGenerator]:
        return self._generators.get(suggestion_type)
