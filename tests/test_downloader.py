import json
import threading
from pathlib import Path

import pytest

from youtube_downloader.downloader import SubtitleDownloader
from youtube_downloader.exceptions import CaptionDownloadError, DownloadCancelled
from youtube_downloader.models import DownloadOptions, SourceInfo, VideoInfo


VTT = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n"


def video(index, video_id, title="Video", *, unavailable=False, automatic=False):
    tracks = {"en": [{"ext": "vtt", "url": "https://www.youtube.com/api/timedtext"}]}
    return VideoInfo(
        index=index,
        video_id=video_id,
        title=title,
        webpage_url=f"https://www.youtube.com/watch?v={video_id}",
        subtitles={} if automatic else tracks,
        automatic_captions=tracks if automatic else {},
        error="Unavailable" if unavailable else None,
    )


class FakeClient:
    def __init__(self, videos, fail_ids=(), cancel_event=None):
        self.source = SourceInfo("playlist" if len(videos) > 1 else "video", "Collection", "https://youtube.test", videos)
        self.fail_ids = set(fail_ids)
        self.calls = 0
        self.cancel_event = cancel_event

    def extract(self, _url, _cancel_event=None):
        return self.source

    def download_caption(self, _track, _cancel_event):
        self.calls += 1
        if self.calls in self.fail_ids:
            raise CaptionDownloadError("safe failure")
        if self.cancel_event is not None and self.calls == 1:
            self.cancel_event.set()
        return VTT


def options(tmp_path, **overrides):
    values = {"output": tmp_path, "output_format": "vtt", "skip_existing": True}
    values.update(overrides)
    return DownloadOptions(**values)


def read_report(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_playlist_preserves_order_and_duplicate_titles(tmp_path):
    client = FakeClient([video(1, "a", "Same"), video(2, "b", "Same")])
    report_path = SubtitleDownloader(client, "url", options(tmp_path)).run()
    report = read_report(report_path)
    assert [row["video_id"] for row in report["videos"]] == ["a", "b"]
    assert [row["subtitle"]["file"] for row in report["videos"]] == ["Same.vtt", "Same (2).vtt"]


def test_unavailable_and_failure_do_not_stop_playlist(tmp_path):
    client = FakeClient([video(1, "a", unavailable=True), video(2, "b"), video(3, "c")], fail_ids={1})
    report = read_report(SubtitleDownloader(client, "url", options(tmp_path)).run())
    assert [row["subtitle"]["status"] for row in report["videos"]] == ["unavailable", "failed", "downloaded"]
    assert report["statistics"]["unavailable"] == 1
    assert report["statistics"]["failed"] == 1


def test_duplicate_video_id_is_skipped(tmp_path):
    client = FakeClient([video(1, "a"), video(2, "a")])
    report = read_report(SubtitleDownloader(client, "url", options(tmp_path)).run())
    assert report["statistics"]["duplicates"] == 1 and client.calls == 1


def test_automatic_caption_counted(tmp_path):
    client = FakeClient([video(1, "a", automatic=True)])
    report = read_report(SubtitleDownloader(client, "url", options(tmp_path)).run())
    assert report["statistics"]["automatic_english"] == 1


def test_no_english_is_recorded(tmp_path):
    item = VideoInfo(1, "a", "No subs", "https://www.youtube.com/watch?v=a")
    report = read_report(SubtitleDownloader(FakeClient([item]), "url", options(tmp_path)).run())
    assert report["statistics"]["no_english"] == 1


def test_valid_existing_file_is_skipped_on_resume(tmp_path):
    first_client = FakeClient([video(1, "a")])
    first_report = SubtitleDownloader(first_client, "url", options(tmp_path)).run()
    second_client = FakeClient([video(1, "a")])
    report = read_report(SubtitleDownloader(second_client, "url", options(tmp_path)).run())
    assert first_report == first_report
    assert report["statistics"]["skipped_existing"] == 1 and second_client.calls == 0


def test_invalid_existing_file_is_replaced(tmp_path):
    client = FakeClient([video(1, "a")])
    report_path = SubtitleDownloader(client, "url", options(tmp_path)).run()
    subtitle = report_path.parent / read_report(report_path)["videos"][0]["subtitle"]["file"]
    subtitle.write_text("", encoding="utf-8")
    second_client = FakeClient([video(1, "a")])
    SubtitleDownloader(second_client, "url", options(tmp_path)).run()
    assert second_client.calls == 1 and subtitle.read_text(encoding="utf-8").startswith("WEBVTT")


def test_overwrite_redownloads_valid_file(tmp_path):
    SubtitleDownloader(FakeClient([video(1, "a")]), "url", options(tmp_path)).run()
    client = FakeClient([video(1, "a")])
    report = read_report(SubtitleDownloader(client, "url", options(tmp_path, overwrite=True)).run())
    assert client.calls == 1 and report["statistics"]["overwritten"] == 1
    assert report["videos"][0]["subtitle"]["status"] == "overwritten"


def test_retry_only_processes_previous_failure_ids(tmp_path):
    videos = [video(1, "failed"), video(2, "working")]
    SubtitleDownloader(FakeClient(videos, fail_ids={1}), "url", options(tmp_path)).run()
    retry = SubtitleDownloader(
        FakeClient(videos), "url", options(tmp_path), retry_video_ids={"failed"}, report_name="download_report_retry.json",
    )
    report = read_report(retry.run())
    assert retry.report_path.name == "download_report_retry.json"
    assert report["retry_only"] is True
    assert report["statistics"]["videos_scanned"] == 1
    assert report["videos"][0]["video_id"] == "failed"


def test_cancellation_saves_partial_report(tmp_path):
    event = threading.Event()
    client = FakeClient([video(1, "a"), video(2, "b")], cancel_event=event)
    downloader = SubtitleDownloader(client, "url", options(tmp_path), cancel_event=event)
    with pytest.raises(DownloadCancelled) as caught:
        downloader.run()
    report = read_report(caught.value.report_path)
    assert report["status"] == "cancelled" and report["statistics"]["downloaded"] == 1
    assert not list(tmp_path.rglob("*.part"))


def test_progress_callback_receives_each_video(tmp_path):
    progress = []
    SubtitleDownloader(
        FakeClient([video(1, "a"), video(2, "b")]), "url", options(tmp_path),
        progress_callback=lambda current, total, title: progress.append((current, total, title)),
    ).run()
    assert progress == [(1, 2, "Video"), (2, 2, "Video")]
