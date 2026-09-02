"""Isolated parsing and deterministic English caption selection policy."""

from __future__ import annotations

import html
import re
from pathlib import PurePosixPath
from typing import Any, Mapping
from urllib.parse import urlparse

from .models import CaptionKind, CaptionTrack


FORMAT_PRIORITY = ("vtt", "srv3", "srv2", "srv1", "ttml", "json3")
_AUTO_MARKER = re.compile(r"\s*[\[(](?:auto|automatic|automatically generated|generated|asr)[\])]\s*", re.I)


def normalize_language(value: str) -> str:
    text = _AUTO_MARKER.sub("", value.strip()).lower().replace("_", "-")
    text = re.sub(r"\s+", " ", text)
    aliases = {
        "english": "en",
        "eng": "en",
        "english (us)": "en-us",
        "english (uk)": "en-gb",
        "english (gb)": "en-gb",
        "english-us": "en-us",
        "english-uk": "en-gb",
    }
    return aliases.get(text, text)


def _is_english_text(value: str) -> bool:
    normalized = normalize_language(value)
    return normalized == "en" or normalized.startswith("en-") or normalized.startswith("english")


def _language_for_track(language_key: str, item: Mapping[str, Any]) -> str | None:
    candidates: list[str] = [language_key]
    for key in ("language", "language_code", "lang", "code", "name", "label", "title"):
        value = item.get(key)
        if isinstance(value, str):
            candidates.append(value)
    for value in candidates:
        if _is_english_text(value):
            normalized = normalize_language(value)
            if normalized.startswith("english"):
                return "en"
            return normalized
    return None


def _extension(item: Mapping[str, Any]) -> str:
    ext = str(item.get("ext") or item.get("format") or "").lower().lstrip(".")
    if ext:
        return ext
    url = str(item.get("url") or "")
    return PurePosixPath(urlparse(url).path).suffix.lower().lstrip(".") or "vtt"


def _format_rank(item: Mapping[str, Any]) -> tuple[int, str]:
    ext = _extension(item)
    try:
        return FORMAT_PRIORITY.index(ext), ext
    except ValueError:
        return len(FORMAT_PRIORITY), ext


def _caption_candidates(collection: Mapping[str, Any] | None, kind: CaptionKind) -> list[CaptionTrack]:
    tracks: list[CaptionTrack] = []
    if not isinstance(collection, Mapping):
        return tracks
    for language_key, raw_formats in collection.items():
        formats = raw_formats if isinstance(raw_formats, list) else []
        english_formats: list[tuple[Mapping[str, Any], str]] = []
        for item in formats:
            if not isinstance(item, Mapping):
                continue
            language = _language_for_track(str(language_key), item)
            if language is not None and (item.get("url") or item.get("data") is not None):
                english_formats.append((item, language))
        if not english_formats:
            continue
        item, language = min(english_formats, key=lambda pair: _format_rank(pair[0]))
        raw_headers = item.get("http_headers")
        headers = {
            str(key): str(value)
            for key, value in raw_headers.items()
            if isinstance(raw_headers, Mapping) and isinstance(key, str) and isinstance(value, str)
        } if isinstance(raw_headers, Mapping) else {}
        tracks.append(CaptionTrack(
            language=language,
            kind=kind,
            extension=_extension(item),
            url=html.unescape(str(item["url"])) if item.get("url") else None,
            data=item.get("data") if isinstance(item.get("data"), (str, bytes)) else None,
            label=str(item.get("name") or item.get("label") or language_key),
            http_headers=headers,
        ))
    return tracks


def _language_rank(language: str, preferred_language: str) -> tuple[int, int, str]:
    normalized = normalize_language(language)
    preferred = normalize_language(preferred_language)
    exact = 0 if normalized == preferred else 1
    fixed = {"en": 0, "en-us": 1, "en-gb": 2, "en-orig": 3}.get(normalized, 4)
    return exact, fixed, normalized


def choose_best_english_caption(
    subtitles: Mapping[str, Any] | None,
    automatic_captions: Mapping[str, Any] | None,
    preferred_language: str = "en",
) -> CaptionTrack | None:
    """Prefer manual English, then automatic, with deterministic locale/format ranking."""
    if not _is_english_text(preferred_language):
        raise ValueError("Ứng dụng này chỉ tải phụ đề tiếng Anh.")
    manual = _caption_candidates(subtitles, "manual")
    automatic = _caption_candidates(automatic_captions, "automatic")
    pool = manual or automatic
    if not pool:
        return None
    return min(pool, key=lambda track: _language_rank(track.language, preferred_language))
