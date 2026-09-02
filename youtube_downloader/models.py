"""Small data models shared by the client, downloader, CLI, and GUI."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping


CaptionKind = Literal["manual", "automatic"]
OutputFormat = Literal["vtt", "srt", "txt"]


@dataclass(slots=True)
class CaptionTrack:
    language: str
    kind: CaptionKind
    extension: str
    url: str | None = None
    data: str | bytes | None = None
    label: str = ""
    http_headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class VideoInfo:
    index: int
    video_id: str
    title: str
    webpage_url: str
    subtitles: Mapping[str, Any] = field(default_factory=dict)
    automatic_captions: Mapping[str, Any] = field(default_factory=dict)
    availability: str | None = None
    duration: float | None = None
    error: str | None = None

    @property
    def unavailable(self) -> bool:
        return bool(self.error) or self.availability in {
            "private",
            "premium_only",
            "subscriber_only",
            "needs_auth",
        }


@dataclass(slots=True)
class SourceInfo:
    source_type: Literal["video", "playlist"]
    title: str
    url: str
    videos: list[VideoInfo]


@dataclass(slots=True)
class DownloadOptions:
    output: Path = Path("output")
    language: str = "en"
    output_format: OutputFormat = "vtt"
    skip_existing: bool = True
    overwrite: bool = False
    delay: float = 0.0
    timeout: float = 30.0
    retries: int = 3
    verbose: bool = False
