"""Topic flagging module — identifies interesting discussion threads using Ollama."""

import logging
import sys
from typing import List

import config
from suggestion_engine import BaseGenerator, Suggestion, SuggestionType

logger = logging.getLogger(__name__)


class TopicFlagger(BaseGenerator):
    """Flags interesting conversation threads worth exploring further."""

    def __init__(self):
        super().__init__(
            suggestion_type=SuggestionType.TOPIC,
            prompt_template=config.TOPIC_PROMPT_TEMPLATE,
            max_in_context=config.MAX_TOPICS_IN_CONTEXT,
        )

    def generate_topics(self, transcript: str) -> List[str]:
        """Generate topic flags and return just the text strings."""
        suggestions = self.generate(transcript)
        return [s.text for s in suggestions]

    def get_topics(self) -> List[Suggestion]:
        """Return all topic suggestions."""
        return self.get_suggestions()


# ---------- CLI test mode ----------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="Generate topics from sample transcript")
    args = parser.parse_args()

    SAMPLE_TRANSCRIPT = (
        "The problem isn't that people don't believe in AI. Everyone's bought in at this point. "
        "The real issue is they don't know where to point it. They've got this powerful tool "
        "and they're using it to write emails. Meanwhile their business is drowning in "
        "manual processes that could be automated in an afternoon. "
        "We tried MoltBot first, but then switched to Notis because the context window "
        "handling was completely different. MoltBot just truncates, Notis does this clever "
        "chunking thing. But my co-founder thought we should stick with MoltBot because "
        "of the pricing. We had a pretty heated debate about it actually."
    )

    logging.basicConfig(level=logging.INFO)
    gen = TopicFlagger()

    if args.test:
        gen.check_ollama()
        topics = gen.generate_topics(SAMPLE_TRANSCRIPT)
        print("Flagged topics:")
        for t in topics:
            print(f"  {t}")
        ok = len(topics) >= 1
        print(f"\nPass: {ok}")
        sys.exit(0 if ok else 1)
