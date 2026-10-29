"""Tests for the pure parcel-mapping helpers.

These need no Home Assistant instance — the whole point of keeping
``parcels.py`` free of I/O is that the carrier-specific mapping can be tested
as plain functions.
"""

from datetime import UTC, datetime, timedelta
import logging
from zoneinfo import ZoneInfo

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hrvatska_posta.const import (
    CAPABILITIES,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    KNOWN_CAPABILITIES,
    PENDING_CAPABILITIES,
    ParcelStatus,
)
from custom_components.hrvatska_posta.parcels import (
    apply_delivered_filter,
    build_history,
    format_dimensions,
    normalize_parcel,
    parse_clock,
    parse_iso,
    sort_parcels_by_ts,
    to_iso_timestamp,
    tracking_url,
    zone_for_location,
)

from . import payloads as p
from .payloads import active_sample, delivered_sample, parsed, pickup_sample, row

CODE = p.ACTIVE_CODE


def _normalize(rows, **kwargs):
    return normalize_parcel(parsed(CODE, rows), **kwargs)


# ---------------------------------------------------------------------------
# status map
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("wording", "expected"),
    [
        (p.ANNOUNCED, ParcelStatus.REGISTERED),
        (p.ACCEPTED, ParcelStatus.REGISTERED),
        (p.ARRIVED, ParcelStatus.IN_TRANSIT),
        (p.PREPARING, ParcelStatus.IN_TRANSIT),
        (p.SENT, ParcelStatus.IN_TRANSIT),
        ("Pošiljka je otpremljena prema unutrašnjem prometu", ParcelStatus.IN_TRANSIT),
        (p.REDIRECTED, ParcelStatus.IN_TRANSIT),
        (p.OUT_FOR_DELIVERY, ParcelStatus.OUT_FOR_DELIVERY),
        (p.ATTEMPT, ParcelStatus.PROBLEM),
        (p.NOT_DELIVERED, ParcelStatus.PROBLEM),
        (p.PICKUP_OFFICE, ParcelStatus.AT_PICKUP_POINT),
        (p.IN_LOCKER, ParcelStatus.AT_PICKUP_POINT),
        (p.DELIVERED, ParcelStatus.DELIVERED),
        ("Pošiljka je uručena primatelju", ParcelStatus.DELIVERED),
        (p.RETURNING, ParcelStatus.RETURNING),
        (p.DELIVERED_TO_SENDER, ParcelStatus.RETURNING),
    ],
)
def test_status_map(wording, expected):
    parcel = _normalize([row("01.05.2026", "10:00:00", wording)])
    assert parcel["status"] == expected
    assert parcel["raw_status"] == wording


def test_accepted_and_arrived_are_different_statuses():
    """English folds both into "Item received"; the Croatian page must not."""
    assert _normalize([row("01.05.2026", "10:00:00", p.ACCEPTED)])["status"] == (
        ParcelStatus.REGISTERED
    )
    assert _normalize([row("01.05.2026", "10:00:00", p.ARRIVED)])["status"] == (
        ParcelStatus.IN_TRANSIT
    )


def test_unmapped_wording_falls_back_to_newest_mappable_and_warns_once(caplog):
    rows = [
        row("01.05.2026", "10:00:00", p.SENT),
        row("02.05.2026", "10:00:00", "Pošiljka je poslana na carinjenje"),
    ]
    with caplog.at_level(logging.WARNING):
        parcel = _normalize(rows)
        _normalize(rows)
    assert parcel["status"] == ParcelStatus.IN_TRANSIT
    assert parcel["raw_status"] == "Pošiljka je poslana na carinjenje"
    assert caplog.text.count("poslana na carinjenje") == 1
    assert "issues/new?template=unrecognised_status.yml" in caplog.text


def test_only_unmapped_wording_is_unknown_with_warning(caplog):
    with caplog.at_level(logging.WARNING):
        parcel = _normalize([row("01.05.2026", "10:00:00", "Nešto novo")])
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert "Nešto novo" in caplog.text


def test_unmapped_history_entry_keeps_null_status():
    parcel = _normalize(
        [row("01.05.2026", "10:00:00", "Nešto novo")], include_history=True
    )
    assert parcel["history"][0]["status"] is None


# ---------------------------------------------------------------------------
# ordering and the return sequence
# ---------------------------------------------------------------------------


def test_page_order_does_not_decide_the_status():
    """The page groups rows by milestone; the clock decides the newest row."""
    rows = list(reversed(p.HOME_ROWS))
    parcel = _normalize(rows)
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["delivered_at"].startswith("2026-04-22T13:12:42")


def test_same_second_tie_is_broken_by_reverse_page_position():
    """Two scans share a second; the one printed lower on the page is older."""
    first_on_page = row("01.05.2026", "10:00:00", p.RETURNING)
    second_on_page = row("01.05.2026", "10:00:00", p.NOT_DELIVERED)
    # Page order: RETURNING, NOT_DELIVERED -> NOT_DELIVERED is the older scan.
    parcel = _normalize([first_on_page, second_on_page])
    assert parcel["status"] == ParcelStatus.RETURNING
    assert parcel["raw_status"] == p.RETURNING
    # And the other page order flips the result.
    flipped = _normalize([second_on_page, first_on_page])
    assert flipped["status"] == ParcelStatus.PROBLEM


def test_full_return_never_counts_as_delivered():
    parcel = _normalize(p.RETURN_ROWS)
    assert parcel["status"] == ParcelStatus.RETURNING
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None
    assert parcel["raw_status"] == p.DELIVERED_TO_SENDER


def test_out_for_delivery_after_a_return_is_not_forward_progress():
    rows = [
        row("01.05.2026", "08:00:00", p.RETURNING),
        row("02.05.2026", "08:00:00", p.OUT_FOR_DELIVERY),
    ]
    parcel = _normalize(rows, include_history=True)
    assert parcel["status"] == ParcelStatus.RETURNING
    assert parcel["history"][-1]["status"] == ParcelStatus.RETURNING


def test_pickup_notice_after_a_return_is_the_senders_office():
    rows = [
        row("01.05.2026", "08:00:00", p.RETURNING, "10119 ZAGREB"),
        row("08.05.2026", "08:00:00", p.PICKUP_OFFICE, "10000 ZAGREB"),
    ]
    parcel = _normalize(rows, include_history=True)
    assert parcel["status"] == ParcelStatus.RETURNING
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None
    assert parcel["history"][-1]["status"] == ParcelStatus.RETURNING


def test_acceptance_after_transit_does_not_regress_to_registered():
    rows = [
        row("01.05.2026", "08:00:00", p.ARRIVED, "10003 ZAGREB"),
        row("01.05.2026", "10:00:00", p.ACCEPTED, "10200 ZAGREB"),
        row("01.05.2026", "11:00:00", p.ANNOUNCED),
    ]
    parcel = _normalize(rows, include_history=True)
    assert parcel["status"] == ParcelStatus.IN_TRANSIT
    assert [e["status"] for e in parcel["history"]] == [ParcelStatus.IN_TRANSIT] * 3


def test_acceptance_first_stays_registered():
    assert _normalize([row("01.05.2026", "10:00:00", p.ACCEPTED)])["status"] == (
        ParcelStatus.REGISTERED
    )


def test_delivered_after_a_return_started_is_returning():
    rows = [
        row("01.05.2026", "08:00:00", p.RETURNING),
        row("02.05.2026", "08:00:00", p.DELIVERED),
    ]
    assert _normalize(rows)["status"] == ParcelStatus.RETURNING


def test_delivered_twice_uses_the_newest_row():
    rows = [
        row("01.05.2026", "08:00:00", p.DELIVERED),
        row("01.05.2026", "08:00:02", p.DELIVERED),
    ]
    parcel = _normalize(rows)
    assert parcel["delivered_at"].startswith("2026-05-01T08:00:02")


# ---------------------------------------------------------------------------
# pickup point, weight, clocks
# ---------------------------------------------------------------------------


def test_pickup_point_drops_the_postcode():
    parcel = normalize_parcel(pickup_sample())
    assert parcel["status"] == ParcelStatus.AT_PICKUP_POINT
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] == "OFFICE"


def test_locker_redirect_marks_the_parcel_headed_for_a_pickup_point():
    rows = [
        row("01.05.2026", "08:00:00", p.ACCEPTED, "10200 ZAGREB"),
        row("01.05.2026", "10:00:00", p.REDIRECTED),
    ]
    parcel = _normalize(rows)
    assert parcel["status"] == ParcelStatus.IN_TRANSIT
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] is None


def test_plain_home_delivery_is_not_headed_for_a_pickup_point():
    assert _normalize(p.HOME_ROWS)["pickup"] is False


def test_a_return_is_not_headed_for_a_pickup_point():
    assert _normalize(p.RETURN_ROWS)["pickup"] is False


def test_locker_delivery_keeps_the_pickup_point():
    parcel = _normalize(p.LOCKER_ROWS)
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] == "LOCKER"


def test_home_delivery_has_no_pickup_point():
    assert _normalize(p.HOME_ROWS)["pickup_point"] is None


def test_pickup_row_without_location_has_no_pickup_point():
    parcel = _normalize([row("01.05.2026", "10:00:00", p.PICKUP_OFFICE)])
    assert parcel["pickup_point"] is None


def test_zero_grams_is_zero_weight_and_missing_is_none():
    assert normalize_parcel(parsed(CODE, p.HOME_ROWS, grams=0))["weight"] == 0.0
    assert normalize_parcel(parsed(CODE, p.HOME_ROWS, grams=None))["weight"] is None


def test_weight_is_kilograms():
    assert normalize_parcel(parsed(CODE, p.HOME_ROWS, grams=1250))["weight"] == 1.25


def test_croatian_clock_is_zagreb_with_dst():
    summer = parse_clock("23.07.2026", "09:35:34", "10000 OFFICE")
    winter = parse_clock("23.01.2026", "09:35:34", None)
    assert summer.utcoffset() == timedelta(hours=2)
    assert winter.utcoffset() == timedelta(hours=1)


def test_relayed_foreign_scan_uses_the_country_zone(caplog):
    with caplog.at_level(logging.DEBUG):
        moment = parse_clock("10.04.2026", "12:00:00", "JPKIXA Tokyo")
    assert moment.tzinfo == ZoneInfo("Asia/Tokyo")
    assert zone_for_location("DENUEB Nuernberg") == ZoneInfo("Europe/Berlin")


def test_relayed_scan_from_a_multi_zone_country_falls_back_to_zagreb():
    assert zone_for_location("USJFKA New York") == ZoneInfo("Europe/Zagreb")


def test_inbound_foreign_item_history_carries_offsets():
    parcel = _normalize(p.INBOUND_ROWS, include_history=True)
    assert parcel["history"][0]["timestamp"] == "2026-04-10T23:30:00+02:00"
    assert parcel["status"] == ParcelStatus.IN_TRANSIT


@pytest.mark.parametrize(
    ("date", "clock"),
    [(None, "10:00:00"), ("01.05.2026", None), ("31.02.2026", "10:00:00")],
)
def test_parse_clock_rejects_garbage(date, clock):
    assert parse_clock(date, clock, None) is None


def test_rows_with_unreadable_clocks_are_skipped():
    rows = [
        row("99.99.9999", "10:00:00", p.DELIVERED),
        row("01.05.2026", "10:00:00", p.SENT),
    ]
    assert _normalize(rows)["status"] == ParcelStatus.IN_TRANSIT


# ---------------------------------------------------------------------------
# timestamp helpers
# ---------------------------------------------------------------------------


def test_parse_iso_handles_z_naive_and_garbage():
    assert parse_iso("2026-04-29T13:12:42Z").tzinfo is not None
    # A naive value is assumed UTC so mixed lists still sort.
    assert parse_iso("2026-04-29T13:12:42").tzinfo == UTC
    assert parse_iso("not-a-date") is None
    assert parse_iso(None) is None


def test_to_iso_timestamp_converts_epoch_milliseconds():
    assert to_iso_timestamp(1784203767167) == "2026-07-16T12:09:27.167000+00:00"
    assert to_iso_timestamp("2026-04-29T13:12:42Z") == "2026-04-29T13:12:42Z"
    assert to_iso_timestamp(None) is None
    assert to_iso_timestamp(10**20) is None  # out of range -> None, never raises


def test_format_dimensions_needs_all_three_axes():
    assert format_dimensions(30, 20, 10) == {
        "length": 30,
        "width": 20,
        "height": 10,
        "text": "30 x 20 x 10 cm",
    }
    assert format_dimensions(30, None, 10) is None


def test_tracking_url_is_the_result_page():
    assert tracking_url(CODE) == (
        "https://posiljka.posta.hr/hr/tracking/trackingdata?barcode=" + CODE
    )
    assert tracking_url(None) is None


# ---------------------------------------------------------------------------
# build_history
# ---------------------------------------------------------------------------


def test_history_is_oldest_to_newest_and_capped():
    rows = [row(f"{day:02d}.04.2026", "10:00:00", p.SENT) for day in range(1, 26)]
    history = _normalize(rows, include_history=True)["history"]
    assert len(history) == 20
    assert history[0]["timestamp"].startswith("2026-04-06")
    assert set(history[0]) == {"timestamp", "status", "raw_status"}
    assert history[-1]["raw_status"] == p.SENT


def test_build_history_handles_none():
    assert build_history(None) == []


# ---------------------------------------------------------------------------
# normalize_parcel — the canonical contract
# ---------------------------------------------------------------------------

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]


def test_normalize_publishes_exactly_the_canonical_keys():
    """The aggregator and cross-carrier dashboards depend on this key set."""
    assert list(normalize_parcel(delivered_sample())) == CANONICAL_KEYS


def test_capabilities_are_known_values():
    """A typo here would silently misreport this carrier on the docs site."""
    assert CAPABILITIES <= KNOWN_CAPABILITIES
    assert PENDING_CAPABILITIES <= KNOWN_CAPABILITIES


def test_a_capability_is_never_both_populated_and_pending():
    """The docs site would have to pick one; "awaiting data" must not hide a confirmed field."""
    assert not CAPABILITIES & PENDING_CAPABILITIES


def test_capabilities_match_what_normalize_parcel_actually_returns():
    """Every declared CAPABILITIES entry must come true somewhere in a sample.

    Copy this test into a real carrier's own test_parcels.py verbatim — it
    stays correct for whatever subset of CAPABILITIES that carrier declares.
    """
    delivered = normalize_parcel(delivered_sample())
    active = normalize_parcel(active_sample())
    pickup = normalize_parcel(pickup_sample())
    with_history = normalize_parcel(delivered_sample(), include_history=True)

    if "weight" in CAPABILITIES:
        assert delivered["weight"] is not None
    if "dimensions" in CAPABILITIES:
        assert delivered["dimensions"] is not None
    if "delivery_window" in CAPABILITIES:
        assert active["planned_from"] is not None or active["planned_to"] is not None
    if "pickup_point" in CAPABILITIES:
        assert pickup["pickup_point"] is not None
    if "url" in CAPABILITIES:
        assert delivered["url"] is not None
    if "history" in CAPABILITIES:
        assert with_history["history"] is not None


def test_normalize_delivered_parcel():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["carrier"] == "Hrvatska pošta"
    assert parcel["barcode"] == p.DELIVERED_CODE
    assert parcel["sender"] is None
    assert parcel["receiver"] is None
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["raw_status"] == p.DELIVERED
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-04-22T13:12:42+02:00"
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None
    assert parcel["weight"] == 0.076
    assert parcel["dimensions"] is None
    assert parcel["history"] is None  # opt-in, default off


def test_normalize_active_parcel():
    parcel = normalize_parcel(active_sample())
    assert parcel["status"] == ParcelStatus.OUT_FOR_DELIVERY
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None


def test_normalize_pending_placeholder():
    """A tracked-but-unanswered code still yields a full parcel dict."""
    parcel = normalize_parcel({"trackingNumber": "RR000000009HR"})
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["delivered"] is False
    assert parcel["raw_status"] is None
    assert parcel["weight"] is None
    assert parcel["history"] is None


def test_normalize_keeps_raw_payload():
    raw = active_sample()
    assert normalize_parcel(raw)["raw"] is raw


# ---------------------------------------------------------------------------
# sort_parcels_by_ts
# ---------------------------------------------------------------------------


def test_sort_parcels_ascending_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "planned_from": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "planned_from": None},
        {"barcode": "c", "planned_from": "2026-05-01T10:00:00Z"},
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["c", "a", "b"]


def test_sort_parcels_descending_still_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "delivered_at": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "delivered_at": "nonsense"},
        {"barcode": "c", "delivered_at": "2026-05-01T10:00:00Z"},
    ]
    ordered = [
        p["barcode"]
        for p in sort_parcels_by_ts(parcels, "delivered_at", descending=True)
    ]
    assert ordered == ["a", "c", "b"]


# ---------------------------------------------------------------------------
# apply_delivered_filter
# ---------------------------------------------------------------------------


def _entry(filter_type: str, amount: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        unique_id=DOMAIN,
    )


def _delivered_pair() -> list[dict]:
    now = datetime.now(UTC)
    return [
        {"barcode": "RECENT", "delivered_at": (now - timedelta(days=1)).isoformat()},
        {"barcode": "OLD", "delivered_at": (now - timedelta(days=30)).isoformat()},
    ]


def test_delivered_filter_by_days():
    kept = apply_delivered_filter(_delivered_pair(), _entry("days", 7))
    assert [p["barcode"] for p in kept] == ["RECENT"]


def test_delivered_filter_by_count():
    parcels = _delivered_pair()
    assert apply_delivered_filter(parcels, _entry("parcels", 1)) == parcels[:1]


def test_delivered_filter_keeps_unparseable_timestamp():
    """Better to show a parcel with a broken date than to silently drop it."""
    parcels = [{"barcode": "WEIRD", "delivered_at": "nonsense"}]
    assert apply_delivered_filter(parcels, _entry("days", 7)) == parcels
