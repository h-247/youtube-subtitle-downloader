"""Responsive customtkinter dark-mode desktop interface."""

from __future__ import annotations

import os
import queue
import json
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any

import customtkinter as ctk

from .auth import load_cookie_file, parse_cookie_export
from .client import YouTubeClient, validate_youtube_url
from .downloader import SubtitleDownloader
from .exceptions import DownloadCancelled, YouTubeSubtitleError
from .instance import acquire_primary_instance, release_primary_instance
from .models import DownloadOptions
from .tray import WINDOW_TRAY
from .window_activation import activate_latest_window, register_window_target, unregister_window_target


BG = "#0F1115"
SURFACE = "#171A21"
ELEVATED = "#1F2430"
ACCENT = "#4C8DFF"
ACCENT_HOVER = "#3678E5"
SUCCESS = "#35C46A"
WARNING = "#F5A524"
ERROR = "#F05252"
TEXT = "#F4F6FA"
MUTED = "#A7AFBF"
BORDER = "#303746"
COOKIE_FILE_TAB = "Chọn tệp cookie"
COOKIE_JSON_TAB = "Dán cookie JSON"
SKIP_EXISTING_MODE = "Bỏ qua tệp có sẵn"
OVERWRITE_MODE = "Ghi đè tệp có sẵn"


@dataclass(slots=True)
class GuiConfig:
    url: str
    output_path: Path
    cookie_path: Path | None = None
    cookie_json: str = ""
    language: str = "en"
    output_format: str = "vtt"
    skip_existing: bool = True
    overwrite: bool = False
    delay: float = 0.0
    timeout: float = 30.0
    retries: int = 3


@dataclass(frozen=True, slots=True)
class DownloadSummary:
    total: int
    downloaded: int
    overwritten: int
    skipped: int
    failed: int

    @property
    def text(self) -> str:
        return (
            f"Tổng: {self.total} video\n"
            f"Tải thành công: {self.downloaded}\n"
            f"Ghi đè: {self.overwritten}\n"
            f"Bỏ qua: {self.skipped}\n"
            f"Không tải được: {self.failed}"
        )


RETRYABLE_STATUSES = frozenset({"failed", "unavailable", "no_english"})


def summarize_report(report: dict[str, Any]) -> DownloadSummary:
    stats = report.get("statistics", {})
    videos = report.get("videos", [])
    total = len(videos) if isinstance(videos, list) else int(stats.get("videos_scanned", 0))
    downloaded = int(stats.get("downloaded", 0))
    overwritten = int(stats.get("overwritten", 0))
    skipped = int(stats.get("skipped_existing", 0)) + int(stats.get("duplicates", 0))
    failed = int(stats.get("failed", 0)) + int(stats.get("unavailable", 0)) + int(stats.get("no_english", 0))
    return DownloadSummary(total, downloaded, overwritten, skipped, failed)


def retryable_video_ids(report: dict[str, Any]) -> set[str]:
    videos = report.get("videos", [])
    if not isinstance(videos, list):
        return set()
    return {
        str(item.get("video_id"))
        for item in videos
        if isinstance(item, dict)
        and isinstance(item.get("subtitle"), dict)
        and item["subtitle"].get("status") in RETRYABLE_STATUSES
        and item.get("video_id")
    }


def summarize_video_statuses(statuses: list[str], total: int) -> DownloadSummary:
    return DownloadSummary(
        total=total,
        downloaded=statuses.count("downloaded"),
        overwritten=statuses.count("overwritten"),
        skipped=statuses.count("skipped_existing") + statuses.count("duplicate"),
        failed=sum(status in RETRYABLE_STATUSES for status in statuses),
    )


def validate_gui_config(config: GuiConfig) -> GuiConfig:
    config.url = validate_youtube_url(config.url)
    if config.output_format not in {"vtt", "srt", "txt"}:
        raise ValueError("Định dạng đầu ra phải là VTT, SRT hoặc TXT.")
    language = config.language.lower().replace("_", "-")
    if language.split("-", 1)[0] not in {"en", "english"}:
        raise ValueError("Ngôn ngữ ưu tiên phải là một biến thể tiếng Anh.")
    if config.delay < 0 or config.timeout <= 0 or not 0 <= config.retries <= 10:
        raise ValueError("Độ trễ không được âm, thời gian chờ phải dương và số lần thử lại từ 0 đến 10.")
    if config.cookie_path is not None and config.cookie_json:
        raise ValueError("Chỉ chọn tệp cookie hoặc dán JSON, không dùng đồng thời cả hai.")
    if config.skip_existing == config.overwrite:
        raise ValueError("Hãy chọn đúng một chế độ: bỏ qua tệp có sẵn hoặc ghi đè.")
    return config


def translate_activity_log(text: str) -> str:
    """Translate the downloader's controlled activity messages for the GUI."""
    replacements = (
        ("Playlist: ", "Danh sách phát: "),
        ("Videos: ", "Số video: "),
        ("  - Unavailable or unauthorized", "  - Không khả dụng hoặc chưa được cấp quyền"),
        ("  - Duplicate playlist entry - skipped", "  - Mục trùng trong danh sách phát - đã bỏ qua"),
        ("  - No English subtitle", "  - Không có phụ đề tiếng Anh"),
        ("  [OK] Already exists - skipped", "  [OK] Tệp đã tồn tại - đã bỏ qua"),
        ("  [OK] English manual subtitle downloaded", "  [OK] Đã tải phụ đề tiếng Anh thủ công"),
        ("  [OK] English automatic subtitle downloaded", "  [OK] Đã tải phụ đề tiếng Anh tự động"),
        ("  [OK] English manual subtitle overwritten", "  [OK] Đã ghi đè phụ đề tiếng Anh thủ công"),
        ("  [OK] English automatic subtitle overwritten", "  [OK] Đã ghi đè phụ đề tiếng Anh tự động"),
        ("  - Failed: ", "  - Thất bại: "),
    )
    translated = text
    for source, target in replacements:
        translated = translated.replace(source, target)
    return translated


class QueueWriter:
    def __init__(self, events: queue.Queue[tuple[str, Any]]):
        self.events = events

    def write(self, text: str) -> int:
        if text:
            self.events.put(("log", text))
        return len(text)

    def flush(self) -> None:
        return


class SubtitleDownloaderApp:
    POLL_MS = 80
    AUTO_RETRY_DELAY_MS = 5_000

    def __init__(
        self,
        root: ctk.CTk | ctk.CTkToplevel,
        *,
        initial_cookie_path: str = "",
        initial_cookie_json: str = "",
    ):
        self.root = root
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_event: threading.Event | None = None
        self.report_path: Path | None = None
        self.result_directory: Path | None = None
        self.last_config: GuiConfig | None = None
        self.retry_video_ids: set[str] = set()
        self.session_video_statuses: dict[str, str] = {}
        self.session_total = 0
        self.auto_retry_attempt = 0
        self.auto_retry_wait_event: threading.Event | None = None
        self.closing = False
        self.child_windows: list[SubtitleDownloaderApp] = []
        self.completion_popup: ctk.CTkToplevel | None = None
        self.window_token = register_window_target(int(root.winfo_id()))

        root.title("Trình tải phụ đề tiếng Anh từ YouTube")
        root.geometry("960x780")
        root.minsize(820, 680)
        root.configure(fg_color=BG)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.url_var = tk.StringVar()
        self.cookie_file_var = tk.StringVar()
        self.output_var = tk.StringVar(value=str(Path.cwd() / "output"))
        self.language_var = tk.StringVar(value="en")
        self.format_var = tk.StringVar(value="vtt")
        self.resume_mode_var = tk.StringVar(value=SKIP_EXISTING_MODE)
        self.delay_var = tk.StringVar(value="0")
        self.timeout_var = tk.StringVar(value="30")
        self.retries_var = tk.StringVar(value="3")
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_text_var = tk.StringVar(value="0 / 0")
        self.status_var = tk.StringVar(value="Sẵn sàng")

        self._build()
        self._apply_initial_cookies(initial_cookie_path, initial_cookie_json)
        WINDOW_TRAY.register(self)
        self.root.after(self.POLL_MS, self._poll_events)

    def _build(self) -> None:
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(3, weight=1)
        header = ctk.CTkFrame(self.root, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 10))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            header,
            text="Trình tải phụ đề tiếng Anh từ YouTube",
            text_color=TEXT,
            font=ctk.CTkFont(size=24, weight="bold"),
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header,
            text="Chỉ tải phụ đề. Ứng dụng không tải video, âm thanh, ảnh thu nhỏ hoặc phân đoạn media.",
            text_color=MUTED,
            font=ctk.CTkFont(size=12),
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        form = ctk.CTkFrame(self.root, fg_color=SURFACE, corner_radius=12, border_width=1, border_color=BORDER)
        form.grid(row=1, column=0, sticky="ew", padx=24, pady=(0, 10))
        form.grid_columnconfigure(1, weight=1)
        label = {"text_color": MUTED, "font": ctk.CTkFont(size=12, weight="bold")}

        ctk.CTkLabel(form, text="URL YOUTUBE", **label).grid(row=0, column=0, sticky="w", padx=(18, 12), pady=(16, 8))
        self.url_entry = ctk.CTkEntry(
            form, textvariable=self.url_var, height=34, fg_color=ELEVATED, border_color=BORDER,
            text_color=TEXT, placeholder_text="https://www.youtube.com/watch?v=...",
        )
        self.url_entry.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(0, 18), pady=(16, 8))

        ctk.CTkLabel(form, text="COOKIE (TÙY CHỌN)", **label).grid(row=1, column=0, sticky="nw", padx=(18, 12), pady=8)
        self.cookie_tabs = ctk.CTkTabview(
            form, height=108, fg_color=ELEVATED, segmented_button_fg_color=ELEVATED,
            segmented_button_selected_color=ACCENT, segmented_button_selected_hover_color=ACCENT_HOVER,
            text_color=TEXT, border_width=1, border_color=BORDER,
        )
        self.cookie_tabs.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(0, 18), pady=8)
        paste_tab = self.cookie_tabs.add(COOKIE_JSON_TAB)
        file_tab = self.cookie_tabs.add(COOKIE_FILE_TAB)
        self.cookie_tabs.set(COOKIE_JSON_TAB)
        file_tab.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(
            file_tab, textvariable=self.cookie_file_var, height=32, fg_color=BG, border_color=BORDER,
            text_color=TEXT, placeholder_text="Tùy chọn: Netscape .txt hoặc Cookie-Editor .json",
        ).grid(row=0, column=0, sticky="ew", padx=(8, 6), pady=8)
        ctk.CTkButton(
            file_tab, text="Chọn tệp", width=92, height=32, command=self._choose_cookie,
            fg_color=ELEVATED, hover_color=BORDER, border_width=1, border_color=BORDER,
        ).grid(row=0, column=1, padx=(0, 8), pady=8)
        paste_tab.grid_columnconfigure(0, weight=1)
        self.cookie_json_text = ctk.CTkTextbox(
            paste_tab, height=58, fg_color=BG, border_width=1, border_color=BORDER,
            text_color=TEXT, wrap="none",
        )
        self.cookie_json_text.grid(row=0, column=0, rowspan=2, sticky="ew", padx=(8, 6), pady=8)
        ctk.CTkButton(
            paste_tab, text="Dán", width=92, height=27, command=self._paste_cookie,
            fg_color=ELEVATED, hover_color=BORDER, border_width=1, border_color=BORDER,
        ).grid(row=0, column=1, padx=(0, 8), pady=(8, 2))
        ctk.CTkButton(
            paste_tab, text="Xóa", width=92, height=27, command=self._clear_cookie,
            fg_color="transparent", hover_color=BORDER, border_width=1, border_color=BORDER,
        ).grid(row=1, column=1, padx=(0, 8), pady=(2, 8))

        ctk.CTkLabel(form, text="THƯ MỤC LƯU", **label).grid(row=2, column=0, sticky="w", padx=(18, 12), pady=8)
        ctk.CTkEntry(
            form, textvariable=self.output_var, height=34, fg_color=ELEVATED, border_color=BORDER, text_color=TEXT,
        ).grid(row=2, column=1, sticky="ew", padx=(0, 6), pady=8)
        ctk.CTkButton(
            form, text="Chọn thư mục", width=108, height=34, command=self._choose_output,
            fg_color=ELEVATED, hover_color=BORDER, border_width=1, border_color=BORDER,
        ).grid(row=2, column=2, padx=(0, 18), pady=8)

        options = ctk.CTkFrame(form, fg_color="transparent")
        options.grid(row=3, column=0, columnspan=3, sticky="ew", padx=18, pady=8)
        for column in range(5):
            options.grid_columnconfigure(column, weight=1 if column in {1, 3, 4} else 0)
        ctk.CTkLabel(options, text="Ưu tiên", text_color=MUTED).grid(row=0, column=0, padx=(0, 6))
        ctk.CTkOptionMenu(
            options, variable=self.language_var, values=["en", "en-US", "en-GB", "en-orig"],
            width=105, fg_color=ELEVATED, button_color=BORDER, button_hover_color=ACCENT_HOVER,
        ).grid(row=0, column=1, sticky="w", padx=(0, 16))
        ctk.CTkLabel(options, text="Định dạng", text_color=MUTED).grid(row=0, column=2, padx=(0, 6))
        ctk.CTkOptionMenu(
            options, variable=self.format_var, values=["vtt", "srt", "txt"],
            width=90, fg_color=ELEVATED, button_color=BORDER, button_hover_color=ACCENT_HOVER,
        ).grid(row=0, column=3, sticky="w", padx=(0, 16))
        self.resume_mode = ctk.CTkSegmentedButton(
            options,
            values=[SKIP_EXISTING_MODE, OVERWRITE_MODE],
            variable=self.resume_mode_var,
            fg_color=ELEVATED,
            selected_color=ACCENT,
            selected_hover_color=ACCENT_HOVER,
            unselected_color=ELEVATED,
            unselected_hover_color=BORDER,
            width=285,
        )
        self.resume_mode.grid(row=0, column=4, sticky="e")

        advanced = ctk.CTkFrame(form, fg_color="transparent")
        advanced.grid(row=4, column=0, columnspan=3, sticky="w", padx=18, pady=(4, 8))
        for text, variable, width in (
            ("Độ trễ (giây)", self.delay_var, 70),
            ("Thời gian chờ (giây)", self.timeout_var, 80),
            ("Số lần thử lại", self.retries_var, 65),
        ):
            ctk.CTkLabel(advanced, text=text, text_color=MUTED).pack(side="left", padx=(0, 6))
            ctk.CTkEntry(
                advanced, textvariable=variable, width=width, height=30,
                fg_color=ELEVATED, border_color=BORDER, text_color=TEXT,
            ).pack(side="left", padx=(0, 16))
        ctk.CTkLabel(
            form,
            text="Cookie chứa thông tin phiên đăng nhập nhạy cảm. Không commit, không chia sẻ và hãy xóa khi không còn dùng.",
            text_color=WARNING, wraplength=820, justify="left", font=ctk.CTkFont(size=11),
        ).grid(row=5, column=0, columnspan=3, sticky="w", padx=18, pady=(0, 14))

        progress = ctk.CTkFrame(self.root, fg_color=SURFACE, corner_radius=12, border_width=1, border_color=BORDER)
        progress.grid(row=2, column=0, sticky="ew", padx=24, pady=(0, 10))
        progress.grid_columnconfigure(0, weight=1)
        top = ctk.CTkFrame(progress, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(10, 4))
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(top, textvariable=self.status_var, text_color=TEXT, anchor="w").grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(top, textvariable=self.progress_text_var, text_color=MUTED).grid(row=0, column=1, sticky="e")
        self.progress = ctk.CTkProgressBar(progress, variable=self.progress_var, progress_color=ACCENT, fg_color=ELEVATED)
        self.progress.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 11))
        self.progress.set(0)

        log_card = ctk.CTkFrame(self.root, fg_color=SURFACE, corner_radius=12, border_width=1, border_color=BORDER)
        log_card.grid(row=3, column=0, sticky="nsew", padx=24, pady=(0, 10))
        log_card.grid_rowconfigure(1, weight=1)
        log_card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            log_card, text="NHẬT KÝ HOẠT ĐỘNG", text_color=MUTED, font=ctk.CTkFont(size=12, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=14, pady=(10, 4))
        self.log = ctk.CTkTextbox(
            log_card, fg_color=BG, text_color=TEXT, border_width=1, border_color=BORDER,
            corner_radius=8, wrap="word", state="disabled",
        )
        self.log.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

        actions = ctk.CTkFrame(self.root, fg_color="transparent")
        actions.grid(row=4, column=0, sticky="ew", padx=24, pady=(0, 18))
        self.start_button = ctk.CTkButton(
            actions, text="Bắt đầu tải", command=self._start, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            height=38, font=ctk.CTkFont(weight="bold"),
        )
        self.start_button.pack(side="left")
        self.cancel_button = ctk.CTkButton(
            actions, text="Hủy", command=self._cancel, fg_color=ERROR, hover_color="#C73F3F", height=38,
            state="disabled",
        )
        self.cancel_button.pack(side="left", padx=(8, 0))
        self.retry_failed_button = ctk.CTkButton(
            actions, text="Chỉ tải lại mục lỗi", command=self._retry_failed,
            fg_color=ELEVATED, hover_color=BORDER, border_width=1, border_color=BORDER,
            height=38, state="disabled",
        )
        self.retry_failed_button.pack(side="left", padx=(8, 0))
        ctk.CTkButton(
            actions, text="Tạo cửa sổ mới", command=self._open_new_window, fg_color=ELEVATED, hover_color=BORDER,
            border_width=1, border_color=BORDER, height=38,
        ).pack(side="left", padx=(8, 0))
        ctk.CTkButton(
            actions, text="Xóa nhật ký", command=self._clear_log, fg_color="transparent", hover_color=BORDER,
            border_width=1, border_color=BORDER, height=38,
        ).pack(side="left", padx=(8, 0))
        self.open_report_button = ctk.CTkButton(
            actions, text="Mở báo cáo", command=self._open_report, fg_color=ELEVATED, hover_color=BORDER,
            border_width=1, border_color=BORDER, height=38, state="disabled",
        )
        self.open_report_button.pack(side="right")
        self.open_folder_button = ctk.CTkButton(
            actions, text="Mở thư mục kết quả", command=self._open_folder, fg_color=ELEVATED, hover_color=BORDER,
            border_width=1, border_color=BORDER, height=38, state="disabled",
        )
        self.open_folder_button.pack(side="right", padx=(0, 8))

    def _choose_cookie(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Chọn tệp cookie YouTube",
            filetypes=(("Tệp cookie", "*.json *.txt"), ("Tất cả tệp", "*.*")),
        )
        if path:
            self.cookie_file_var.set(path)

    def _choose_output(self) -> None:
        path = filedialog.askdirectory(parent=self.root, title="Chọn thư mục lưu phụ đề")
        if path:
            self.output_var.set(path)

    def _paste_cookie(self) -> None:
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            messagebox.showerror("Bộ nhớ tạm", "Bộ nhớ tạm không chứa văn bản.", parent=self.root)
            return
        self.cookie_json_text.delete("1.0", "end")
        self.cookie_json_text.insert("1.0", text)

    def _clear_cookie(self) -> None:
        self.cookie_json_text.delete("1.0", "end")

    def _apply_initial_cookies(self, cookie_path: str, cookie_json: str) -> None:
        if cookie_json:
            self.cookie_json_text.insert("1.0", cookie_json)
            self.cookie_tabs.set(COOKIE_JSON_TAB)
        elif cookie_path:
            self.cookie_file_var.set(cookie_path)
            self.cookie_tabs.set(COOKIE_FILE_TAB)

    def _open_new_window(self) -> None:
        cookie_path = ""
        cookie_json = ""
        if self.cookie_tabs.get() == COOKIE_JSON_TAB:
            cookie_json = self.cookie_json_text.get("1.0", "end-1c")
        else:
            cookie_path = self.cookie_file_var.get().strip()
        window = ctk.CTkToplevel(self.root, fg_color=BG)
        child = SubtitleDownloaderApp(
            window,
            initial_cookie_path=cookie_path,
            initial_cookie_json=cookie_json,
        )
        child.output_var.set(self.output_var.get())
        child.language_var.set(self.language_var.get())
        child.format_var.set(self.format_var.get())
        child.resume_mode_var.set(self.resume_mode_var.get())
        child.delay_var.set(self.delay_var.get())
        child.timeout_var.set(self.timeout_var.get())
        child.retries_var.set(self.retries_var.get())
        child.status_var.set("Cửa sổ mới sẵn sàng tải song song")
        self.child_windows.append(child)

    def _collect_config(self) -> GuiConfig:
        try:
            delay = float(self.delay_var.get())
            timeout = float(self.timeout_var.get())
            retries = int(self.retries_var.get())
        except ValueError as exc:
            raise ValueError("Độ trễ, thời gian chờ và số lần thử lại phải là số hợp lệ.") from exc
        output = self.output_var.get().strip()
        if not output:
            raise ValueError("Hãy chọn thư mục lưu kết quả.")
        cookie_path: Path | None = None
        cookie_json = ""
        if self.cookie_tabs.get() == COOKIE_FILE_TAB:
            value = self.cookie_file_var.get().strip()
            cookie_path = Path(value) if value else None
        else:
            cookie_json = self.cookie_json_text.get("1.0", "end-1c").strip()
        return validate_gui_config(GuiConfig(
            url=self.url_var.get(),
            output_path=Path(output),
            cookie_path=cookie_path,
            cookie_json=cookie_json,
            language=self.language_var.get(),
            output_format=self.format_var.get(),
            skip_existing=self.resume_mode_var.get() == SKIP_EXISTING_MODE,
            overwrite=self.resume_mode_var.get() == OVERWRITE_MODE,
            delay=delay,
            timeout=timeout,
            retries=retries,
        ))

    def _start(self) -> None:
        try:
            config = self._collect_config()
            if config.cookie_json:
                parse_cookie_export(config.cookie_json)
            elif config.cookie_path:
                load_cookie_file(config.cookie_path)
        except (ValueError, YouTubeSubtitleError, OSError) as exc:
            messagebox.showerror("Cấu hình chưa hợp lệ", str(exc), parent=self.root)
            return
        self.session_video_statuses = {}
        self.session_total = 0
        self.auto_retry_attempt = 0
        self._start_download(config)

    def _start_download(self, config: GuiConfig, *, retry_ids: set[str] | None = None) -> None:
        self.report_path = None
        self.result_directory = None
        self.cancel_event = threading.Event()
        self.auto_retry_wait_event = None
        self.last_config = config
        self.retry_video_ids = set()
        self._clear_log()
        self.progress_var.set(0)
        self.progress_text_var.set("0 / 0")
        self.status_var.set("Đang tìm các video lỗi để tải lại..." if retry_ids else "Đang tìm phụ đề trên YouTube...")
        self._set_running(True)
        self.worker = threading.Thread(
            target=self._worker,
            args=(config, self.cancel_event, retry_ids),
            name="youtube-subtitle-worker",
            daemon=True,
        )
        self.worker.start()

    def _worker(
        self,
        config: GuiConfig,
        cancel_event: threading.Event,
        retry_ids: set[str] | None = None,
    ) -> None:
        downloader: SubtitleDownloader | None = None
        try:
            rows = parse_cookie_export(config.cookie_json) if config.cookie_json else (
                load_cookie_file(config.cookie_path) if config.cookie_path else []
            )
            options = DownloadOptions(
                output=config.output_path,
                language=config.language,
                output_format=config.output_format,  # type: ignore[arg-type]
                skip_existing=config.skip_existing,
                overwrite=config.overwrite,
                delay=config.delay,
                timeout=config.timeout,
                retries=config.retries,
            )
            with YouTubeClient(rows, timeout=config.timeout, retries=config.retries) as client:
                downloader = SubtitleDownloader(
                    client,
                    config.url,
                    options,
                    log_callback=lambda text: self.events.put(("log", translate_activity_log(text))),
                    progress_callback=lambda current, total, title: self.events.put(
                        ("progress", (current, total, title))
                    ),
                    cancel_event=cancel_event,
                    retry_video_ids=retry_ids,
                    report_name="download_report_retry.json" if retry_ids else "download_report.json",
                )
                report = downloader.run()
            self.events.put(("done", ("completed", report, report.parent)))
        except DownloadCancelled as exc:
            report = exc.report_path or (downloader.report_path if downloader else None)
            self.events.put(("done", ("cancelled", report, report.parent if report else None)))
        except YouTubeSubtitleError as exc:
            self.events.put(("done", ("error", str(exc), None)))
        except Exception as exc:
            self.events.put(("done", ("error", f"Lỗi nội bộ không mong đợi ({exc.__class__.__name__}).", None)))

    def _retry_failed(self) -> None:
        if self.last_config is None or not self.retry_video_ids:
            return
        self.auto_retry_attempt = 0
        self._start_download(self.last_config, retry_ids=set(self.retry_video_ids))

    def _merge_session_report(self, report: dict[str, Any]) -> DownloadSummary:
        videos = report.get("videos", [])
        if not isinstance(videos, list):
            return summarize_video_statuses(list(self.session_video_statuses.values()), self.session_total)
        if not report.get("retry_only"):
            self.session_video_statuses = {}
            self.session_total = len(videos)
        for item in videos:
            if not isinstance(item, dict) or not isinstance(item.get("subtitle"), dict):
                continue
            status = item["subtitle"].get("status")
            if not isinstance(status, str):
                continue
            key = f"{item.get('index')}:{item.get('video_id')}"
            self.session_video_statuses[key] = status
        return summarize_video_statuses(list(self.session_video_statuses.values()), self.session_total)

    def _session_retryable_ids(self) -> set[str]:
        return {
            key.split(":", 1)[1]
            for key, status in self.session_video_statuses.items()
            if status in RETRYABLE_STATUSES
        }

    def _schedule_auto_retry(self, config: GuiConfig, retry_ids: set[str]) -> None:
        """Retry failed rows after a short pause while keeping cancellation available."""
        wait_event = threading.Event()
        self.auto_retry_wait_event = wait_event
        self.cancel_event = wait_event
        self._set_running(True)

        def begin_retry() -> None:
            if wait_event.is_set() or self.closing:
                self._set_running(False)
                self._enable_results()
                self.status_var.set("Đã dừng tự động tải lại")
                self.retry_failed_button.configure(state="normal" if self.retry_video_ids else "disabled")
                return
            self._start_download(config, retry_ids=retry_ids)

        self.root.after(self.AUTO_RETRY_DELAY_MS, begin_retry)

    def _poll_events(self) -> None:
        WINDOW_TRAY.drain_commands()
        try:
            for _ in range(300):
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "progress":
                    current, total, title = payload
                    self.progress_var.set(current / total if total else 0)
                    self.progress_text_var.set(f"{current} / {total}")
                    self.status_var.set(str(title))
                elif kind == "done":
                    self._handle_done(*payload)
        except queue.Empty:
            pass
        try:
            if self.root.winfo_exists():
                self.root.after(self.POLL_MS, self._poll_events)
        except tk.TclError:
            pass

    def _handle_done(self, outcome: str, detail: Any, directory: Path | None) -> None:
        self._set_running(False)
        if outcome == "completed":
            self.report_path = Path(detail)
            self.result_directory = directory
            self.progress_var.set(1)
            try:
                report = json.loads(self.report_path.read_text(encoding="utf-8"))
                summary = self._merge_session_report(report)
                self.retry_video_ids = self._session_retryable_ids()
            except (OSError, ValueError, TypeError):
                summary = DownloadSummary(0, 0, 0, 0, 0)
                self.retry_video_ids = set()
            self._enable_results()
            if self.retry_video_ids and self.last_config is not None:
                self.auto_retry_attempt += 1
                self.retry_failed_button.configure(state="disabled")
                self._append_log(
                    f"Tự động tải lại lần {self.auto_retry_attempt}: còn {len(self.retry_video_ids)} mục lỗi.\n"
                )
                self.status_var.set(
                    f"Đang tự động tải lại lần {self.auto_retry_attempt}: còn {len(self.retry_video_ids)} mục lỗi..."
                )
                self._schedule_auto_retry(self.last_config, set(self.retry_video_ids))
                return
            self.status_var.set(
                "Đã hoàn tất — "
                f"Tổng {summary.total} | Tải {summary.downloaded} | Ghi đè {summary.overwritten} | "
                f"Bỏ qua {summary.skipped} | Lỗi {summary.failed}"
            )
            self.retry_failed_button.configure(state="disabled")
            if not self.closing:
                self._show_completion_popup(summary.text)
        elif outcome == "cancelled":
            if detail:
                self.report_path = Path(detail)
                self.result_directory = directory
                self._enable_results()
            self.status_var.set("Đã hủy an toàn; báo cáo dở dang đã được lưu")
            if not self.closing:
                messagebox.showinfo("Đã hủy", "Chạy lại cùng cấu hình để tiếp tục.", parent=self.root)
        else:
            self.status_var.set("Thất bại")
            self._append_log(f"Lỗi: {detail}\n")
            if not self.closing:
                messagebox.showerror("Tải phụ đề thất bại", str(detail), parent=self.root)
        if self.closing:
            self.root.destroy()

    def _set_running(self, running: bool) -> None:
        self.start_button.configure(state="disabled" if running else "normal")
        self.cancel_button.configure(state="normal" if running else "disabled")
        if running:
            self.open_folder_button.configure(state="disabled")
            self.open_report_button.configure(state="disabled")
            self.retry_failed_button.configure(state="disabled")

    def _enable_results(self) -> None:
        self.open_folder_button.configure(state="normal")
        self.open_report_button.configure(state="normal")

    def _cancel(self) -> None:
        if self.cancel_event and not self.cancel_event.is_set():
            self.cancel_event.set()
            self.cancel_button.configure(state="disabled")
            self.status_var.set("Đang hủy sau khi yêu cầu hiện tại hoàn tất...")

    def _focus_window(self) -> None:
        """Bring this downloader window to the foreground."""
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.focus_force()
            self.root.after(350, lambda: self.root.attributes("-topmost", False))
        except tk.TclError:
            pass

    def _show_completion_popup(self, message: str) -> None:
        """Show the persistent completion popup until the user acts on it."""
        self._dismiss_completion_popup()
        try:
            popup = ctk.CTkToplevel(self.root, fg_color=BG)
            self.completion_popup = popup
            popup.title("Tải phụ đề đã hoàn tất")
            popup.overrideredirect(True)
            popup.resizable(False, False)
            popup.attributes("-topmost", True)

            width, height = 440, 176
            x = max(12, popup.winfo_screenwidth() - width - 24)
            y = max(12, popup.winfo_screenheight() - height - 72)
            popup.geometry(f"{width}x{height}+{x}+{y}")

            card = ctk.CTkFrame(
                popup,
                fg_color=ELEVATED,
                border_width=1,
                border_color=ACCENT,
                corner_radius=12,
            )
            card.pack(fill="both", expand=True, padx=2, pady=2)
            card.grid_columnconfigure(0, weight=1)
            title = ctk.CTkLabel(
                card,
                text="Tải phụ đề đã hoàn tất",
                text_color=TEXT,
                font=ctk.CTkFont(size=17, weight="bold"),
            )
            title.grid(row=0, column=0, sticky="w", padx=(18, 8), pady=(15, 4))
            close_button = ctk.CTkButton(
                card,
                text="×",
                width=32,
                height=28,
                fg_color="transparent",
                hover_color=BORDER,
                command=self._dismiss_completion_popup,
            )
            close_button.grid(row=0, column=1, padx=(0, 10), pady=(10, 0))
            detail = ctk.CTkLabel(
                card,
                text=message,
                text_color=MUTED,
                justify="left",
                anchor="w",
                wraplength=398,
            )
            detail.grid(row=1, column=0, columnspan=2, sticky="ew", padx=18, pady=(0, 10))
            open_button = ctk.CTkButton(
                card,
                text="Mở cửa sổ tải",
                command=self._activate_completion_popup,
                fg_color=ACCENT,
                hover_color=ACCENT_HOVER,
            )
            open_button.grid(row=2, column=0, columnspan=2, sticky="ew", padx=18, pady=(0, 15))
            for widget in (popup, card, title, detail):
                widget.bind("<Button-1>", lambda _event: self._activate_completion_popup())
            popup.lift()
        except tk.TclError:
            self.completion_popup = None

    def _activate_completion_popup(self) -> None:
        self._dismiss_completion_popup()
        self._focus_window()

    def _dismiss_completion_popup(self) -> None:
        popup = getattr(self, "completion_popup", None)
        self.completion_popup = None
        if popup is not None:
            try:
                popup.destroy()
            except tk.TclError:
                pass

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _append_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    @staticmethod
    def _open_path(path: Path) -> None:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def _open_folder(self) -> None:
        if self.result_directory:
            try:
                self._open_path(self.result_directory)
            except OSError as exc:
                messagebox.showerror("Mở thư mục", str(exc), parent=self.root)

    def _open_report(self) -> None:
        if self.report_path:
            try:
                self._open_path(self.report_path)
            except OSError as exc:
                messagebox.showerror("Mở báo cáo", str(exc), parent=self.root)

    def _on_close(self) -> None:
        """Cancel outstanding work and close without waiting on network I/O.

        A caption request may be inside its configured timeout/retry interval.
        Waiting for the worker to report cancellation makes the window appear
        frozen.  The worker is deliberately a daemon and receives this event,
        so destroying the GUI immediately is safe and lets the packaged app
        exit promptly.
        """
        if self.closing:
            return
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("Đang tải phụ đề", "Hủy tải và đóng ứng dụng?", parent=self.root):
                return
        self.closing = True
        window_token = getattr(self, "window_token", None)
        if window_token:
            unregister_window_target(window_token)
        if self.auto_retry_wait_event is not None:
            self.auto_retry_wait_event.set()
        if self.cancel_event is not None:
            self.cancel_event.set()
        self._dismiss_completion_popup()
        self._close_child_windows()
        WINDOW_TRAY.unregister(self)
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def _close_immediately(self) -> None:
        """Close this window from the explicit tray-menu command."""
        if self.closing:
            return
        self.closing = True
        if self.auto_retry_wait_event is not None:
            self.auto_retry_wait_event.set()
        if self.cancel_event is not None:
            self.cancel_event.set()
        self._dismiss_completion_popup()
        self._close_child_windows()
        unregister_window_target(self.window_token)
        WINDOW_TRAY.unregister(self)
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def _close_child_windows(self) -> None:
        """Unregister children before Tk destroys their owning top-level window."""
        child_windows = getattr(self, "child_windows", [])
        for child in tuple(child_windows):
            child._close_immediately()
        child_windows.clear()


def main() -> int:
    if not acquire_primary_instance():
        # A pinned-taskbar click must focus the running app, not create another
        # process and another notification-area icon.
        for _ in range(30):
            if activate_latest_window():
                break
            time.sleep(0.1)
        return 0
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    try:
        root = ctk.CTk(fg_color=BG)
    except tk.TclError as exc:
        print(f"Không thể khởi động giao diện: {exc}", file=sys.stderr)
        release_primary_instance()
        return 2
    SubtitleDownloaderApp(root)
    try:
        root.mainloop()
    finally:
        WINDOW_TRAY.shutdown()
        release_primary_instance()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
