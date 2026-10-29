"""Diagnostics support for the Hrvatska pošta parcel tracker integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import HrvatskaPostaConfigEntry

# Diagnostics are pasted into public issues, so redact anything that
# identifies a person, an address or a specific parcel. Over-redacting is
# cheap; under-redacting leaks a user's home address into a GitHub thread.
#
# Scan locations are post offices, sorting centres and lockers rather than
# addresses, but they place the recipient, so they are redacted as well.
TO_REDACT = {
    # canonical fields we publish ourselves
    "tracking_code",
    "barcode",
    "sender",
    "receiver",
    "url",
    "pickup_point",
    # carrier payload fields
    "trackingNumber",
    "location",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HrvatskaPostaConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for the Hrvatska pošta config entry."""
    coordinator = entry.runtime_data.coordinator

    return {
        "entry_options": async_redact_data(dict(entry.options), TO_REDACT),
        "counts": {
            "incoming_active": len(coordinator.data or []),
            "delivered": len(coordinator.delivered or []),
            "skipped_from_fetch": len(coordinator.delivered_codes),
        },
        "polling": {
            "tier_minutes": coordinator.current_tier_minutes,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
            "suspended": coordinator.update_interval is None,
        },
        "incoming": async_redact_data(coordinator.data or [], TO_REDACT),
        "delivered": async_redact_data(coordinator.delivered or [], TO_REDACT),
    }
