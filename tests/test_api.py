"""Tests for the Hrvatska pošta API client."""

import logging
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.hrvatska_posta import api as api_module
from custom_components.hrvatska_posta.api import (
    HrvatskaPostaApiClient,
    HrvatskaPostaApiError,
)

from . import payloads as p

CODE = p.ACTIVE_CODE


@pytest.fixture(autouse=True)
def _reset_warnings():
    api_module._warned.clear()


def _session(status: int = 200, body: str | bytes = "", headers=None) -> MagicMock:
    response = AsyncMock()
    response.status = status
    response.headers = headers or {}
    raw = body.encode() if isinstance(body, str) else body

    async def iter_chunked(size):
        # Small slices, like a real stream, so reassembly is exercised.
        for start in range(0, len(raw), 1000):
            yield raw[start : start + 1000]

    response.content.iter_chunked = iter_chunked
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=ctx)
    return session


async def _get(body, code=CODE, **kwargs):
    return await HrvatskaPostaApiClient(_session(body=body, **kwargs)).async_get_parcel(
        code
    )


async def test_found_page_is_parsed():
    session = _session(body=p.page(CODE, p.HOME_ROWS))
    parcel = await HrvatskaPostaApiClient(session).async_get_parcel(CODE.lower())

    assert parcel["trackingNumber"] == CODE
    assert parcel["weightGrams"] == 76
    assert parcel["logos"] == ["hp-full-logo.svg"]
    assert [r["wording"] for r in parcel["rows"]] == [r["wording"] for r in p.HOME_ROWS]
    assert parcel["rows"][1]["location"] == "10000 OFFICE"
    assert parcel["rows"][0]["location"] is None


async def test_request_is_croatian_page_without_redirects():
    session = _session(body=p.page(CODE, p.HOME_ROWS))
    await HrvatskaPostaApiClient(session).async_get_parcel(CODE)
    url = session.get.call_args[0][0]
    assert url == f"https://posiljka.posta.hr/hr/tracking/trackingdata?barcode={CODE}"
    assert session.get.call_args.kwargs["allow_redirects"] is False
    assert session.get.call_args.kwargs["headers"] == {"Accept": "text/html"}


async def test_mobile_duplicate_is_ignored():
    parcel = await _get(p.page(CODE, p.HOME_ROWS))
    assert len(parcel["rows"]) == len(p.HOME_ROWS)
    assert "MOBILE-DUPLICATE" not in str(parcel)


async def test_wording_is_read_whatever_its_span_class():
    parcel = await _get(p.page(CODE, p.HOME_ROWS))
    assert all(r["wording"] for r in parcel["rows"])


async def test_zero_weight_page():
    parcel = await _get(p.page(CODE, p.HOME_ROWS, weight="0 g"))
    assert parcel["weightGrams"] == 0


async def test_missing_weight_is_none():
    parcel = await _get(p.page(CODE, p.HOME_ROWS, weight=None))
    assert parcel["weightGrams"] is None


async def test_odd_weight_warns_once_and_is_none(caplog):
    with caplog.at_level(logging.WARNING):
        first = await _get(p.page(CODE, p.HOME_ROWS, weight="1,2 kg"))
        await _get(p.page(CODE, p.HOME_ROWS, weight="1,2 kg"))
    assert first["weightGrams"] is None
    assert caplog.text.count("weight") == 1
    assert "1,2" not in caplog.text


async def test_no_data_page_is_none():
    assert await _get(p.no_data_page()) is None


async def test_bad_format_page_is_a_schema_error():
    with pytest.raises(HrvatskaPostaApiError):
        await _get(p.bad_format_page())


async def test_attribute_alone_is_not_a_bad_format_notice():
    """Every page carries the message as an attribute; only visible text counts."""
    assert await _get(p.no_data_page()) is None


async def test_number_mismatch_is_inconclusive():
    with pytest.raises(HrvatskaPostaApiError):
        await _get(p.page("RR999999999HR", p.HOME_ROWS))


@pytest.mark.parametrize(
    "body",
    [
        "<html><head><title>Error 404</title></head><body>gone</body></html>",
        "<html><head><title>Hrvatska Pošta - Nema podataka</title></head><body>?</body></html>",
        "<html><head><title>Hrvatska Pošta - Tracking</title></head><body>x</body></html>",
        p.page(CODE, []),
    ],
)
async def test_unrecognised_pages_raise(body, caplog):
    with caplog.at_level(logging.WARNING), pytest.raises(HrvatskaPostaApiError):
        await _get(body)
    assert "issues/new?template=unrecognised_status.yml" in caplog.text


async def test_header_without_number_raises():
    body = p.page(CODE, p.HOME_ROWS).replace(
        "Broj pošiljke</span>", "Nešto drugo</span>"
    )
    with pytest.raises(HrvatskaPostaApiError):
        await _get(body)


@pytest.mark.parametrize(
    "marker", ["Request Rejected", "Access Denied", "Just a moment", "cf-turnstile"]
)
async def test_bot_wall_is_a_transport_error(marker, caplog):
    with caplog.at_level(logging.WARNING), pytest.raises(HrvatskaPostaApiError):
        await _get(f"<html><head><title>x</title></head><body>{marker}</body></html>")
    assert "bot-protection" in caplog.text


async def test_empty_body_raises():
    with pytest.raises(HrvatskaPostaApiError):
        await _get("   ")


async def test_oversized_body_raises():
    with pytest.raises(HrvatskaPostaApiError):
        await _get(b"x" * (api_module.MAX_BODY_BYTES + 1))


async def test_unreadable_rows_are_dropped_with_one_warning(caplog):
    rows = [*p.HOME_ROWS, p.row("99.99.9999", "10:00:00", p.SENT)]
    with caplog.at_level(logging.WARNING):
        parcel = await _get(p.page(CODE, rows))
    assert len(parcel["rows"]) == len(p.HOME_ROWS)
    assert "could not be read" in caplog.text


async def test_all_rows_unreadable_raises():
    with pytest.raises(HrvatskaPostaApiError):
        await _get(p.page(CODE, [p.row("99.99.9999", "10:00:00", p.SENT)]))


@pytest.mark.parametrize("status", [301, 302, 404, 500, 503])
async def test_non_200_raises_with_status(status):
    client = HrvatskaPostaApiClient(_session(status))
    with pytest.raises(HrvatskaPostaApiError) as err:
        await client.async_get_parcel(CODE)
    assert err.value.status_code == status


async def test_429_carries_retry_after():
    client = HrvatskaPostaApiClient(_session(429, headers={"Retry-After": "120"}))
    with pytest.raises(HrvatskaPostaApiError) as err:
        await client.async_get_parcel(CODE)
    assert err.value.status_code == 429
    assert err.value.retry_after == 120.0


@pytest.mark.parametrize(
    "header", [{}, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}]
)
async def test_429_without_seconds_has_no_retry_after(header):
    client = HrvatskaPostaApiClient(_session(429, headers=header))
    with pytest.raises(HrvatskaPostaApiError) as err:
        await client.async_get_parcel(CODE)
    assert err.value.retry_after is None


async def test_network_error_propagates():
    session = MagicMock()
    session.get = MagicMock(side_effect=aiohttp.ClientError("boom"))
    with pytest.raises(aiohttp.ClientError):
        await HrvatskaPostaApiClient(session).async_get_parcel(CODE)
