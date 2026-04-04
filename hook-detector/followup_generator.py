"""Follow-up question generation module — suggests questions to ask the guest."""

import logging
import sys
from typing import List

import config
from suggestion_engine import BaseGenerator, Suggestion, SuggestionType

logger = logging.getLogger(__name__)


class FollowupGenerator(BaseGenerator):
    """Generates follow-up questions based on conversation transcript."""

    def __init__(self):
        super().__init__(
            suggestion_type=SuggestionType.FOLLOWUP,
            prompt_template=config.FOLLOWUP_PROMPT_TEMPLATE,
            max_in_context=config.MAX_FOLLOWUPS_IN_CONTEXT,
        )

    def generate_followups(self, transcript: str) -> List[str]:
        """Generate follow-up questions and return just the text strings."""
        suggestions = self.generate(transcript)
        return [s.text for s in suggestions]

    def get_followups(self) -> List[Suggestion]:
        """Return all follow-up suggestions."""
        return self.get_suggestions()


# ---------- CLI test mode ----------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="Generate follow-ups from sample transcript")
    args = parser.parse_args()

    SAMPLE_TRANSCRIPT = (
        "We tried MoltBot first, but then switched to Notis because the context window "
        "handling was completely different. MoltBot just truncates, Notis does this clever "
        "chunking thing. But my co-founder thought we should stick with MoltBot because "
        "of the pricing. We had a pretty heated debate about it actually. "
        "Anyway, the real breakthrough was when we realized you could automate the "
        "manual processes in an afternoon. Our kids even listen to the show now, "
        "which is wild."
    )

    logging.basicConfig(level=logging.INFO)
    gen = FollowupGenerator()

    if args.test:
        gen.check_ollama()
        questions = gen.generate_followups(SAMPLE_TRANSCRIPT)
        print("Suggested follow-ups:")
        for q in questions:
            print(f"  {q}")
        ok = len(questions) >= 1
        print(f"\nPass: {ok}")
        sys.exit(0 if ok else 1)
