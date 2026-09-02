"""Portable filename and path-length handling."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from .exceptions import FilenameError


_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE = re.compile(r"\s+")
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def sanitize_filename(value: str, max_length: int = 100, fallback: str = "Untitled") -> str:
    cleaned = unicodedata.normalize("NFKC", value)
    cleaned = _INVALID.sub("-", cleaned)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip(" .")
    if not cleaned:
        cleaned = fallback
    if cleaned.split(".", 1)[0].upper() in _RESERVED:
        cleaned = f"_{cleaned}"
    cleaned = cleaned[:max_length].rstrip(" .")
    return cleaned or fallback


def collection_directory(root: Path, title: str, max_total: int = 240) -> Path:
    root = root.expanduser().resolve(strict=False)
    available = max(12, min(80, max_total - len(str(root)) - 1))
    directory = root / sanitize_filename(title, available, "YouTube subtitles")
    if len(str(directory)) > max_total:
        raise FilenameError("Đường dẫn thư mục lưu quá dài. Hãy chọn vị trí ngắn hơn.")
    return directory


def subtitle_path(
    directory: Path,
    index: int,
    title: str,
    language: str,
    extension: str,
    *,
    max_total: int = 240,
) -> Path:
    # Subtitle filenames use the video title and selected output extension.
    # Caption language and playlist position are preserved in the report, not
    # in the filename.
    del language
    suffix = f".{extension.lower()}"
    available = min(130, max_total - len(str(directory)) - len(suffix) - 1)
    if available < 8:
        raise FilenameError("Đường dẫn đầu ra quá dài. Hãy chọn vị trí ngắn hơn.")
    safe_title = sanitize_filename(title, available, "Untitled")
    path = directory / f"{safe_title}{suffix}"
    # Preserve a readable title-only name while preventing a later playlist
    # item with the same title from replacing an earlier subtitle.
    if path.exists() and index > 1:
        marker = f" ({index})"
        safe_title = sanitize_filename(title, available - len(marker), "Untitled")
        path = directory / f"{safe_title}{marker}{suffix}"
    if len(str(path)) > max_total:
        raise FilenameError("Đường dẫn đầu ra quá dài. Hãy chọn vị trí ngắn hơn.")
    return path
