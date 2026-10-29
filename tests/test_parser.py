"""Tests for the page reader that need no HTTP layer."""

import pytest

from custom_components.hrvatska_posta.parser import (
    HrvatskaPostaParseError,
    clean_text,
    parse_page,
)

from . import payloads as p

CODE = p.ACTIVE_CODE


def test_clean_text_collapses_whitespace_and_nbsp():
    assert clean_text(" a\xa0 b \r\n c ") == "a b c"


def test_rows_keep_page_order_and_columns():
    parsed = parse_page(p.page(CODE, p.LOCKER_ROWS), CODE)
    assert [r["time"] for r in parsed["rows"]] == [r["time"] for r in p.LOCKER_ROWS]
    assert parsed["rows"][3] == {
        "date": "22.04.2026",
        "time": "14:20:00",
        "wording": p.IN_LOCKER,
        "location": "21001 LOCKER",
    }


def test_weekday_span_is_not_the_time():
    parsed = parse_page(p.page(CODE, p.HOME_ROWS), CODE)
    assert all(r["time"] != "Ponedjeljak" for r in parsed["rows"])


def test_only_the_trace_block_is_read():
    html = p.page(CODE, p.HOME_ROWS).replace(
        "<footer>", '<span class="Text-Style-4">Masa</span><span>9 g</span><footer>'
    )
    assert parse_page(html, CODE)["weight"] == "76 g"


def test_scripts_are_not_visible_text():
    html = p.no_data_page().replace(
        "</body>", f"<script>var m = '{p.BAD_FORMAT_NOTICE}';</script></body>"
    )
    assert parse_page(html, CODE) is None


def test_unclosed_and_stray_tags_do_not_break_reading():
    html = p.page(CODE, p.HOME_ROWS).replace("<footer>", "</em><p>unclosed<footer>")
    assert len(parse_page(html, CODE)["rows"]) == len(p.HOME_ROWS)


def test_page_without_trace_block_raises():
    html = p.page(CODE, p.HOME_ROWS).replace("track__n__trace", "other")
    with pytest.raises(HrvatskaPostaParseError):
        parse_page(html, CODE)


def test_only_logo_images_are_recorded():
    html = p.page(CODE, p.HOME_ROWS).replace(
        "<footer>", '<img src="/assets/icons/events-circle-checked.svg"><footer>'
    )
    html = html.replace(
        '<div class="event__status__item">',
        '<img src="/assets/icons/events-circle-checked.svg" />'
        '<div class="event__status__item">',
    )
    assert parse_page(html, CODE)["logos"] == ["hp-full-logo.svg"]
