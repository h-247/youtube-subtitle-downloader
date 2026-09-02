"""Windows notification-area icon and commands for every downloader window."""

from __future__ import annotations

import queue
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .gui import SubtitleDownloaderApp


class TrayController:
    """Own one tray icon while one or more GUI windows are open."""

    def __init__(self) -> None:
        self._apps: set[SubtitleDownloaderApp] = set()
        self._commands: queue.Queue[str] = queue.Queue()
        self._icon = None
        self._lock = threading.RLock()

    def register(self, app: SubtitleDownloaderApp) -> None:
        with self._lock:
            self._apps.add(app)
            if self._icon is None and sys.platform == "win32":
                self._start_icon()

    def unregister(self, app: SubtitleDownloaderApp) -> None:
        with self._lock:
            self._apps.discard(app)
            if not self._apps:
                self.stop()

    def request_show_all(self) -> None:
        self._commands.put("show_all")

    def request_close_all(self) -> None:
        self._commands.put("close_all")

    def drain_commands(self) -> None:
        """Run tray requests on the Tk main thread."""
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                return
            with self._lock:
                apps = tuple(self._apps)
            if command == "show_all":
                for app in apps:
                    app._focus_window()
            elif command == "close_all":
                for app in apps:
                    app._close_immediately()

    def stop(self) -> None:
        with self._lock:
            icon = self._icon
            self._icon = None
        if icon is not None:
            try:
                # NIM_DELETE must be sent before the tray message loop stops;
                # otherwise Explorer can keep a dead icon until it is hovered.
                if icon.visible:
                    icon.visible = False
            except Exception:
                pass
            try:
                icon.stop()
            except Exception:
                pass

    def shutdown(self) -> None:
        """Forget every GUI instance and remove the icon during app exit."""
        with self._lock:
            self._apps.clear()
        self.stop()

    def _start_icon(self) -> None:
        try:
            import pystray
            from PIL import Image

            icon = pystray.Icon(
                "youtube_subtitle_downloader",
                Image.open(_icon_path()).convert("RGBA"),
                "Trình tải phụ đề YouTube",
                menu=pystray.Menu(
                    pystray.MenuItem("Hiện tất cả cửa sổ", lambda _icon, _item: self.request_show_all()),
                    pystray.MenuItem("Đóng tất cả cửa sổ", lambda _icon, _item: self.request_close_all()),
                ),
            )
            self._icon = icon
            icon.run_detached(setup=self._finish_start)
        except Exception:
            # A missing tray backend must not prevent subtitle downloads.
            self._icon = None

    def _finish_start(self, icon) -> None:
        """Finish startup safely even when the last window closes immediately."""
        with self._lock:
            should_show = self._icon is icon and bool(self._apps)
        if should_show:
            icon.visible = True
        else:
            icon.stop()


def _icon_path() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "assets" / "app-icon.ico"


WINDOW_TRAY = TrayController()
