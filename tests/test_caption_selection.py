from youtube_downloader.captions import choose_best_english_caption


def item(ext="vtt", url="https://www.youtube.com/api/timedtext"):
    return {"ext": ext, "url": url}


def test_manual_beats_automatic_same_locale():
    result = choose_best_english_caption({"en": [item()]}, {"en": [item("json3")]})
    assert result and result.kind == "manual" and result.language == "en"


def test_manual_variant_beats_better_automatic_locale():
    result = choose_best_english_caption({"en-GB": [item()]}, {"en": [item()]})
    assert result and result.kind == "manual" and result.language == "en-gb"


def test_automatic_en_us_only():
    result = choose_best_english_caption({}, {"en-US": [item()]})
    assert result and result.kind == "automatic" and result.language == "en-us"


def test_visible_english_label_is_detected():
    result = choose_best_english_caption({"English": [item()]}, {"es": [item()]})
    assert result and result.language == "en"


def test_default_locale_order_is_deterministic():
    result = choose_best_english_caption({"en-US": [item()], "en": [item()]}, {})
    assert result and result.language == "en"


def test_requested_locale_is_preferred_within_same_kind():
    result = choose_best_english_caption({"en": [item()], "en-US": [item()]}, {}, "en-US")
    assert result and result.language == "en-us"


def test_no_english_returns_none():
    assert choose_best_english_caption({"es": [item()]}, {"fr": [item()]}) is None


def test_best_format_is_selected():
    result = choose_best_english_caption(
        {"en": [item("json3", "https://www.youtube.com/a"), item("vtt", "https://www.youtube.com/b")]},
        {},
    )
    assert result and result.extension == "vtt" and result.url.endswith("/b")


def test_track_fields_can_identify_english_when_key_cannot():
    result = choose_best_english_caption(
        {"track-1": [{"ext": "vtt", "url": "https://www.youtube.com/a", "name": "English [Auto]"}]},
        {},
    )
    assert result and result.language == "en"
