"""Thin, isolated adapter around yt-dlp structured metadata and caption HTTP."""

from __future__ import annotations

import re
import threading
from collections.abc import Mapping
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .auth import build_requests_session, install_cookies, is_safe_caption_host
from .converter import MAX_CAPTION_BYTES
from .exceptions import CaptionDownloadError, ConfigurationError, DownloadCancelled, ExtractionError
from .models import CaptionTrack, SourceInfo, VideoInfo


def validate_youtube_url(url: str) -> str:
    value = url.strip()
    # A link copied from a Markdown note/chat is often pasted literally as
    # ``[label](https://www.youtube.com/...)``.  Accept its target so the GUI
    # receives the same URL the user intended to open.
    markdown_link = re.fullmatch(r"\[[^\]]*\]\((https?://[^)\s]+)\)", value)
    if markdown_link:
        value = markdown_link.group(1)
    # Markdown often escapes query separators in a copied link target.
    value = value.replace(r"\&", "&")
    try:
        parsed = urlparse(value)
    except ValueError as exc:
        raise ConfigurationError("Hãy nhập URL video hoặc danh sách phát YouTube hợp lệ.") from exc
    host = (parsed.hostname or "").lower()
    youtube_host = host == "youtube.com" or host.endswith(".youtube.com")
    if parsed.scheme not in {"http", "https"} or not (youtube_host or host == "youtu.be"):
        raise ConfigurationError("Chỉ hỗ trợ URL video, Shorts hoặc danh sách phát YouTube.")
    if host == "youtu.be" and parsed.path.strip("/"):
        return value
    allowed_path = parsed.path == "/watch" or parsed.path.startswith("/shorts/") or parsed.path == "/playlist"
    if not allowed_path:
        raise ConfigurationError("Hãy dùng URL video, Shorts hoặc danh sách phát; ứng dụng không hỗ trợ tải cả kênh.")
    return value


def resolve_playlist_mode(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.path == "/playlist" or bool(parse_qs(parsed.query).get("list"))


def _safe_external_error(error: BaseException) -> str:
    message = str(error).lower()
    if "playlist" in message and (
        "not found" in message or "does not exist" in message or "invalid" in message
    ):
        return (
            "Không tìm thấy danh sách phát này. Hãy mở playlist trên YouTube, "
            "sao chép lại URL đầy đủ từ thanh địa chỉ rồi thử lại."
        )
    if "bot" in message or "captcha" in message:
        return "YouTube yêu cầu đăng nhập hoặc xác minh bot. Hãy làm mới trang và xuất cookie từ trình duyệt của anh."
    if "sign in" in message or "login" in message or "cookie" in message:
        return "Video này cần phiên trình duyệt được cấp quyền. Hãy xuất cookie YouTube mới rồi thử lại."
    if "private" in message or "unavailable" in message or "members-only" in message:
        return "Video không khả dụng hoặc phiên trình duyệt hiện tại chưa được cấp quyền."
    if "geo" in message or "country" in message:
        return "Video không khả dụng tại khu vực hiện tại. Ứng dụng không vượt qua giới hạn khu vực."
    return "Không thể đọc metadata YouTube. Hãy thử lại sau hoặc cung cấp cookie mới được cấp quyền."


class _QuietLogger:
    def __init__(self, verbose: bool = False, output: Callable[[str], None] | None = None):
        self.verbose = verbose
        self.output = output

    def debug(self, message: str) -> None:
        if self.verbose and self.output and not str(message).startswith("[debug]"):
            self.output("yt-dlp: metadata step completed\n")

    def warning(self, _message: str) -> None:
        return

    def error(self, _message: str) -> None:
        return


class YouTubeClient:
    """Discover metadata with yt-dlp; fetch only one selected caption URL."""

    def __init__(
        self,
        cookie_rows: list[dict[str, Any]] | None = None,
        *,
        timeout: float = 30.0,
        retries: int = 3,
        verbose: bool = False,
        output: Callable[[str], None] | None = None,
    ):
        self.cookie_rows = cookie_rows or []
        self.timeout = timeout
        self.retries = retries
        self.verbose = verbose
        self.output = output
        self.session = build_requests_session(self.cookie_rows)
        retry = Retry(
            total=retries,
            connect=retries,
            read=retries,
            status=retries,
            backoff_factor=0.75,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def __enter__(self) -> "YouTubeClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self.session.close()

    def _ydl_options(
        self,
        playlist_mode: bool,
        *,
        flat_playlist: bool = False,
        ignore_errors: bool = False,
    ) -> dict[str, Any]:
        return {
            "skip_download": True,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": not playlist_mode,
            "extract_flat": "in_playlist" if flat_playlist else False,
            "ignoreerrors": ignore_errors,
            # Captions are useful even when YouTube does not expose a playable media format.
            "ignore_no_formats_error": True,
            "socket_timeout": self.timeout,
            "retries": self.retries,
            "fragment_retries": 0,
            "writethumbnail": False,
            "write_all_thumbnails": False,
            "writeinfojson": False,
            "writesubtitles": False,
            "writeautomaticsub": False,
            "cachedir": False,
            "logger": _QuietLogger(self.verbose, self.output),
            "extractor_args": {"youtube": {"skip": ["hls", "dash", "translated_subs"]}},
        }

    def extract(
        self,
        url: str,
        cancel_event: threading.Event | None = None,
    ) -> SourceInfo:
        validated_url = validate_youtube_url(url)
        use_playlist = resolve_playlist_mode(validated_url)
        try:
            import yt_dlp
            from yt_dlp.utils import DownloadError
        except ImportError as exc:
            raise ExtractionError("Chưa cài yt-dlp. Hãy chạy: python -m pip install -r requirements.txt") from exc

        try:
            with yt_dlp.YoutubeDL(
                self._ydl_options(use_playlist, flat_playlist=use_playlist)
            ) as ydl:
                if self.cookie_rows:
                    install_cookies(ydl.cookiejar, self.cookie_rows)
                # download=False is the only yt-dlp operation used by this application.
                raw = ydl.extract_info(validated_url, download=False)
        except DownloadError as exc:
            raise ExtractionError(_safe_external_error(exc)) from exc
        except Exception as exc:
            raise ExtractionError(_safe_external_error(exc)) from exc

        if not isinstance(raw, Mapping):
            raise ExtractionError("YouTube không trả về metadata video có thể truy cập.")
        if raw.get("_type") in {"playlist", "multi_video"} or isinstance(raw.get("entries"), (list, tuple)) or raw.get("entries") is not None:
            return self._resolve_playlist_entries(raw, validated_url, yt_dlp.YoutubeDL, DownloadError, cancel_event)
        return SourceInfo(
            source_type="video",
            title=str(raw.get("title") or raw.get("id") or "YouTube video"),
            url=validated_url,
            videos=[self._parse_video(raw, 1)],
        )

    def _resolve_playlist_entries(
        self,
        raw: Mapping[str, Any],
        source_url: str,
        ydl_class: Any,
        download_error: type[Exception],
        cancel_event: threading.Event | None,
    ) -> SourceInfo:
        """Resolve flat playlist rows individually so one entry cannot erase the rest.

        Watch Later commonly returns usable flat rows while per-entry media-format
        extraction fails. Resolving the canonical video URL with media formats
        ignored preserves those rows and still exposes subtitle metadata.
        """
        try:
            entries = list(raw.get("entries") or [])
        except TypeError:
            entries = []
        videos: list[VideoInfo] = []
        with ydl_class(self._ydl_options(False)) as ydl:
            if self.cookie_rows:
                install_cookies(ydl.cookiejar, self.cookie_rows)
            for position, entry in enumerate(entries, start=1):
                if cancel_event is not None and cancel_event.is_set():
                    raise DownloadCancelled()
                if not isinstance(entry, Mapping):
                    videos.append(VideoInfo(
                        index=position,
                        video_id=f"unavailable-{position}",
                        title=f"Video không khả dụng {position}",
                        webpage_url="",
                        error="Không khả dụng hoặc chưa được cấp quyền",
                    ))
                    continue
                playlist_index = entry.get("playlist_index")
                index = int(playlist_index) if isinstance(playlist_index, (int, float)) else position
                entry_id = str(entry.get("id") or f"unknown-{index}")
                entry_title = str(entry.get("title") or entry_id)
                detail_url = self._entry_video_url(entry)
                if detail_url is None:
                    videos.append(VideoInfo(
                        index=index,
                        video_id=entry_id,
                        title=entry_title,
                        webpage_url="",
                        error="Không có URL video hợp lệ",
                    ))
                    continue
                try:
                    detailed = ydl.extract_info(detail_url, download=False)
                except download_error as exc:
                    videos.append(VideoInfo(
                        index=index,
                        video_id=entry_id,
                        title=entry_title,
                        webpage_url=detail_url,
                        error=_safe_external_error(exc),
                    ))
                    continue
                except Exception as exc:
                    videos.append(VideoInfo(
                        index=index,
                        video_id=entry_id,
                        title=entry_title,
                        webpage_url=detail_url,
                        error=_safe_external_error(exc),
                    ))
                    continue
                if not isinstance(detailed, Mapping):
                    videos.append(VideoInfo(
                        index=index,
                        video_id=entry_id,
                        title=entry_title,
                        webpage_url=detail_url,
                        error="YouTube không trả về metadata video có thể truy cập",
                    ))
                    continue
                video = self._parse_video(detailed, index)
                if not detailed.get("title"):
                    video.title = entry_title
                if not detailed.get("id"):
                    video.video_id = entry_id
                videos.append(video)
        if not videos:
            raise ExtractionError("Danh sách phát không có mục nào có thể truy cập.")
        return SourceInfo(
            source_type="playlist",
            title=str(raw.get("title") or raw.get("playlist_title") or raw.get("id") or "YouTube playlist"),
            url=source_url,
            videos=videos,
        )

    @staticmethod
    def _entry_video_url(entry: Mapping[str, Any]) -> str | None:
        """Return a canonical YouTube watch URL from a flat playlist entry."""
        video_id = entry.get("id")
        if isinstance(video_id, str) and video_id and all(char.isalnum() or char in "_-" for char in video_id):
            return f"https://www.youtube.com/watch?v={video_id}"
        candidate = entry.get("webpage_url") or entry.get("url")
        if not isinstance(candidate, str):
            return None
        parsed = urlparse(candidate)
        if parsed.hostname == "youtu.be" and parsed.path.strip("/"):
            return candidate
        video_ids = parse_qs(parsed.query).get("v", [])
        if parsed.path == "/watch" and video_ids:
            return f"https://www.youtube.com/watch?v={video_ids[0]}"
        if parsed.path.startswith("/shorts/"):
            return candidate
        return None

    @staticmethod
    def _parse_video(raw: Mapping[str, Any], index: int) -> VideoInfo:
        video_id = str(raw.get("id") or f"unknown-{index}")
        duration = raw.get("duration")
        return VideoInfo(
            index=index,
            video_id=video_id,
            title=str(raw.get("title") or video_id or f"Video {index}"),
            webpage_url=str(raw.get("webpage_url") or raw.get("url") or ""),
            subtitles=raw.get("subtitles") if isinstance(raw.get("subtitles"), Mapping) else {},
            automatic_captions=(
                raw.get("automatic_captions") if isinstance(raw.get("automatic_captions"), Mapping) else {}
            ),
            availability=str(raw["availability"]) if raw.get("availability") else None,
            duration=float(duration) if isinstance(duration, (int, float)) else None,
        )

    @staticmethod
    def _safe_track_headers(track: CaptionTrack) -> dict[str, str]:
        allowed = {"accept", "accept-language", "user-agent", "referer", "origin"}
        headers: dict[str, str] = {}
        for name, value in track.http_headers.items():
            if name.lower() not in allowed:
                continue
            if name.lower() in {"referer", "origin"}:
                host = urlparse(value).hostname or ""
                if not is_safe_caption_host(host):
                    continue
            headers[name] = value
        return headers

    def download_caption(self, track: CaptionTrack, cancel_event: threading.Event | None = None) -> str:
        if track.data is not None:
            data = track.data.encode("utf-8") if isinstance(track.data, str) else track.data
        else:
            if not track.url:
                raise CaptionDownloadError("Phụ đề đã chọn không có dữ liệu để tải.")
            parsed = urlparse(track.url)
            if parsed.scheme != "https" or not is_safe_caption_host(parsed.hostname or ""):
                raise CaptionDownloadError("URL phụ đề không thuộc máy chủ YouTube hoặc Google được cho phép.")
            try:
                response_context = self.session.get(
                    track.url,
                    headers=self._safe_track_headers(track),
                    timeout=self.timeout,
                    stream=True,
                )
                with response_context as response:
                    if response.status_code == 429:
                        raise CaptionDownloadError("YouTube đang giới hạn yêu cầu phụ đề. Hãy chờ rồi thử lại sau.")
                    if response.status_code in {401, 403}:
                        raise CaptionDownloadError("Phụ đề cần cookie YouTube mới được cấp quyền.")
                    if response.status_code >= 400:
                        raise CaptionDownloadError(f"Yêu cầu phụ đề thất bại với HTTP {response.status_code}.")
                    length = response.headers.get("Content-Length")
                    if length and int(length) > MAX_CAPTION_BYTES:
                        raise CaptionDownloadError("Phản hồi phụ đề có kích thước lớn bất thường.")
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in response.iter_content(64 * 1024):
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > MAX_CAPTION_BYTES:
                            raise CaptionDownloadError("Phản hồi phụ đề có kích thước lớn bất thường.")
                        chunks.append(chunk)
                    data = b"".join(chunks)
            except CaptionDownloadError:
                raise
            except requests.RequestException as exc:
                raise CaptionDownloadError("Yêu cầu phụ đề thất bại sau số lần thử lại giới hạn.") from exc
            except (ValueError, TypeError) as exc:
                raise CaptionDownloadError("Header phản hồi phụ đề không hợp lệ.") from exc
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelled()
        if not data:
            raise CaptionDownloadError("Phản hồi phụ đề đang trống.")
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise CaptionDownloadError("Phản hồi phụ đề không phải văn bản UTF-8 hợp lệ.") from exc
