from types import SimpleNamespace

from youtube_downloader.tray import TrayController


def test_stop_hides_icon_before_stopping_it():
    calls = []

    class FakeIcon:
        def __init__(self):
            self._visible = True

        @property
        def visible(self):
            return self._visible

        @visible.setter
        def visible(self, value):
            self._visible = value
            calls.append(("visible", value))

        def stop(self):
            calls.append(("stop", None))

    controller = TrayController()
    controller._icon = FakeIcon()

    controller.stop()

    assert calls == [("visible", False), ("stop", None)]
    assert controller._icon is None


def test_shutdown_forgets_closed_windows():
    controller = TrayController()
    controller._apps.add(object())

    controller.shutdown()

    assert controller._apps == set()


def test_late_tray_start_does_not_show_icon_after_last_window_closed():
    calls = []
    icon = SimpleNamespace(visible=False, stop=lambda: calls.append("stop"))
    controller = TrayController()

    controller._finish_start(icon)

    assert icon.visible is False
    assert calls == ["stop"]
