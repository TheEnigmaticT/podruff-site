"""Tests for suggestion_engine module — Suggestion dataclass, BaseGenerator, SuggestionStore."""

import time
from unittest.mock import patch, MagicMock

from suggestion_engine import (
    BaseGenerator,
    Suggestion,
    SuggestionStatus,
    SuggestionStore,
    SuggestionType,
)
from hook_generator import HookGenerator
from topic_flagger import TopicFlagger
from followup_generator import FollowupGenerator


class TestSuggestion:
    def test_make_id_is_unique(self):
        ids = {Suggestion.make_id() for _ in range(100)}
        assert len(ids) == 100

    def test_default_status_is_new(self):
        s = Suggestion(id="abc", type=SuggestionType.HOOK, text="test", timestamp=time.time())
        assert s.status == SuggestionStatus.NEW


class TestBaseGenerator:
    @patch("suggestion_engine.requests.post")
    def test_generate_stores_suggestions(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "Line one\nLine two"}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        gen = HookGenerator()
        results = gen.generate("Some transcript")
        assert len(results) == 2
        assert all(s.type == SuggestionType.HOOK for s in results)
        assert all(s.status == SuggestionStatus.NEW for s in results)
        assert len(gen.get_suggestions()) == 2

    @patch("suggestion_engine.requests.post")
    def test_tag_suggestion(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "A hook"}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        gen = HookGenerator()
        results = gen.generate("Transcript")
        sid = results[0].id

        tagged = gen.tag_suggestion(sid, SuggestionStatus.USED)
        assert tagged is not None
        assert tagged.status == SuggestionStatus.USED

    def test_tag_nonexistent_returns_none(self):
        gen = HookGenerator()
        assert gen.tag_suggestion("fake_id", SuggestionStatus.USED) is None


class TestSuggestionStore:
    @patch("suggestion_engine.requests.post")
    def test_get_all_combines_types(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "Item one"}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        hook_gen = HookGenerator()
        topic_gen = TopicFlagger()

        store = SuggestionStore()
        store.register(hook_gen)
        store.register(topic_gen)

        hook_gen.generate("transcript")
        topic_gen.generate("transcript")

        all_items = store.get_all()
        assert len(all_items) == 2
        types = {s.type for s in all_items}
        assert SuggestionType.HOOK in types
        assert SuggestionType.TOPIC in types

    @patch("suggestion_engine.requests.post")
    def test_get_all_with_filter(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "Item one"}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        hook_gen = HookGenerator()
        topic_gen = TopicFlagger()

        store = SuggestionStore()
        store.register(hook_gen)
        store.register(topic_gen)

        hook_gen.generate("transcript")
        topic_gen.generate("transcript")

        hooks_only = store.get_all(type_filter=[SuggestionType.HOOK])
        assert len(hooks_only) == 1
        assert hooks_only[0].type == SuggestionType.HOOK

    @patch("suggestion_engine.requests.post")
    def test_tag_across_generators(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "Item one"}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        hook_gen = HookGenerator()
        topic_gen = TopicFlagger()

        store = SuggestionStore()
        store.register(hook_gen)
        store.register(topic_gen)

        topic_gen.generate("transcript")
        topic_id = topic_gen.get_suggestions()[0].id

        result = store.tag(topic_id, SuggestionStatus.GOOD)
        assert result is not None
        assert result.status == SuggestionStatus.GOOD

    def test_tag_nonexistent(self):
        store = SuggestionStore()
        store.register(HookGenerator())
        assert store.tag("fake", SuggestionStatus.USED) is None


class TestTopicFlagger:
    @patch("suggestion_engine.requests.post")
    def test_generates_topics(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "MoltBot vs Notis comparison\nPricing disagreement"}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        gen = TopicFlagger()
        topics = gen.generate_topics("Some transcript about tools")
        assert len(topics) == 2
        assert "MoltBot" in topics[0]

    @patch("suggestion_engine.requests.post")
    def test_topic_prompt_uses_correct_template(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "A topic"}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        gen = TopicFlagger()
        gen.generate_topics("transcript")

        prompt = mock_post.call_args[1]["json"]["prompt"]
        assert "discussion threads" in prompt.lower() or "threads" in prompt.lower()


class TestFollowupGenerator:
    @patch("suggestion_engine.requests.post")
    def test_generates_followups(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "What was the pricing argument about?\nHow does Notis handle context windows?"}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        gen = FollowupGenerator()
        questions = gen.generate_followups("Some transcript")
        assert len(questions) == 2
        assert "?" in questions[0]

    @patch("suggestion_engine.requests.post")
    def test_followup_prompt_uses_correct_template(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"response": "A question?"}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        gen = FollowupGenerator()
        gen.generate_followups("transcript")

        prompt = mock_post.call_args[1]["json"]["prompt"]
        assert "follow-up" in prompt.lower() or "dig deeper" in prompt.lower()
