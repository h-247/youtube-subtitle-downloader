from http.cookiejar import CookieJar
import pytest
import yt_dlp

from youtube_downloader.client import YouTubeClient, resolve_playlist_mode, validate_youtube_url
from youtube_downloader.exceptions import CaptionDownloadError, ConfigurationError
from youtube_downloader.models import CaptionTrack


class FakeYDL:
    raw = {}
    details = {}
    last_options = None
    last_download = None
    options_history = []

    def __init__(self, options):
        FakeYDL.last_options = options
        FakeYDL.options_history.append(options)
        self.cookiejar = CookieJar()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def extract_info(self, url, download):
        FakeYDL.last_download = download
        if self.last_options["extract_flat"] == "in_playlist":
            return self.raw
        if url in self.details:
            detail = self.details[url]
            if isinstance(detail, Exception):
                raise detail
            return detail
        return self.raw


def reset_fake() -> None:
    FakeYDL.raw = {}
    FakeYDL.details = {}
    FakeYDL.last_options = None
    FakeYDL.last_download = None
    FakeYDL.options_history = []


def test_video_extraction_uses_structured_metadata_and_never_downloads(monkeypatch):
    reset_fake()
    FakeYDL.raw = {
        "id": "abc", "title": "Video", "webpage_url": "https://www.youtube.com/watch?v=abc",
        "subtitles": {"en": []}, "automatic_captions": {}, "availability": "public", "duration": 12,
    }
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    with YouTubeClient() as client:
        source = client.extract("https://www.youtube.com/watch?v=abc")
    assert source.source_type == "video" and source.videos[0].video_id == "abc"
    assert FakeYDL.last_download is False
    assert FakeYDL.last_options["skip_download"] is True
    assert FakeYDL.last_options["writethumbnail"] is False
    assert FakeYDL.last_options["noplaylist"] is True


def test_playlist_order_and_unavailable_entries(monkeypatch):
    reset_fake()
    FakeYDL.raw = {
        "_type": "playlist", "title": "List", "entries": [
            {"id": "b", "title": "Second", "playlist_index": 2},
            None,
            {"id": "a", "title": "First", "playlist_index": 1},
        ],
    }
    FakeYDL.details = {
        "https://www.youtube.com/watch?v=b": {"id": "b", "title": "Second"},
        "https://www.youtube.com/watch?v=a": {"id": "a", "title": "First"},
    }
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    with YouTubeClient() as client:
        source = client.extract("https://www.youtube.com/playlist?list=PL123")
    assert [video.title for video in source.videos] == ["Second", "Video không khả dụng 2", "First"]
    assert source.videos[1].unavailable
    assert FakeYDL.options_history[0]["noplaylist"] is False
    assert FakeYDL.options_history[0]["extract_flat"] == "in_playlist"
    assert FakeYDL.last_options["noplaylist"] is True


def test_combined_url_automatically_expands_playlist(monkeypatch):
    reset_fake()
    FakeYDL.raw = {"_type": "playlist", "title": "List", "entries": [{"id": "abc", "title": "Video"}]}
    FakeYDL.details = {"https://www.youtube.com/watch?v=abc": {"id": "abc", "title": "Video"}}
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    with YouTubeClient() as client:
        client.extract("https://www.youtube.com/watch?v=abc&list=PL123")
    assert FakeYDL.options_history[0]["noplaylist"] is False


def test_watch_later_flat_entries_are_resolved_individually(monkeypatch):
    reset_fake()
    FakeYDL.raw = {"_type": "playlist", "title": "Watch later", "entries": [{"id": "abc123", "title": "Saved video"}]}
    FakeYDL.details = {
        "https://www.youtube.com/watch?v=abc123": {
            "id": "abc123",
            "title": "Saved video",
            "subtitles": {"en": [{"ext": "vtt", "url": "https://www.youtube.com/api/timedtext"}]},
        }
    }
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    with YouTubeClient() as client:
        source = client.extract("https://www.youtube.com/playlist?list=WL")
    assert source.videos[0].title == "Saved video"
    assert not source.videos[0].unavailable
    assert source.videos[0].subtitles["en"][0]["ext"] == "vtt"
    assert FakeYDL.options_history[0]["ignore_no_formats_error"] is True


def test_one_watch_later_entry_failure_does_not_hide_other_entries(monkeypatch):
    reset_fake()
    FakeYDL.raw = {
        "_type": "playlist",
        "title": "Watch later",
        "entries": [{"id": "failed1", "title": "Unavailable"}, {"id": "working2", "title": "Working"}],
    }
    FakeYDL.details = {
        "https://www.youtube.com/watch?v=failed1": RuntimeError("sign in required"),
        "https://www.youtube.com/watch?v=working2": {"id": "working2", "title": "Working"},
    }
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    with YouTubeClient() as client:
        source = client.extract("https://www.youtube.com/playlist?list=WL")
    assert source.videos[0].unavailable
    assert source.videos[1].video_id == "working2"


@pytest.mark.parametrize("url", [
    "https://youtu.be/abc", "https://www.youtube.com/shorts/abc",
    "https://www.youtube.com/watch?v=abc", "https://www.youtube.com/playlist?list=PL123",
])
def test_supported_urls(url):
    assert validate_youtube_url(url) == url


def test_markdown_style_playlist_link_is_normalized():
    url = "https://www.youtube.com/playlist?list=PLKMz4v1-KWGI"
    assert validate_youtube_url(f"[{url}]({url})") == url


def test_markdown_escaped_query_separator_is_normalized():
    raw = (
        "[playlist](https://youtube.com/playlist?list=PLKMz4v1-KWGI"
        r"\&si=zWJfIE3W71UUD9ZM)"
    )
    assert validate_youtube_url(raw) == (
        "https://youtube.com/playlist?list=PLKMz4v1-KWGI&si=zWJfIE3W71UUD9ZM"
    )


def test_channel_url_is_rejected():
    with pytest.raises(ConfigurationError):
        validate_youtube_url("https://www.youtube.com/@channel")


def test_caption_url_host_is_restricted():
    track = CaptionTrack(language="en", kind="manual", extension="vtt", url="https://evil.example/caption")
    with YouTubeClient() as client, pytest.raises(CaptionDownloadError):
        client.download_caption(track)


def test_secret_headers_are_removed():
    track = CaptionTrack(
        language="en", kind="manual", extension="vtt", data="WEBVTT",
        http_headers={"Authorization": "secret", "Cookie": "secret", "User-Agent": "safe"},
    )
    assert YouTubeClient._safe_track_headers(track) == {"User-Agent": "safe"}


def test_url_alone_selects_playlist_mode():
    assert resolve_playlist_mode("https://www.youtube.com/playlist?list=PL123")
    assert resolve_playlist_mode("https://www.youtube.com/watch?v=abc&list=PL123")
    assert not resolve_playlist_mode("https://www.youtube.com/watch?v=abc")
