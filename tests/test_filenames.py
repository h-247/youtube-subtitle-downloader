from pathlib import Path

import pytest

from youtube_downloader.exceptions import FilenameError
from youtube_downloader.filenames import collection_directory, sanitize_filename, subtitle_path


def test_windows_invalid_characters():
    assert sanitize_filename('<bad>:"name"/\\|?*') == "-bad---name------"


def test_unicode_is_preserved():
    assert sanitize_filename("Tiếng Việt 日本語") == "Tiếng Việt 日本語"


def test_windows_reserved_names_are_prefixed():
    assert sanitize_filename("CON.txt") == "_CON.txt"


def test_trailing_dots_and_spaces_removed():
    assert sanitize_filename("title...   ") == "title"


def test_long_title_is_bounded(tmp_path: Path):
    directory = collection_directory(tmp_path, "Playlist")
    path = subtitle_path(directory, 1, "x" * 500, "en", "vtt")
    assert len(str(path)) <= 240 and path.name.endswith(".vtt")
    assert not path.name.startswith("001 - ")


def test_duplicate_titles_are_unique_by_index(tmp_path: Path):
    directory = collection_directory(tmp_path, "Playlist")
    first = subtitle_path(directory, 1, "Same", "en", "vtt")
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_text("WEBVTT", encoding="utf-8")
    second = subtitle_path(directory, 2, "Same", "en", "vtt")
    assert first.name == "Same.vtt" and second.name == "Same (2).vtt"


def test_impossibly_long_root_is_rejected():
    root = Path("C:/") / ("a" * 250)
    with pytest.raises(FilenameError):
        collection_directory(root, "Title")
