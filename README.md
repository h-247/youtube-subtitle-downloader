# YouTube English Subtitle Downloader

A Python 3.11+ desktop application and CLI that downloads the best available **English subtitle only** from YouTube videos, Shorts, and playlists. It never downloads video, audio, thumbnails, livestream recordings, or media segments.

The application uses the structured [`yt-dlp` Python API](https://github.com/yt-dlp/yt-dlp#embedding-yt-dlp) for metadata and caption discovery. It calls `extract_info(..., download=False)` with media downloading disabled, then retrieves only the single selected caption resource.

## Legal and authorization boundary

Use this tool only for public content and content you are authorized to view in your own browser session. It does not bypass authentication, geographic restrictions, bot protection, CAPTCHAs, or DRM. It does not request a username or password. YouTube availability and terms still apply.

## Requirements

- Python 3.11 or newer
- Windows, macOS, or Linux with Tk support
- Internet access to YouTube

## Installation

```powershell
git clone <your-repository-url> youtube-subtitle-downloader
cd youtube-subtitle-downloader
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

On macOS/Linux, activate the environment with `source .venv/bin/activate`.

## Desktop GUI

Run:

```powershell
python gui.py
```

On Windows you can also double-click `run_gui.bat`; it uses `.venv\Scripts\python.exe` when present.

### Windows release executable

Run `build_exe.bat` to create `C:\Users\toanv\OneDrive\Desktop\YouTubeSubtitleDownloader.exe`. The one-file
release embeds `assets/app-icon.ico`, runs without a Terminal window, and is intentionally emitted to this
space-free path. Pin this exact file to the Taskbar after the first launch.

The dark-mode GUI provides:

- video or playlist URL input;
- a Vietnamese interface with the in-memory **Dán cookie JSON** tab selected by default;
- optional cookie file or in-memory pasted Cookie-Editor JSON;
- output directory, English locale, VTT/SRT/TXT format, exclusive skip/overwrite mode, delay, timeout, and retries;
- responsive progress, current title, activity log, cancellation, result/report shortcuts, and **Chỉ tải lại mục lỗi**;
- a Vietnamese completion summary showing total videos, downloaded, overwritten, skipped, and unsuccessful counts;
- a persistent GUI completion popup that stays visible until clicked and focuses its corresponding downloader window.

The application identifies a video or playlist directly from its URL. A URL containing `list=...` is processed as a playlist; a normal video or Shorts URL is processed as one video. Channel downloads are intentionally unsupported.

**Watch Later:** use `https://www.youtube.com/playlist?list=WL` with fresh cookies from the same authorized browser session. The application discovers the saved rows first and resolves each video independently, so an unavailable item cannot make the entire Watch Later list appear unavailable.

## CLI

Required option: `--url`.

```powershell
python main.py `
  --url "https://www.youtube.com/watch?v=VIDEO_ID" `
  --output ".\output" `
  --language en `
  --format vtt
```

Playlist example:

```powershell
python main.py `
  --url "https://www.youtube.com/playlist?list=PLAYLIST_ID" `
  --format srt `
  --delay 1 `
  --timeout 30 `
  --retries 3
```

Authorized-cookie example:

```powershell
python main.py `
  --url "YOUTUBE_URL" `
  --cookies ".\youtube.cookies.json" `
  --format txt
```

Options:

```text
--url URL                 required YouTube video, Shorts, or playlist URL
--cookies PATH            Netscape or Cookie-Editor file; raw JSON is never a CLI option
--output PATH             default: ./output
--language LOCALE         default: en; examples: en-US, en-GB, en-orig
--format vtt|srt|txt      default: vtt
--skip-existing           default; skip a valid existing subtitle
--overwrite               explicitly replace an existing subtitle
--delay SECONDS           non-negative delay between videos
--timeout SECONDS         HTTP timeout
--retries COUNT           bounded retries, 0-10
--verbose                 additional safe progress
```

Press Ctrl+C once to request safe cancellation after the active request. A partial report is saved and the same command can resume later.

## Cookies and private/age-restricted content

Cookies are optional for public videos. For content available only in your own browser session:

1. Sign in to YouTube normally in your browser and confirm that the video opens there.
2. Export only current YouTube/Google cookies using a trusted Cookie-Editor-compatible tool, as either JSON or Netscape format.
3. Select the file in the GUI, paste the JSON into the GUI, or pass the file with `--cookies PATH`.

The paste box accepts a Cookie-Editor array, an object with a `cookies` array, or the same JSON inside a Markdown code fence. Pasted JSON remains in application memory and is never written to disk. The CLI intentionally has no raw-cookie argument because command-line values can leak into shell history.

> **Cookies contain sensitive authenticated session information. Do not commit them to Git. Do not share them. Delete them when no longer needed.**

Cookie values, signed caption URLs, authentication headers, and bearer tokens are never written to logs or `download_report.json`. Cookie entries unrelated to YouTube/Google and expired cookies are rejected. Authentication material is restricted to approved YouTube/Google hosts.

## Caption selection

For each accessible video:

1. Prefer manually created English subtitles.
2. If none exist, use automatically generated English captions.
3. Within the same type, prefer the requested locale, then `en`, `en-US`, `en-GB`, `en-orig`, and other English variants.
4. For a selected language track, prefer `vtt`, `srv3`, `srv2`, `srv1`, `ttml`, then `json3`.

Manual always wins across locales; manual `en-GB` beats automatic `en`. Visible names such as `English`, `English (US)`, `English (UK)`, and `English [Auto]` are recognized alongside language codes.

## Output formats

- **VTT** preserves valid original WebVTT when possible and requires timed cues.
- **SRT** creates numbered cues, comma timestamps, and removes unsupported WebVTT-only cue tags.
- **TXT** removes timestamps, metadata, and tags; decodes HTML entities; and removes consecutive duplicate lines.

Malformed, empty, non-UTF-8, and unexpectedly large responses are rejected.

## Output layout

Playlist:

```text
output/
└── Playlist Name/
    ├── First Video.vtt
    ├── Second Video.vtt
    └── download_report.json
```

Single video:

```text
output/
└── Video Title/
    ├── Video Title.vtt
    └── download_report.json
```

Names are sanitized for Windows, macOS, and Linux, including reserved Windows names, trailing dots/spaces, invalid characters, duplicates, Unicode, and long paths. Playlist ordering is preserved.

## Resume and reports

Valid existing subtitles are skipped by default. Empty or malformed files are retrieved again. Every subtitle and report is written to a `.part` file and atomically replaced, so interruption does not leave a completed-looking corrupt file.

Subtitle filenames use the video title and selected output extension, for example `Mình nói được tiếng Anh ngay sau khi biết cách học NÀY.vtt`. Caption language stays out of the filename. If a later playlist item has the identical title, the application adds its playlist position in parentheses to avoid replacing the first file.

The GUI offers one exclusive resume choice: **Bỏ qua tệp có sẵn** (default) or **Ghi đè tệp có sẵn**. It can open additional in-process windows for parallel jobs; a selected cookie file or pasted cookie JSON is copied only in memory to the new window and is never passed through command-line arguments or written to disk.

For VTT output, the corresponding YouTube link is stored in a WebVTT `NOTE` block immediately after the required `WEBVTT` header. The link is standalone metadata, is not shown as a subtitle cue, and keeps the VTT valid.

`download_report.json` is updated after every video and records source information, completion/cancellation status, statistics, and safe per-video outcomes. An unavailable or malformed entry does not stop the rest of a playlist. Duplicate video IDs are recorded and skipped. When a run has unsuccessful videos, the GUI automatically retries only those video IDs using the same URL and in-memory configuration, with a five-second pause between retry cycles. It continues until all retryable items complete or the user presses **Hủy**. The latest retry details are written to `download_report_retry.json` without replacing the original report.

## Troubleshooting

- **Sign in / confirm you are not a bot:** Open YouTube normally, complete any manual check yourself, export fresh cookies, and retry. The application does not automate or evade these checks.
- **Private or organization-managed video:** Confirm the same browser session is authorized and export current cookies.
- **Expired cookies:** Delete the old export, sign in again, and create a fresh export.
- **HTTP 429:** Stop and wait before retrying. Increasing retries is not a rate-limit bypass.
- **Unavailable video:** It is recorded in the report and playlist processing continues.
- **Every Watch Later item is unavailable:** Refresh the browser session, export fresh YouTube cookies, and retry. The application now keeps each discovered Watch Later row and reports the specific per-video failure instead of collapsing the entire list.
- **No English subtitle:** The video has no recognized manual or automatic English track.
- **Long output path:** Choose a shorter output root such as `C:\Subs`.
- **GUI does not open on Linux:** Install the platform package that provides Tkinter, then retry.

## Development and tests

Unit tests use fixtures and doubles and never contact YouTube:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m compileall -q .
```

The test suite covers caption priority, playlist isolation/order, filename portability, conversions, Cookie-Editor/Netscape parsing, secret-safe errors, resume/atomic behavior, queue-based GUI events, cancellation, partial reports, and GUI construction.

## Limitations

- English subtitles only.
- No full-channel downloads.
- No DRM, CAPTCHA, geographic restriction, or bot-protection bypass.
- YouTube and `yt-dlp` behavior can change; keep `yt-dlp` within the compatible range in `requirements.txt` and retest after upgrades.
- Availability may differ by account, region, age, organization, and YouTube policy.
