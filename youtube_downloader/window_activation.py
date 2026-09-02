"""Cross-process focus support for the single-instance GUI."""

from __future__ import annotations

import ctypes
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path


_TARGET_PREFIX = "youtube-subtitle-window-"


def register_window_target(window_handle: int) -> str:
    """Persist the native top-level HWND for later single-instance activation."""
    if sys.platform == "win32":
        try:
            top_level = int(ctypes.windll.user32.GetAncestor(window_handle, 2))  # GA_ROOT
            if top_level:
                window_handle = top_level
        except (AttributeError, OSError, TypeError, ValueError):
            pass
    token = uuid.uuid4().hex
    _target_path(token).write_text(
        json.dumps({"window_handle": window_handle, "pid": os.getpid()}),
        encoding="utf-8",
    )
    return token


def unregister_window_target(token: str) -> None:
    try:
        _target_path(token).unlink(missing_ok=True)
    except OSError:
        pass


def activate_latest_window() -> bool:
    """Bring the newest valid downloader window to the foreground."""
    if sys.platform != "win32":
        return False
    for token in _target_tokens_latest_first():
        try:
            data = json.loads(_target_path(token).read_text(encoding="utf-8"))
            handle = int(data["window_handle"])
            user32 = ctypes.windll.user32
            if not user32.IsWindow(handle):
                unregister_window_target(token)
                continue
            user32.ShowWindow(handle, 9)  # SW_RESTORE
            user32.BringWindowToTop(handle)
            user32.SetForegroundWindow(handle)
            return True
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            unregister_window_target(token)
    return False


def _target_path(token: str) -> Path:
    return Path(tempfile.gettempdir()) / f"{_TARGET_PREFIX}{token}.json"


def _target_tokens_latest_first():
    candidates = []
    for path in Path(tempfile.gettempdir()).glob(f"{_TARGET_PREFIX}*.json"):
        try:
            candidates.append((path.stat().st_mtime, path))
        except OSError:
            continue
    for _modified, path in sorted(candidates, reverse=True):
        token = path.stem.removeprefix(_TARGET_PREFIX)
        if len(token) == 32 and all(character in "0123456789abcdef" for character in token.lower()):
            yield token
