"""Playlist-safe orchestration, resume behavior, atomic files, and reports."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable, TextIO

from .captions import choose_best_english_caption
from .client import YouTubeClient
from .converter import appears_valid_subtitle, convert_subtitle
from .exceptions import ConfigurationError, DownloadCancelled, YouTubeSubtitleError
from .filenames import collection_directory, subtitle_path
from .models import DownloadOptions, SourceInfo, VideoInfo


ProgressCallback = Callable[[int, int, str], None]


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    try:
        with part.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(part, path)
    finally:
        if part.exists():
            try:
                part.unlink()
            except OSError:
                pass


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _existing_is_valid(path: Path, output_format: str) -> bool:
    try:
        # The requested title-only filename has no extension, so pass the
        # selected format explicitly to preserve validation for TXT output.
        probe_name = f"subtitle.{output_format.lower()}"
        return path.is_file() and appears_valid_subtitle(probe_name, path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError):
        return False


class SubtitleDownloader:
    def __init__(
        self,
        client: YouTubeClient,
        url: str,
        options: DownloadOptions,
        *,
        output: TextIO | None = None,
        log_callback: Callable[[str], None] | None = None,
        progress_callback: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
        retry_video_ids: set[str] | None = None,
        report_name: str = "download_report.json",
    ):
        self.client = client
        self.url = url
        self.options = options
        self.output = output
        self.log_callback = log_callback
        self.progress_callback = progress_callback
        self.cancel_event = cancel_event or threading.Event()
        self.retry_video_ids = set(retry_video_ids or ())
        self.report_name = report_name
        self.report_path: Path | None = None
        self.result_directory: Path | None = None

    def _log(self, message: str) -> None:
        text = message.rstrip() + "\n"
        if self.output is not None:
            self.output.write(text)
            self.output.flush()
        if self.log_callback is not None:
            self.log_callback(text)

    @staticmethod
    def _new_report(source: SourceInfo) -> dict[str, Any]:
        return {
            "source": {"type": source.source_type, "title": source.title, "url": source.url},
            "status": "running",
            "statistics": {
                "videos_scanned": 0,
                "manual_english": 0,
                "automatic_english": 0,
                "no_english": 0,
                "unavailable": 0,
                "downloaded": 0,
                "overwritten": 0,
                "skipped_existing": 0,
                "duplicates": 0,
                "failed": 0,
            },
            "videos": [],
        }

    @staticmethod
    def _video_record(video: VideoInfo, status: str, **subtitle: Any) -> dict[str, Any]:
        payload = {"status": status, **subtitle}
        return {
            "index": video.index,
            "video_id": video.video_id,
            "title": video.title,
            "subtitle": payload,
        }

    def _save(self, report: dict[str, Any]) -> None:
        if self.report_path is not None:
            _atomic_json(self.report_path, report)

    def run(self) -> Path:
        source = self.client.extract(self.url, self.cancel_event)
        self.result_directory = collection_directory(self.options.output, source.title)
        self.result_directory.mkdir(parents=True, exist_ok=True)
        self.report_path = self.result_directory / self.report_name
        report = self._new_report(source)
        videos = source.videos
        if self.retry_video_ids:
            videos = [video for video in videos if video.video_id in self.retry_video_ids]
            if not videos:
                raise ConfigurationError("Không tìm thấy video lỗi nào để tải lại trong URL này.")
            report["retry_only"] = True
        self._save(report)
        total = len(videos)
        if source.source_type == "playlist":
            self._log(f"Playlist: {source.title}\nVideos: {total}\n")
        else:
            self._log(f"Video: {source.title}\n")

        seen_ids: set[str] = set()
        try:
            for position, video in enumerate(videos, start=1):
                if self.cancel_event.is_set():
                    raise DownloadCancelled(self.report_path)
                if self.progress_callback:
                    self.progress_callback(position, total, video.title)
                self._log(f"[{position:03d}/{total:03d}] {video.title}")
                stats = report["statistics"]
                stats["videos_scanned"] += 1

                if video.unavailable:
                    stats["unavailable"] += 1
                    report["videos"].append(self._video_record(video, "unavailable"))
                    self._log("  - Unavailable or unauthorized\n")
                    self._save(report)
                    continue
                if video.video_id in seen_ids:
                    stats["duplicates"] += 1
                    report["videos"].append(self._video_record(video, "duplicate"))
                    self._log("  - Duplicate playlist entry - skipped\n")
                    self._save(report)
                    continue
                seen_ids.add(video.video_id)

                track = choose_best_english_caption(
                    video.subtitles,
                    video.automatic_captions,
                    self.options.language,
                )
                if track is None:
                    stats["no_english"] += 1
                    report["videos"].append(self._video_record(video, "no_english"))
                    self._log("  - No English subtitle\n")
                    self._save(report)
                    continue
                stats[f"{track.kind}_english"] += 1
                path = subtitle_path(
                    self.result_directory,
                    video.index,
                    video.title,
                    track.language,
                    self.options.output_format,
                )
                existing_valid = _existing_is_valid(path, self.options.output_format)
                if self.options.skip_existing and not self.options.overwrite and existing_valid:
                    stats["skipped_existing"] += 1
                    report["videos"].append(self._video_record(
                        video,
                        "skipped_existing",
                        language=track.language,
                        type=track.kind,
                        file=path.name,
                    ))
                    self._log("  [OK] Already exists - skipped\n")
                    self._save(report)
                    continue

                try:
                    raw_caption = self.client.download_caption(track, self.cancel_event)
                    converted = convert_subtitle(
                        raw_caption,
                        track.extension,
                        self.options.output_format,
                        source_url=video.webpage_url,
                    )
                    _atomic_text(path, converted)
                except DownloadCancelled:
                    raise DownloadCancelled(self.report_path)
                except (YouTubeSubtitleError, OSError, UnicodeError) as exc:
                    stats["failed"] += 1
                    report["videos"].append(self._video_record(
                        video,
                        "failed",
                        language=track.language,
                        type=track.kind,
                        error=str(exc),
                    ))
                    self._log(f"  - Failed: {exc}\n")
                    self._save(report)
                    continue

                was_overwritten = self.options.overwrite and existing_valid
                stats["overwritten" if was_overwritten else "downloaded"] += 1
                report["videos"].append(self._video_record(
                    video,
                    "overwritten" if was_overwritten else "downloaded",
                    language=track.language,
                    type=track.kind,
                    file=path.name,
                ))
                action = "overwritten" if was_overwritten else "downloaded"
                self._log(f"  [OK] English {track.kind} subtitle {action}\n")
                self._save(report)
                if self.options.delay > 0 and position < total and self.cancel_event.wait(self.options.delay):
                    raise DownloadCancelled(self.report_path)
        except DownloadCancelled:
            report["status"] = "cancelled"
            self._save(report)
            raise DownloadCancelled(self.report_path)

        report["status"] = "completed"
        self._save(report)
        return self.report_path
