"""Validated conversion from YouTube caption formats to VTT, SRT, or text."""

from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import urlparse

from .exceptions import SubtitleError


MAX_CAPTION_BYTES = 10 * 1024 * 1024
_TIMING = re.compile(
    r"^(?P<start>(?:\d+:)?\d{2}:\d{2}[.,]\d{3})\s*-->\s*"
    r"(?P<end>(?:\d+:)?\d{2}:\d{2}[.,]\d{3})(?:\s+.*)?$"
)
_TAG = re.compile(r"<[^>]+>")
_VTT_ONLY_TAG = re.compile(r"</?(?:v|c|ruby|rt)(?:\.[^\s>]+)*(?:\s+[^>]*)?>", re.I)


@dataclass(slots=True)
class Cue:
    start: str
    end: str
    lines: list[str]


def _timestamp_from_ms(milliseconds: float) -> str:
    value = max(0, int(round(milliseconds)))
    hours, remainder = divmod(value, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def _normalize_timestamp(timestamp: str, separator: str) -> str:
    timestamp = timestamp.replace(",", ".")
    if len(timestamp.split(":", 1)) == 1:
        raise SubtitleError("Phụ đề chứa mốc thời gian không hợp lệ.")
    if len(timestamp.split(".", 1)[0].split(":")) == 2:
        timestamp = f"00:{timestamp}"
    if len(timestamp.split(".", 1)[0].split(":")) != 3:
        raise SubtitleError("Phụ đề chứa mốc thời gian không hợp lệ.")
    return timestamp.replace(".", separator)


def parse_cues(text: str) -> list[Cue]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    lines = normalized.split("\n")
    cues: list[Cue] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line or line == "WEBVTT" or line.startswith(("X-TIMESTAMP-MAP",)):
            index += 1
            continue
        if line.startswith(("NOTE", "STYLE", "REGION")):
            index += 1
            while index < len(lines) and lines[index].strip():
                index += 1
            continue
        timing_index = index
        if not _TIMING.match(line) and index + 1 < len(lines) and _TIMING.match(lines[index + 1].strip()):
            timing_index = index + 1
        timing = _TIMING.match(lines[timing_index].strip())
        if timing is None:
            index += 1
            continue
        index = timing_index + 1
        cue_lines: list[str] = []
        while index < len(lines) and lines[index].strip():
            cue_lines.append(lines[index].strip())
            index += 1
        if cue_lines:
            cues.append(Cue(timing.group("start"), timing.group("end"), cue_lines))
    if not cues:
        raise SubtitleError("Nội dung phụ đề không có cue thời gian hợp lệ.")
    return cues


def _seconds(value: str) -> float:
    value = value.strip()
    if value.endswith("ms"):
        return float(value[:-2]) / 1000
    if value.endswith("s"):
        return float(value[:-1])
    if ":" in value:
        parts = [float(part) for part in value.replace(",", ".").split(":")]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
    return float(value)


def _cues_to_vtt(cues: list[tuple[float, float, str]]) -> str:
    blocks = []
    for start, end, content in cues:
        cleaned = html.unescape(content).strip()
        if cleaned and end > start:
            blocks.append(f"{_timestamp_from_ms(start * 1000)} --> {_timestamp_from_ms(end * 1000)}\n{cleaned}")
    if not blocks:
        raise SubtitleError("Nội dung phụ đề không có cue thời gian hợp lệ.")
    return "WEBVTT\n\n" + "\n\n".join(blocks) + "\n"


def _xml_to_vtt(text: str) -> str:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise SubtitleError("Dữ liệu phụ đề XML không hợp lệ.") from exc
    cues: list[tuple[float, float, str]] = []
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1].lower()
        if tag not in {"text", "p"}:
            continue
        attrs = element.attrib
        try:
            if "start" in attrs:
                start = _seconds(attrs["start"])
                duration = _seconds(attrs.get("dur", "0"))
                end = start + duration
            elif "t" in attrs:
                start = float(attrs["t"]) / 1000
                end = start + float(attrs.get("d", "0")) / 1000
            elif "begin" in attrs:
                start = _seconds(attrs["begin"])
                end = _seconds(attrs["end"]) if "end" in attrs else start + _seconds(attrs.get("dur", "0"))
            else:
                continue
        except (ValueError, KeyError) as exc:
            raise SubtitleError("Phụ đề XML chứa dữ liệu thời gian không hợp lệ.") from exc
        content = "".join(element.itertext()).strip()
        cues.append((start, end, content))
    return _cues_to_vtt(cues)


def _json3_to_vtt(text: str) -> str:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SubtitleError("Dữ liệu phụ đề JSON3 không hợp lệ.") from exc
    cues: list[tuple[float, float, str]] = []
    for event in payload.get("events", []) if isinstance(payload, dict) else []:
        if not isinstance(event, dict) or not isinstance(event.get("segs"), list):
            continue
        start = float(event.get("tStartMs", 0)) / 1000
        duration = float(event.get("dDurationMs", 0)) / 1000
        content = "".join(str(segment.get("utf8", "")) for segment in event["segs"] if isinstance(segment, dict))
        cues.append((start, start + duration, content))
    return _cues_to_vtt(cues)


def source_to_vtt(text: str, source_extension: str) -> str:
    ext = source_extension.lower().lstrip(".")
    if ext == "json3":
        return _json3_to_vtt(text)
    if ext in {"srv1", "srv2", "srv3", "ttml", "xml"}:
        return _xml_to_vtt(text)
    if text.lstrip("\ufeff\r\n ").startswith("WEBVTT"):
        parse_cues(text)
        return text.lstrip("\ufeff")
    cues = parse_cues(text)
    blocks = [
        f"{_normalize_timestamp(cue.start, '.')} --> {_normalize_timestamp(cue.end, '.')}\n"
        + "\n".join(cue.lines)
        for cue in cues
    ]
    return "WEBVTT\n\n" + "\n\n".join(blocks) + "\n"


def to_srt(vtt_text: str) -> str:
    cues = parse_cues(vtt_text)
    blocks = []
    for number, cue in enumerate(cues, start=1):
        lines = [_VTT_ONLY_TAG.sub("", line) for line in cue.lines]
        blocks.append(
            f"{number}\n{_normalize_timestamp(cue.start, ',')} --> {_normalize_timestamp(cue.end, ',')}\n"
            + "\n".join(lines)
        )
    return "\n\n".join(blocks) + "\n"


def to_txt(vtt_text: str) -> str:
    cues = parse_cues(vtt_text)
    output: list[str] = []
    for cue in cues:
        for line in cue.lines:
            cleaned = html.unescape(_TAG.sub("", line)).strip()
            if cleaned and (not output or cleaned != output[-1]):
                output.append(cleaned)
    if not output:
        raise SubtitleError("Phụ đề không chứa văn bản có thể sử dụng.")
    return "\n".join(output) + "\n"


def add_source_url_note(vtt_text: str, source_url: str | None) -> str:
    """Add the video link as the first non-header WebVTT metadata line."""
    if not source_url or "\n" in source_url or "\r" in source_url:
        return vtt_text
    parsed = urlparse(source_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (
        host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")
    ):
        return vtt_text
    first_newline = vtt_text.find("\n")
    if first_newline < 0 or not vtt_text.startswith("WEBVTT"):
        return vtt_text
    header = vtt_text[:first_newline]
    body = vtt_text[first_newline + 1 :].lstrip("\n")
    return f"{header}\n\nNOTE\n{source_url}\n\n{body}"


def convert_subtitle(
    text: str,
    source_extension: str,
    output_format: str,
    *,
    source_url: str | None = None,
) -> str:
    if len(text.encode("utf-8", errors="ignore")) > MAX_CAPTION_BYTES:
        raise SubtitleError("Phản hồi phụ đề có kích thước lớn bất thường.")
    vtt = source_to_vtt(text, source_extension)
    if output_format == "vtt":
        return add_source_url_note(vtt, source_url)
    if output_format == "srt":
        return to_srt(vtt)
    if output_format == "txt":
        return to_txt(vtt)
    raise ValueError(f"Định dạng phụ đề không được hỗ trợ: {output_format}")


def appears_valid_subtitle(path: str, text: str) -> bool:
    if not text.strip():
        return False
    try:
        if path.lower().endswith(".txt"):
            return bool(text.strip()) and "-->" not in text
        parse_cues(text)
        return True
    except SubtitleError:
        return False
