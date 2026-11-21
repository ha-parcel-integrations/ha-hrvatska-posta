"""The parcel contract's checks, run over every normalised sample."""

from datetime import datetime

import pytest

from custom_components.hrvatska_posta.const import ParcelStatus
from custom_components.hrvatska_posta.parcels import (
    ZAGREB,
    normalize_parcel,
    parse_iso,
    planned_window,
)

from . import payloads as p
from .payloads import active_sample, delivered_sample, parsed, pickup_sample, row

CODE = "RR000000009HR"
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


def _from_rows(rows: list[dict]) -> dict:
    return normalize_parcel(parsed(CODE, rows))


SAMPLES = {
    "delivered": delivered_sample,
    "active": active_sample,
    "at_pickup_point": pickup_sample,
    "collected": lambda: parsed(CODE, p.LOCKER_ROWS),
    "returning": lambda: parsed(
        CODE, [row("01.05.2026", "08:00:00", p.RETURNING, "10000 X")]
    ),
    "back_at_sender": lambda: parsed(CODE, p.RETURN_ROWS),
    "problem": lambda: parsed(CODE, [row("01.05.2026", "08:00:00", p.NOT_DELIVERED)]),
    "bare": lambda: {"trackingNumber": "RR000000003HR"},
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return normalize_parcel(SAMPLES[request.param](), include_history=True)


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_delivered_follows_status(parcel):
    assert parcel["delivered"] is (parcel["status"] is ParcelStatus.DELIVERED)


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_planned_is_empty(parcel):
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None


def test_pickup_is_a_bool_and_the_point_needs_it(parcel):
    assert isinstance(parcel["pickup"], bool)
    if not parcel["pickup"]:
        assert parcel["pickup_point"] is None


def test_a_collected_locker_parcel_stays_pickup():
    parcel = _from_rows(p.LOCKER_ROWS)
    assert parcel["delivered"] is True
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] == "LOCKER"


def test_a_parcel_back_at_the_sender_is_not_delivered():
    parcel = _from_rows(p.RETURN_ROWS)
    assert parcel["status"] is ParcelStatus.RETURNING
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None


def test_a_moment_ends_at_the_end_of_that_day():
    start, end = planned_window("2099-04-29T13:00:00Z", None, zone=ZAGREB)
    end = datetime.fromisoformat(end)
    assert (end.hour, end.minute, end.second, end.microsecond) == (23, 59, 59, 0)
    assert end.date() == parse_iso(start).date()


def test_a_date_covers_the_whole_day():
    start, end = planned_window("2099-04-29", None, zone=ZAGREB)
    start = datetime.fromisoformat(start)
    end = datetime.fromisoformat(end)
    assert (start.hour, start.minute, start.second) == (0, 0, 0)
    assert (end.hour, end.minute, end.second, end.microsecond) == (23, 59, 59, 0)
    assert start.date() == end.date()
