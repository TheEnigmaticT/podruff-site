"""Hook generation module — generates punchy hooks from transcript using Ollama."""

import logging
import sys
from typing import List

import config
from suggestion_engine import BaseGenerator, Suggestion, SuggestionType

logger = logging.getLogger(__name__)


class HookGenerator(BaseGenerator):
    """Generates short, quotable hooks from conversation transcripts."""

    def __init__(self):
        super().__init__(
            suggestion_type=SuggestionType.HOOK,
            prompt_template=config.HOOK_PROMPT_TEMPLATE,
            max_in_context=config.MAX_HOOKS_IN_CONTEXT,
        )

    # Backwards compat: expose old API names
    def generate_hooks(self, transcript: str) -> List[str]:
        """Generate hooks and return just the text strings (legacy API)."""
        suggestions = self.generate(transcript)
        return [s.text for s in suggestions]

    def get_hooks(self) -> List[Suggestion]:
        """Return all hook suggestions (legacy API name)."""
        return self.get_suggestions()


# ---------- CLI test modes ----------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="Generate hooks from sample transcript")
    parser.add_argument("--test-dedup", action="store_true", help="Test deduplication")
    args = parser.parse_args()

    SAMPLE_TRANSCRIPT = (
        "The problem isn't that people don't believe in AI. Everyone's bought in at this point. "
        "The real issue is they don't know where to point it. They've got this powerful tool "
        "and they're using it to write emails. Meanwhile their business is drowning in "
        "manual processes that could be automated in an afternoon."
    )

    logging.basicConfig(level=logging.INFO)
    gen = HookGenerator()

    if args.test:
        gen.check_ollama()
        hooks = gen.generate_hooks(SAMPLE_TRANSCRIPT)
        print("Generated hooks:")
        for h in hooks:
            print(f"  {h}")
        ok = len(hooks) >= 2 and all(len(h.split()) <= 12 for h in hooks)
        print(f"\nPass: {ok}")
        sys.exit(0 if ok else 1)

    if args.test_dedup:
        gen.check_ollama()
        hooks1 = gen.generate_hooks(SAMPLE_TRANSCRIPT)
        print("Round 1 hooks:")
        for h in hooks1:
            print(f"  {h}")

        hooks2 = gen.generate_hooks(SAMPLE_TRANSCRIPT)
        print("\nRound 2 hooks (should be different):")
        for h in hooks2:
            print(f"  {h}")

        overlap = set(hooks1) & set(hooks2)
        ok = len(overlap) == 0
        print(f"\nNo exact duplicates: {ok}")
        sys.exit(0 if ok else 1)
