"""Windows single-instance lock for the packaged GUI."""

from __future__ import annotations

import atexit
import ctypes
import sys
from ctypes import wintypes


_MUTEX_NAME = "Local\\YouTubeSubtitleDownloader.GuiInstance"
_ERROR_ALREADY_EXISTS = 183
_mutex_handle = None


def acquire_primary_instance() -> bool:
    """Return False when another GUI process already owns the app mutex."""
    global _mutex_handle
    if sys.platform != "win32" or _mutex_handle is not None:
        return True

    kernel32 = ctypes.windll.kernel32
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    create_mutex.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    handle = create_mutex(None, False, _MUTEX_NAME)
    if not handle:
        # Do not make a Win32 API failure prevent the GUI from opening.
        return True
    if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
        close_handle(handle)
        return False

    _mutex_handle = handle
    atexit.register(release_primary_instance)
    return True


def release_primary_instance() -> None:
    """Release the mutex explicitly when the primary GUI exits."""
    global _mutex_handle
    handle = _mutex_handle
    _mutex_handle = None
    if handle is not None and sys.platform == "win32":
        try:
            close_handle = ctypes.windll.kernel32.CloseHandle
            close_handle.argtypes = [wintypes.HANDLE]
            close_handle.restype = wintypes.BOOL
            close_handle(handle)
        except (AttributeError, OSError):
            pass
