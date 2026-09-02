"""Safe in-memory handling for YouTube/Google browser cookies."""

from __future__ import annotations

import json
import time
from http.cookiejar import Cookie, CookieJar, LoadError, MozillaCookieJar
from pathlib import Path
from typing import Any, Iterable

import requests

from .exceptions import CookieError


MAX_COOKIE_JSON_BYTES = 5 * 1024 * 1024
_ALLOWED_COOKIE_DOMAINS = ("youtube.com", "youtu.be", "google.com", "googlevideo.com")


def is_google_cookie_domain(domain: str) -> bool:
    normalized = domain.strip().lstrip(".").lower()
    return any(normalized == allowed or normalized.endswith(f".{allowed}") for allowed in _ALLOWED_COOKIE_DOMAINS)


def is_safe_caption_host(host: str) -> bool:
    normalized = host.strip().rstrip(".").lower()
    return any(
        normalized == allowed or normalized.endswith(f".{allowed}")
        for allowed in ("youtube.com", "google.com", "googlevideo.com", "ytimg.com")
    )


def _strip_markdown_fence(raw: str) -> str:
    text = raw.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].lstrip().startswith("```"):
        lines.pop(0)
    if lines and lines[-1].strip().startswith("```"):
        lines.pop()
    return "\n".join(lines).strip()


def _cookie_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and isinstance(payload.get("cookies"), list):
        rows = payload["cookies"]
    else:
        raise CookieError("Cookie JSON phải là mảng Cookie-Editor hoặc đối tượng chứa mảng cookies.")
    if not rows:
        raise CookieError("Cookie JSON không chứa mục cookie nào.")
    if not all(isinstance(row, dict) for row in rows):
        raise CookieError("Mỗi mục cookie phải là một đối tượng JSON.")
    return rows


def parse_cookie_export(raw_json: str, *, now: float | None = None) -> list[dict[str, Any]]:
    """Parse Cookie-Editor JSON without echoing values or writing any data."""
    if len(raw_json.encode("utf-8", errors="ignore")) > MAX_COOKIE_JSON_BYTES:
        raise CookieError("Cookie JSON có kích thước lớn bất thường.")
    text = _strip_markdown_fence(raw_json).lstrip("\ufeff")
    if not text:
        raise CookieError("Cookie JSON đang trống.")
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise CookieError("Cookie JSON không hợp lệ. Hãy xuất lại bằng Cookie-Editor.") from exc

    current_time = time.time() if now is None else now
    valid: list[dict[str, Any]] = []
    expired_count = 0
    for index, row in enumerate(_cookie_rows(payload), start=1):
        name = row.get("name")
        value = row.get("value")
        if not isinstance(name, str) or not name.strip():
            raise CookieError(f"Mục cookie số {index} không có tên hợp lệ.")
        if not isinstance(value, str):
            raise CookieError(f"Mục cookie số {index} không có giá trị dạng chuỗi.")
        domain = row.get("domain")
        if not isinstance(domain, str) or not is_google_cookie_domain(domain):
            continue
        expires = row.get("expirationDate", row.get("expires"))
        if isinstance(expires, (int, float)) and expires > 0 and expires <= current_time:
            expired_count += 1
            continue
        clean_row = dict(row)
        clean_row["domain"] = domain
        valid.append(clean_row)

    if not valid:
        detail = " Tất cả cookie phù hợp đều đã hết hạn." if expired_count else ""
        raise CookieError(f"Bản xuất không chứa cookie YouTube hoặc Google còn hiệu lực.{detail}")
    return valid


def _row_from_cookie(cookie: Cookie) -> dict[str, Any]:
    return {
        "name": cookie.name,
        "value": cookie.value,
        "domain": cookie.domain,
        "path": cookie.path,
        "secure": cookie.secure,
        "expires": cookie.expires,
        "httpOnly": "HttpOnly" in cookie._rest,
    }


def load_cookie_file(path: Path, *, now: float | None = None) -> list[dict[str, Any]]:
    """Load either Cookie-Editor JSON or a Netscape cookie file."""
    if not path.is_file():
        raise CookieError(f"Tệp cookie không tồn tại: {path}")
    if path.stat().st_size > MAX_COOKIE_JSON_BYTES:
        raise CookieError("Tệp cookie có kích thước lớn bất thường.")
    try:
        prefix = path.read_text(encoding="utf-8-sig", errors="strict")
    except (OSError, UnicodeError) as exc:
        raise CookieError(f"Không thể đọc tệp cookie dưới dạng văn bản: {path}") from exc
    if prefix.lstrip().startswith(("[", "{")):
        return parse_cookie_export(prefix, now=now)

    jar = MozillaCookieJar(str(path))
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
    except (LoadError, OSError) as exc:
        raise CookieError("Tệp cookie không phải JSON Cookie-Editor hoặc định dạng Netscape hợp lệ.") from exc
    current_time = time.time() if now is None else now
    rows = [
        _row_from_cookie(cookie)
        for cookie in jar
        if is_google_cookie_domain(cookie.domain)
        and (cookie.expires is None or cookie.expires <= 0 or cookie.expires > current_time)
    ]
    if not rows:
        raise CookieError("Tệp không chứa cookie YouTube hoặc Google còn hiệu lực.")
    return rows


def cookie_from_row(row: dict[str, Any]) -> Cookie:
    domain = str(row.get("domain") or ".youtube.com")
    expires_value = row.get("expirationDate", row.get("expires"))
    expires = int(expires_value) if isinstance(expires_value, (int, float)) and expires_value > 0 else None
    rest = {"HttpOnly": None} if row.get("httpOnly") else {}
    return Cookie(
        version=0,
        name=str(row["name"]),
        value=str(row["value"]),
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=domain.startswith("."),
        path=str(row.get("path") or "/"),
        path_specified=True,
        secure=bool(row.get("secure", True)),
        expires=expires,
        discard=expires is None,
        comment=None,
        comment_url=None,
        rest=rest,
        rfc2109=False,
    )


def install_cookies(jar: CookieJar, rows: Iterable[dict[str, Any]]) -> None:
    for row in rows:
        jar.set_cookie(cookie_from_row(row))


def build_requests_session(rows: Iterable[dict[str, Any]]) -> requests.Session:
    session = requests.Session()
    install_cookies(session.cookies, rows)
    session.headers.update({
        "Accept": "text/vtt,text/plain,application/json,application/xml;q=0.9,*/*;q=0.5",
        "User-Agent": "Mozilla/5.0 YouTubeSubtitleDownloader/1.0",
    })
    return session
