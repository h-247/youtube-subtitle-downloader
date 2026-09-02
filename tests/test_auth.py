import json
from pathlib import Path

import pytest

from youtube_downloader.auth import load_cookie_file, parse_cookie_export
from youtube_downloader.exceptions import CookieError


COOKIE = {"name": "SID", "value": "super-secret", "domain": ".youtube.com", "path": "/"}


def test_cookie_editor_array():
    rows = parse_cookie_export(json.dumps([COOKIE]), now=1)
    assert rows[0]["name"] == "SID"


def test_wrapped_cookies_object():
    rows = parse_cookie_export(json.dumps({"cookies": [COOKIE]}), now=1)
    assert len(rows) == 1


def test_markdown_fenced_json():
    rows = parse_cookie_export(f"```json\n{json.dumps([COOKIE])}\n```", now=1)
    assert rows[0]["domain"] == ".youtube.com"


def test_unrelated_domains_are_rejected_without_secret():
    secret = "never-print-this-value"
    with pytest.raises(CookieError) as caught:
        parse_cookie_export(json.dumps([{"name": "x", "value": secret, "domain": ".example.com"}]), now=1)
    assert secret not in str(caught.value)


def test_missing_domain_is_not_assumed_to_be_youtube():
    with pytest.raises(CookieError):
        parse_cookie_export(json.dumps([{"name": "SID", "value": "secret"}]), now=1)


def test_expired_cookies_are_rejected_without_secret():
    secret = "expired-secret"
    with pytest.raises(CookieError) as caught:
        parse_cookie_export(json.dumps([{**COOKIE, "value": secret, "expirationDate": 10}]), now=11)
    assert secret not in str(caught.value)


def test_malformed_json_does_not_echo_input():
    raw = "{secret-value"
    with pytest.raises(CookieError) as caught:
        parse_cookie_export(raw)
    assert "secret-value" not in str(caught.value)


def test_netscape_cookie_file(tmp_path: Path):
    path = tmp_path / "youtube.cookies.txt"
    path.write_text(
        "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\tsecret\n",
        encoding="utf-8",
    )
    rows = load_cookie_file(path, now=1)
    assert rows[0]["name"] == "SID" and rows[0]["value"] == "secret"


def test_empty_export_rejected():
    with pytest.raises(CookieError):
        parse_cookie_export("[]")
