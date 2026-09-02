import pytest

from youtube_downloader.converter import convert_subtitle, parse_cues
from youtube_downloader.exceptions import SubtitleError


VTT = """WEBVTT

00:00:01.000 --> 00:00:02.500
<c.green>Hello &amp; welcome</c>

00:00:02.500 --> 00:00:04.000 align:start
Hello again
"""


def test_valid_vtt_is_preserved():
    assert convert_subtitle(VTT, "vtt", "vtt") == VTT


def test_vtt_includes_video_link_in_first_metadata_block():
    result = convert_subtitle(
        VTT,
        "vtt",
        "vtt",
        source_url="https://www.youtube.com/watch?v=abc",
    )
    assert result.startswith("WEBVTT\n\nNOTE\nhttps://www.youtube.com/watch?v=abc\n\n")
    assert parse_cues(result)[0].lines == ["<c.green>Hello &amp; welcome</c>"]


def test_vtt_to_srt_numbers_and_timestamps():
    result = convert_subtitle(VTT, "vtt", "srt")
    assert "1\n00:00:01,000 --> 00:00:02,500" in result
    assert "<c.green>" not in result


def test_vtt_to_txt_removes_metadata_and_decodes_html():
    result = convert_subtitle(VTT, "vtt", "txt")
    assert result == "Hello & welcome\nHello again\n"
    assert "-->" not in result


def test_duplicate_consecutive_lines_removed():
    vtt = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nSame\n\n00:00:02.000 --> 00:00:03.000\nSame\n"
    assert convert_subtitle(vtt, "vtt", "txt") == "Same\n"


def test_malformed_caption_rejected():
    with pytest.raises(SubtitleError):
        convert_subtitle("not captions", "vtt", "vtt")


def test_srv_xml_conversion():
    xml = '<transcript><text start="1.5" dur="2">Hello &amp;amp; bye</text></transcript>'
    result = convert_subtitle(xml, "srv1", "vtt")
    assert "00:00:01.500 --> 00:00:03.500" in result
    assert "Hello & bye" in result


def test_json3_conversion():
    raw = '{"events":[{"tStartMs":1000,"dDurationMs":1500,"segs":[{"utf8":"Hello"}]}]}'
    result = convert_subtitle(raw, "json3", "srt")
    assert "00:00:01,000 --> 00:00:02,500" in result and "Hello" in result


def test_srt_input_can_be_normalized_to_vtt():
    srt = "1\n00:00:01,000 --> 00:00:02,000\nHello\n"
    assert convert_subtitle(srt, "srt", "vtt").startswith("WEBVTT")


def test_parse_requires_timed_cues():
    with pytest.raises(SubtitleError):
        parse_cues("WEBVTT\n\nNOTE only")
