import sys
from types import SimpleNamespace

import youtube_downloader.instance as instance


def test_non_windows_process_is_always_primary(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert instance.acquire_primary_instance()


def test_existing_windows_mutex_closes_duplicate_handle(monkeypatch):
    calls = []

    class FakeCreateMutex:
        restype = None

        def __call__(self, *_args):
            return 123

    kernel32 = SimpleNamespace(
        CreateMutexW=FakeCreateMutex(),
        GetLastError=lambda: 183,
        CloseHandle=lambda handle: calls.append(handle),
    )
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(instance, "_mutex_handle", None)
    monkeypatch.setattr(instance.ctypes, "windll", SimpleNamespace(kernel32=kernel32))

    assert not instance.acquire_primary_instance()
    assert calls == [123]
    assert instance._mutex_handle is None
