"""Tests for Hrvatska pošta diagnostics."""

from datetime import timedelta
from unittest.mock import MagicMock

from custom_components.hrvatska_posta.diagnostics import (
    async_get_config_entry_diagnostics,
)


async def test_diagnostics_redacts_and_counts(hass):
    """Diagnostics get pasted into public issues — nothing identifying may survive."""
    entry = MagicMock()
    entry.options = {"parcels": [{"tracking_code": "RR000000002HR"}]}
    entry.runtime_data.coordinator.current_tier_minutes = 15
    entry.runtime_data.coordinator.update_interval = timedelta(minutes=15)
    entry.runtime_data.coordinator.data = [
        {
            "barcode": "RR000000002HR",
            "sender": "Example Shop",
            "receiver": "Jane Doe",
            "status": "out_for_delivery",
            "pickup_point": "OFFICE",
            "raw": {
                "trackingNumber": "RR000000002HR",
                "rows": [
                    {
                        "date": "22.04.2026",
                        "time": "08:05:00",
                        "wording": "Dostava na adresu je u tijeku",
                        "location": "21000 OFFICE",
                    }
                ],
            },
        }
    ]
    entry.runtime_data.coordinator.delivered = []
    entry.runtime_data.coordinator.delivered_codes = set()

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["counts"] == {
        "incoming_active": 1,
        "delivered": 0,
        "skipped_from_fetch": 0,
    }
    assert result["polling"] == {
        "tier_minutes": 15,
        "update_interval_seconds": 900.0,
        "suspended": False,
    }
    # tracking codes and payload PII are redacted, at every nesting level
    assert result["entry_options"]["parcels"][0]["tracking_code"] == "**REDACTED**"
    assert result["incoming"][0]["barcode"] == "**REDACTED**"
    assert result["incoming"][0]["receiver"] == "**REDACTED**"
    assert result["incoming"][0]["pickup_point"] == "**REDACTED**"
    assert result["incoming"][0]["raw"]["trackingNumber"] == "**REDACTED**"
    row = result["incoming"][0]["raw"]["rows"][0]
    assert row["location"] == "**REDACTED**"
    assert row["wording"] == "Dostava na adresu je u tijeku"
    # non-identifying fields survive, or the diagnostics would be useless
    assert result["incoming"][0]["status"] == "out_for_delivery"


async def test_diagnostics_reports_suspended_polling(hass):
    """update_interval None (Section 2.1's full stop) must be visible, not just absent."""
    entry = MagicMock()
    entry.options = {"parcels": []}
    entry.runtime_data.coordinator.current_tier_minutes = None
    entry.runtime_data.coordinator.update_interval = None
    entry.runtime_data.coordinator.data = []
    entry.runtime_data.coordinator.delivered = []

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["polling"] == {
        "tier_minutes": None,
        "update_interval_seconds": None,
        "suspended": True,
    }
