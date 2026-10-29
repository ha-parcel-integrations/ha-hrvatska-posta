"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options. That is deliberate: it keeps the
carrier-specific mapping (which you rewrite per carrier) apart from the
coordinator (which is nearly identical everywhere), and it makes the mapping
trivially unit-testable without spinning up HA.

Two things here are carrier-specific: :data:`_STATUS_MAP` and
:func:`normalize_parcel`. Everything else — the
timestamp parsing, the history builder, the sort contract, the delivered
filter, the one-shot warning for unmapped statuses — is suite-wide machinery
and should be left alone.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
import re
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry

from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    HISTORY_MAX_EVENTS,
    TRACKING_URL,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# Where users report a status we do not map yet. Rewritten by the bootstrap
# script; it must point at the carrier's own repo so the log line is
# copy-pasteable straight into a new issue.
#
# The ``?template=`` parameter matters: without it the link opens a blank form,
# and the report comes back missing the version and the log line we need.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-hrvatska-posta/issues/new"
    "?template=unrecognised_status.yml"
)

# Keyed on the Croatian scan wording, whitespace-collapsed, exact match. The
# English page folds two different Croatian scans into "Item received", so the
# map must stay on the Croatian wording.
_REDIRECTED_TO_LOCKER = "Pošiljka preusmjerena u paketomat"

_STATUS_MAP: dict[str, ParcelStatus] = {
    "Pošiljatelj je unio podatke, pošiljka još nije predana Hrvatskoj pošti": ParcelStatus.REGISTERED,
    "Pošiljka je zaprimljena": ParcelStatus.REGISTERED,
    "Pošiljka je prispjela": ParcelStatus.IN_TRANSIT,
    "Pošiljka je u pripremi za otpremu": ParcelStatus.IN_TRANSIT,
    "Pošiljka je otpremljena": ParcelStatus.IN_TRANSIT,
    "Pošiljka je otpremljena prema unutrašnjem prometu": ParcelStatus.IN_TRANSIT,
    _REDIRECTED_TO_LOCKER: ParcelStatus.IN_TRANSIT,
    "Dostava na adresu je u tijeku": ParcelStatus.OUT_FOR_DELIVERY,
    "Pokušaj dostave pošiljke": ParcelStatus.PROBLEM,
    "Pošiljka nije uručena": ParcelStatus.PROBLEM,
    "Pošiljku možete preuzeti u poštanskom uredu": ParcelStatus.AT_PICKUP_POINT,
    "Pošiljka predana u paketomat": ParcelStatus.AT_PICKUP_POINT,
    "Pošiljka je uručena": ParcelStatus.DELIVERED,
    "Pošiljka je uručena primatelju": ParcelStatus.DELIVERED,
    "Pošiljka se vraća pošiljatelju": ParcelStatus.RETURNING,
    "Pošiljka je uručena pošiljatelju": ParcelStatus.RETURNING,
}

ZAGREB = ZoneInfo("Europe/Zagreb")

# Countries that sit in a single time zone, for scans relayed from abroad. A
# country with several zones (or one missing here) falls back to Zagreb.
_COUNTRY_ZONE_NAMES: dict[str, str] = {
    "AT": "Europe/Vienna",
    "BA": "Europe/Sarajevo",
    "BE": "Europe/Brussels",
    "BG": "Europe/Sofia",
    "CH": "Europe/Zurich",
    "CN": "Asia/Shanghai",
    "CZ": "Europe/Prague",
    "DE": "Europe/Berlin",
    "DK": "Europe/Copenhagen",
    "FI": "Europe/Helsinki",
    "FR": "Europe/Paris",
    "GB": "Europe/London",
    "GR": "Europe/Athens",
    "HK": "Asia/Hong_Kong",
    "HU": "Europe/Budapest",
    "IE": "Europe/Dublin",
    "IL": "Asia/Jerusalem",
    "IN": "Asia/Kolkata",
    "IT": "Europe/Rome",
    "JP": "Asia/Tokyo",
    "KR": "Asia/Seoul",
    "LU": "Europe/Luxembourg",
    "ME": "Europe/Podgorica",
    "MK": "Europe/Skopje",
    "NL": "Europe/Amsterdam",
    "NO": "Europe/Oslo",
    "PL": "Europe/Warsaw",
    "RO": "Europe/Bucharest",
    "RS": "Europe/Belgrade",
    "SE": "Europe/Stockholm",
    "SG": "Asia/Singapore",
    "SI": "Europe/Ljubljana",
    "SK": "Europe/Bratislava",
    "TH": "Asia/Bangkok",
    "TR": "Europe/Istanbul",
    "TW": "Asia/Taipei",
}
_COUNTRY_ZONES = {
    country: ZoneInfo(name) for country, name in _COUNTRY_ZONE_NAMES.items()
}

_UPU_OFFICE_RE = re.compile(r"^([A-Z]{2})[A-Z]{3}[A-Z0-9]\s")
_POSTCODE_RE = re.compile(r"^\d{5}\s+")
_RELAYED_LOGGED: set[str] = set()

# Status codes we have already warned about, so each unmapped one is logged
# only once per HA session instead of on every poll.
_unmapped_statuses_logged: set[str] = set()


def _warn_unmapped_status(code: str) -> None:
    """Log an unmapped carrier status once, with a copy-paste issue link."""
    if code in _unmapped_statuses_logged:
        return
    _unmapped_statuses_logged.add(code)
    _LOGGER.warning(
        "Unrecognised Hrvatska pošta status — help us map it. Open an issue "
        "and paste this line: %s\n  status=%s → no canonical status",
        NEW_ISSUE_URL,
        code,
    )


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Naive values are treated as UTC so a list always sorts without crashing on
    a mixed set.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def to_iso_timestamp(value: Any) -> str | None:
    """Return an ISO 8601 string for an API timestamp field.

    Numbers are treated as **epoch milliseconds** — the common case for the
    consumer APIs in this suite. Strings pass through untouched; their
    consumers are guarded by :func:`parse_iso`. Adjust the numeric branch if
    your carrier stamps in seconds.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return str(value)


def format_dimensions(
    length: float | None, width: float | None, height: float | None
) -> dict[str, Any] | None:
    """Return the canonical ``dimensions`` dict, or ``None`` when incomplete.

    Units contract: **centimetres**, with ``text`` pre-formatted as
    ``"L x W x H cm"`` (integer values, lowercase ``x``) so dashboards can show
    a dimension without doing their own formatting. Convert before calling if
    the carrier reports millimetres or inches.
    """
    if length is None or width is None or height is None:
        return None
    return {
        "length": length,
        "width": width,
        "height": height,
        "text": f"{int(length)} x {int(width)} x {int(height)} cm",
    }


def zone_for_location(location: str | None) -> ZoneInfo:
    """Return the zone a scan's clock was printed in.

    Croatian offices and lockers lead with a five-digit postcode; a scan relayed
    from abroad leads with a six-character UPU office code, whose first two
    letters name the country.
    """
    match = _UPU_OFFICE_RE.match(location or "")
    if match is None:
        return ZAGREB
    country = match.group(1)
    zone = _COUNTRY_ZONES.get(country)
    if country not in _RELAYED_LOGGED:
        _RELAYED_LOGGED.add(country)
        _LOGGER.debug(
            "Relayed foreign scan from %s, using zone %s", country, (zone or ZAGREB).key
        )
    return zone or ZAGREB


def parse_clock(
    date: str | None, clock: str | None, location: str | None
) -> datetime | None:
    """Parse a row's ``dd.MM.yyyy`` + ``HH:mm:ss`` into an aware datetime."""
    if not date or not clock:
        return None
    try:
        naive = datetime.strptime(f"{date} {clock}", "%d.%m.%Y %H:%M:%S")
    except ValueError:
        return None
    return naive.replace(tzinfo=zone_for_location(location))


_MOVING = frozenset(
    {
        ParcelStatus.IN_TRANSIT,
        ParcelStatus.OUT_FOR_DELIVERY,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.DELIVERED,
    }
)


def _walk_rows(rows: list[dict]) -> list[dict]:
    """Return the rows oldest to newest with a derived status on each.

    The page groups scans by milestone and shares seconds between them, so
    ties on the clock fall back to reverse page position. Once a return has
    started, a later delivered, out-for-delivery or pickup scan belongs to the
    return leg (the sender's office) and is not forward progress.
    """
    dated = []
    for index, row in enumerate(rows):
        moment = parse_clock(row.get("date"), row.get("time"), row.get("location"))
        if moment is not None and row.get("wording"):
            dated.append((moment, -index, row))
    dated.sort(key=lambda item: (item[0], item[1]))

    walked: list[dict] = []
    return_started = False
    moving = False
    previous: ParcelStatus | None = None
    for moment, _, row in dated:
        wording = row["wording"]
        status = _STATUS_MAP.get(wording)
        if status is None:
            _warn_unmapped_status(wording)
        entry = {
            "moment": moment,
            "wording": wording,
            "location": row.get("location"),
            "status": status,
            "previous": previous,
        }
        if status is not None:
            if return_started and status in (
                ParcelStatus.DELIVERED,
                ParcelStatus.OUT_FOR_DELIVERY,
                ParcelStatus.AT_PICKUP_POINT,
            ):
                entry["status"] = ParcelStatus.RETURNING
            elif moving and status is ParcelStatus.REGISTERED:
                entry["status"] = ParcelStatus.IN_TRANSIT
            if entry["status"] is ParcelStatus.RETURNING:
                return_started = True
            if entry["status"] in _MOVING:
                moving = True
            previous = status
        walked.append(entry)
    return walked


def build_history(
    walked: list[dict] | None, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Build the canonical ``history`` list from the walked scan rows.

    Each entry is ``{timestamp, status, raw_status}`` — identical across all
    suite carriers, and top-level (not under ``raw``) so it survives the
    aggregator's ``strip_raw()``. Oldest → newest, capped to the most recent
    ``max_events``. An unmapped wording keeps ``status: null``.
    """
    return [
        {
            "timestamp": entry["moment"].isoformat(),
            "status": entry["status"],
            "raw_status": entry["wording"],
        }
        for entry in (walked or [])
    ][-max_events:]


def tracking_url(tracking_code: str | None) -> str | None:
    """Construct the consumer tracking deep-link for a parcel."""
    if not tracking_code:
        return None
    return TRACKING_URL.format(tracking_code=tracking_code)


def _headed_for_pickup_point(walked: list[dict], status: ParcelStatus) -> bool:
    """Whether an undelivered parcel is going to, or waiting at, a pickup point.

    The locker redirect is the one signal before arrival; a post-office pickup
    notice only appears once the parcel is already there.
    """
    if status in (ParcelStatus.DELIVERED, ParcelStatus.RETURNING):
        return False
    return any(
        entry["wording"] == _REDIRECTED_TO_LOCKER
        or entry["status"] is ParcelStatus.AT_PICKUP_POINT
        for entry in walked
    )


def _pickup_name(location: str | None) -> str | None:
    """Return the office or locker name without its leading postcode."""
    if not location:
        return None
    return _POSTCODE_RE.sub("", location) or None


def normalize_parcel(raw: dict, *, include_history: bool = False) -> dict:
    """Return a carrier-agnostic parcel dict with the parsed page under ``raw``.

    The keys of the returned dict are the suite's contract. The page shows no
    ETA, sender, receiver or dimensions, so those are always ``None``.
    ``raw`` without scan rows (a number the carrier has not answered for yet)
    reports ``unknown`` without a warning.
    """
    tracking_code = raw.get("trackingNumber")
    walked = _walk_rows(raw.get("rows") or [])
    mapped = [entry for entry in walked if entry["status"] is not None]
    newest = mapped[-1] if mapped else None
    status = newest["status"] if newest else ParcelStatus.UNKNOWN
    delivered = status is ParcelStatus.DELIVERED

    pickup_point = None
    if status is ParcelStatus.AT_PICKUP_POINT or (
        delivered
        and newest is not None
        and newest["previous"] is ParcelStatus.AT_PICKUP_POINT
    ):
        at_point = [e for e in mapped if e["status"] is ParcelStatus.AT_PICKUP_POINT]
        pickup_point = _pickup_name(at_point[-1]["location"]) if at_point else None

    grams = raw.get("weightGrams")
    return {
        "carrier": "Hrvatska pošta",
        "barcode": tracking_code,
        "sender": None,
        "receiver": None,
        "status": status,
        "raw_status": walked[-1]["wording"] if walked else None,
        "delivered": delivered,
        "delivered_at": newest["moment"].isoformat() if delivered and newest else None,
        "planned_from": None,
        "planned_to": None,
        "pickup": _headed_for_pickup_point(walked, status),
        "pickup_point": pickup_point,
        "url": tracking_url(tracking_code),
        "weight": grams / 1000 if grams is not None else None,
        "dimensions": None,
        "history": build_history(walked) if include_history else None,
        "raw": raw,
    }


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    The suite's sort contract: incoming/outgoing ascending on ``planned_from``,
    delivered descending on ``delivered_at``. Parcels whose value is missing or
    unparseable always sort to the end, regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first. ``days`` keeps deliveries
    from the last N days (an unparseable ``delivered_at`` is kept rather than
    silently dropped); the ``parcels`` type keeps the N most recent. Parcels
    stay *tracked* either way — this only controls what the delivered sensor
    shows.
    """
    options = entry.options
    filter_type = options.get(CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE)
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(UTC) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
