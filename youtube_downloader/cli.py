"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from pathlib import Path

from .auth import load_cookie_file
from .client import YouTubeClient, validate_youtube_url
from .downloader import SubtitleDownloader
from .exceptions import DownloadCancelled, YouTubeSubtitleError
from .models import DownloadOptions


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="youtube-subtitle-downloader",
        description="Download the best available English subtitle from YouTube (never video or audio).",
    )
    parser.add_argument("--url", required=True, help="YouTube video, Shorts, or playlist URL")
    parser.add_argument("--cookies", type=Path, help="Netscape or Cookie-Editor cookie file")
    parser.add_argument("--output", type=Path, default=Path("output"), help="Output root (default: ./output)")
    parser.add_argument("--language", default="en", help="Preferred English locale (default: en)")
    parser.add_argument("--format", dest="output_format", choices=("vtt", "srt", "txt"), default="vtt")
    resume = parser.add_mutually_exclusive_group()
    resume.add_argument(
        "--skip-existing",
        dest="resume_mode",
        action="store_const",
        const="skip",
        help="Skip valid existing subtitles (default)",
    )
    resume.add_argument(
        "--overwrite",
        dest="resume_mode",
        action="store_const",
        const="overwrite",
        help="Replace existing subtitle files",
    )
    parser.set_defaults(resume_mode="skip")
    parser.add_argument("--delay", type=float, default=0.0, help="Seconds between videos")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout seconds")
    parser.add_argument("--retries", type=int, default=3, help="Bounded metadata/HTTP retries")
    parser.add_argument("--verbose", action="store_true", help="Show additional safe progress")
    return parser


def options_from_args(args: argparse.Namespace) -> DownloadOptions:
    validate_youtube_url(args.url)
    language = args.language.strip()
    if language.lower().replace("_", "-").split("-", 1)[0] not in {"en", "english"}:
        raise ValueError("--language must be an English locale such as en, en-US, or en-GB.")
    if args.delay < 0:
        raise ValueError("--delay cannot be negative.")
    if args.timeout <= 0:
        raise ValueError("--timeout must be greater than zero.")
    if not 0 <= args.retries <= 10:
        raise ValueError("--retries must be between 0 and 10.")
    return DownloadOptions(
        output=args.output,
        language=language,
        output_format=args.output_format,
        skip_existing=args.resume_mode == "skip",
        overwrite=args.resume_mode == "overwrite",
        delay=args.delay,
        timeout=args.timeout,
        retries=args.retries,
        verbose=args.verbose,
    )


def _report_failed(path: Path) -> int:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        return int(report.get("statistics", {}).get("failed", 0))
    except (OSError, ValueError, TypeError):
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        options = options_from_args(args)
        cookie_rows = load_cookie_file(args.cookies) if args.cookies else []
    except (ValueError, YouTubeSubtitleError, OSError) as exc:
        parser.error(str(exc))

    cancel_event = threading.Event()
    interrupt_count = 0

    def request_cancel(_signum: int, _frame: object) -> None:
        nonlocal interrupt_count
        interrupt_count += 1
        if interrupt_count > 1:
            raise KeyboardInterrupt
        cancel_event.set()
        print("\nCancellation requested; finishing the active request safely...", file=sys.stderr)

    previous_handler = signal.signal(signal.SIGINT, request_cancel)
    try:
        with YouTubeClient(
            cookie_rows,
            timeout=options.timeout,
            retries=options.retries,
            verbose=options.verbose,
        ) as client:
            downloader = SubtitleDownloader(
                client,
                args.url,
                options,
                output=sys.stdout,
                cancel_event=cancel_event,
            )
            report_path = downloader.run()
        print(f"Report: {report_path}")
        return 1 if _report_failed(report_path) else 0
    except DownloadCancelled as exc:
        if exc.report_path:
            print(f"Cancelled. Partial report: {exc.report_path}", file=sys.stderr)
        else:
            print("Cancelled.", file=sys.stderr)
        return 130
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except YouTubeSubtitleError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    finally:
        signal.signal(signal.SIGINT, previous_handler)


if __name__ == "__main__":
    raise SystemExit(main())
