import queue
import sys
import threading
from types import SimpleNamespace
from pathlib import Path

import pytest

import youtube_downloader.window_activation as window_activation
from youtube_downloader.gui import (
    GuiConfig,
    QueueWriter,
    retryable_video_ids,
    summarize_report,
    summarize_video_statuses,
    translate_activity_log,
    validate_gui_config,
)


def config(**overrides):
    values = {
        "url": "https://www.youtube.com/watch?v=abc",
        "output_path": Path("output"),
    }
    values.update(overrides)
    return GuiConfig(**values)


def test_public_video_config_needs_no_cookies():
    assert validate_gui_config(config()).cookie_path is None


def test_file_cookie_mode():
    result = validate_gui_config(config(cookie_path=Path("cookies.txt")))
    assert result.cookie_path == Path("cookies.txt") and not result.cookie_json


def test_pasted_cookie_mode():
    result = validate_gui_config(config(cookie_json="[]"))
    assert result.cookie_json == "[]" and result.cookie_path is None


def test_two_cookie_sources_rejected():
    with pytest.raises(ValueError):
        validate_gui_config(config(cookie_path=Path("a"), cookie_json="[]"))


def test_bad_numbers_rejected():
    with pytest.raises(ValueError):
        validate_gui_config(config(timeout=0))


def test_resume_modes_are_mutually_exclusive():
    with pytest.raises(ValueError):
        validate_gui_config(config(skip_existing=True, overwrite=True))
    with pytest.raises(ValueError):
        validate_gui_config(config(skip_existing=False, overwrite=False))


def test_completion_summary_counts_every_video_outcome():
    report = {
        "statistics": {
            "downloaded": 2,
            "overwritten": 1,
            "skipped_existing": 1,
            "duplicates": 1,
            "failed": 1,
            "unavailable": 1,
            "no_english": 1,
        },
        "videos": [{}, {}, {}, {}, {}, {}, {}, {}],
    }
    summary = summarize_report(report)
    assert (summary.total, summary.downloaded, summary.overwritten, summary.skipped, summary.failed) == (8, 2, 1, 2, 3)


def test_retryable_ids_include_only_unsuccessful_videos():
    report = {
        "videos": [
            {"video_id": "a", "subtitle": {"status": "failed"}},
            {"video_id": "b", "subtitle": {"status": "unavailable"}},
            {"video_id": "c", "subtitle": {"status": "no_english"}},
            {"video_id": "d", "subtitle": {"status": "skipped_existing"}},
            {"video_id": "e", "subtitle": {"status": "downloaded"}},
        ],
    }
    assert retryable_video_ids(report) == {"a", "b", "c"}


def test_session_summary_replaces_a_failed_outcome_after_auto_retry():
    initial = summarize_video_statuses(["downloaded", "failed", "skipped_existing"], 3)
    completed = summarize_video_statuses(["downloaded", "downloaded", "skipped_existing"], 3)
    assert initial.failed == 1
    assert (completed.total, completed.downloaded, completed.skipped, completed.failed) == (3, 2, 1, 0)


def test_queue_writer_never_touches_widgets():
    events = queue.Queue()
    writer = QueueWriter(events)
    assert writer.write("hello") == 5
    assert events.get_nowait() == ("log", "hello")


def test_activity_log_is_translated_for_gui():
    assert translate_activity_log("  - No English subtitle\n") == "  - Không có phụ đề tiếng Anh\n"


def test_single_instance_activation_focuses_registered_window(monkeypatch, tmp_path):
    calls = []

    class FakeUser32:
        @staticmethod
        def GetAncestor(handle, flag):
            calls.append(("ancestor", handle, flag))
            return 654

        @staticmethod
        def IsWindow(handle):
            calls.append(("is_window", handle))
            return True

        @staticmethod
        def ShowWindow(handle, command):
            calls.append(("show", handle, command))

        @staticmethod
        def BringWindowToTop(handle):
            calls.append(("bring", handle))

        @staticmethod
        def SetForegroundWindow(handle):
            calls.append(("foreground", handle))

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(window_activation.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(
        window_activation,
        "ctypes",
        SimpleNamespace(windll=SimpleNamespace(user32=FakeUser32())),
    )
    token = window_activation.register_window_target(456)
    try:
        assert window_activation.activate_latest_window()
    finally:
        window_activation.unregister_window_target(token)
    assert calls == [
        ("ancestor", 456, 2),
        ("is_window", 654),
        ("show", 654, 9),
        ("bring", 654),
        ("foreground", 654),
    ]


def test_single_instance_activation_uses_latest_registered_window(monkeypatch, tmp_path):
    class FakeUser32:
        @staticmethod
        def GetAncestor(handle, _flag):
            return handle

        @staticmethod
        def IsWindow(_handle):
            return True

        @staticmethod
        def ShowWindow(_handle, _command):
            return

        @staticmethod
        def BringWindowToTop(_handle):
            return

        @staticmethod
        def SetForegroundWindow(_handle):
            return

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(window_activation.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(
        window_activation,
        "ctypes",
        SimpleNamespace(windll=SimpleNamespace(user32=FakeUser32())),
    )
    token = window_activation.register_window_target(789)
    try:
        assert window_activation.activate_latest_window()
    finally:
        window_activation.unregister_window_target(token)


def test_close_running_gui_destroys_window_without_waiting_for_worker(monkeypatch):
    import youtube_downloader.gui as gui

    class FakeRoot:
        def __init__(self):
            self.destroyed = False

        def destroy(self):
            self.destroyed = True

    app = gui.SubtitleDownloaderApp.__new__(gui.SubtitleDownloaderApp)
    app.root = FakeRoot()
    app.worker = SimpleNamespace(is_alive=lambda: True)
    app.cancel_event = threading.Event()
    app.auto_retry_wait_event = threading.Event()
    app.closing = False
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *_args, **_kwargs: True)

    app._on_close()

    assert app.closing and app.root.destroyed
    assert app.cancel_event.is_set() and app.auto_retry_wait_event.is_set()


def test_gui_creation_smoke():
    import customtkinter as ctk
    import tkinter as tk
    from youtube_downloader.gui import BG, SubtitleDownloaderApp

    ctk.set_appearance_mode("dark")
    try:
        root = ctk.CTk(fg_color=BG)
    except tk.TclError:
        pytest.skip("No desktop display is available")
    root.withdraw()
    app = SubtitleDownloaderApp(root)
    root.update_idletasks()
    assert app.start_button.cget("text") == "Bắt đầu tải"
    assert app.cookie_tabs.get() == "Dán cookie JSON"
    app._close_immediately()


def test_new_window_copies_pasted_cookie_in_memory():
    import customtkinter as ctk
    import tkinter as tk
    from youtube_downloader.gui import BG, SubtitleDownloaderApp

    ctk.set_appearance_mode("dark")
    try:
        root = ctk.CTk(fg_color=BG)
    except tk.TclError:
        pytest.skip("No desktop display is available")
    root.withdraw()
    app = SubtitleDownloaderApp(root)
    app.cookie_json_text.insert("1.0", "[{\"name\": \"SID\"}]")
    app._open_new_window()
    child = app.child_windows[-1]
    assert child.cookie_tabs.get() == "Dán cookie JSON"
    assert child.cookie_json_text.get("1.0", "end-1c") == "[{\"name\": \"SID\"}]"
    app._close_immediately()
