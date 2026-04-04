from unittest.mock import patch, MagicMock, call
from pipeline.poller import process_video, publish_scheduled


def test_process_video_runs_full_pipeline():
    with (
        patch("pipeline.poller.download_video", return_value="/tmp/video.mp4") as mock_dl,
        patch("pipeline.poller.transcribe_video", return_value=[{"start": 0, "end": 10, "text": "Hi"}]) as mock_tx,
        patch("pipeline.poller.segment_topics", return_value=[{
            "topic": "Intro", "start": 0, "end": 10,
            "segments": [{"start": 0, "end": 10, "text": "Hi"}]
        }]) as mock_seg,
        patch("pipeline.poller.select_hook", return_value={"sentence": "Hi", "start": 0, "end": 5}) as mock_hook,
        patch("pipeline.poller.generate_headline", return_value={"headline": "Title", "description": "Desc"}) as mock_hl,
        patch("pipeline.poller.extract_segment") as mock_extract,
        patch("pipeline.poller.extract_frame") as mock_frame,
        patch("pipeline.poller.prepend_hook") as mock_prepend,
        patch("pipeline.poller.get_clip_duration", return_value=5.0) as mock_dur,
        patch("pipeline.poller.create_short") as mock_short,
        patch("pipeline.poller.generate_thumbnail") as mock_thumb,
        patch("pipeline.poller.upload_file", return_value="https://r2.example.com/file") as mock_upload,
        patch("pipeline.poller.create_clip_card", return_value="card-1") as mock_card,
        patch("pipeline.poller.post_message") as mock_msg,
        patch("pipeline.poller.post_review_card") as mock_review,
        patch("os.path.exists", return_value=False),
    ):
        cards = process_video("https://youtube.com/watch?v=test", "parent-card-id")
        mock_dl.assert_called_once()
        mock_tx.assert_called_once()
        mock_seg.assert_called_once()
        mock_hook.assert_called_once()
        mock_hl.assert_called_once()
        mock_card.assert_called_once()
        assert cards == ["card-1"]
        # Verify Slack notifications were attempted
        assert mock_msg.call_count >= 2  # start + per-topic + done
        mock_review.assert_called_once()


def test_publish_scheduled_publishes_and_updates():
    mock_card = {
        "id": "card-1",
        "properties": {
            "Clip URL": {"url": "https://r2.example.com/clip.mp4"},
            "Headline": {"rich_text": [{"plain_text": "Title"}]},
            "Description": {"rich_text": [{"plain_text": "Desc"}]},
            "Platforms": {"multi_select": [{"name": "youtube"}, {"name": "linkedin"}]},
        },
    }
    with (
        patch("pipeline.poller.get_scheduled_cards", return_value=[mock_card]),
        patch("pipeline.poller.publish_clip", return_value={"id": "post-1"}) as mock_pub,
        patch("pipeline.poller.update_card_status") as mock_update,
    ):
        count = publish_scheduled("2026-03-05")
        mock_pub.assert_called_once()
        mock_update.assert_called_once_with("card-1", "Published")
        assert count == 1
