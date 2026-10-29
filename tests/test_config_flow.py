"""Tests for the Hrvatska pošta config and options flow."""

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hrvatska_posta.config_flow import (
    normalize_tracking_code,
    valid_tracking_code,
)
from custom_components.hrvatska_posta.const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_INCLUDE_HISTORY,
    CONF_PARCELS,
    CONF_TRACKING_CODE,
    DOMAIN,
)


def test_normalize_tracking_code_strips_whitespace_and_uppercases():
    assert normalize_tracking_code(" rr 000000002 hr ") == "RR000000002HR"
    assert normalize_tracking_code("") == ""
    assert normalize_tracking_code(None) == ""


@pytest.mark.parametrize(
    "code",
    [
        "RR000000002HR",
        "EE123456789DE",
        "3SABC123456789",
        "123456789012",
        "00" + "1" * 18,
        "J" + "A1" * 8,
        "BAT123456",
    ],
)
def test_valid_tracking_code_accepts_the_trackers_own_shapes(code):
    assert valid_tracking_code(code)


@pytest.mark.parametrize(
    "code",
    [
        "",
        "ABC",
        "RR00000000HR",
        "RR0000000002HR",
        "12345678901",
        "3S",
        "J123",
        "BAT",
        "RR-000000002HR",
    ],
)
def test_valid_tracking_code_rejects_other_shapes(code):
    assert not valid_tracking_code(code)


async def test_user_flow_creates_hub_without_input(hass):
    """No account, no postcode — the entry is created straight away."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == "create_entry"
    assert result["title"] == "Hrvatska pošta"
    assert result["options"][CONF_PARCELS] == []


async def test_second_hub_rejected(hass):
    MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] == "abort"
    # single_config_entry in the manifest aborts before the flow runs.
    assert result["reason"] == "single_instance_allowed"


def _hub(parcels: list[dict]) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        options={CONF_PARCELS: parcels},
    )


def _settings_input(
    *,
    history=False,
    filter_type="days",
    amount=7,
) -> dict:
    """Build the settings-form submission."""
    return {
        CONF_DELIVERED_FILTER_TYPE: filter_type,
        CONF_DELIVERED_FILTER_AMOUNT: amount,
        CONF_INCLUDE_HISTORY: history,
    }


async def _open_options_step(hass, entry, step_id: str):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    assert result["menu_options"] == ["parcels", "settings"]
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step_id}
    )


async def test_options_add_parcel(hass):
    entry = _hub([])
    entry.add_to_hass(hass)

    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": ["rr000000002hr"]}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == [{CONF_TRACKING_CODE: "RR000000002HR"}]


async def test_options_add_code_with_separators(hass):
    """Pasted codes with internal spaces are cleaned up."""
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": ["rr 000000002 hr"]}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == [{CONF_TRACKING_CODE: "RR000000002HR"}]


async def test_options_rejects_a_code_the_tracker_would_not_accept(hass):
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": ["abc"]}
    )
    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_tracking_code"}


async def test_options_de_duplicates_tracking_codes(hass):
    entry = _hub([{CONF_TRACKING_CODE: "RR000000011HR"}])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": ["RR000000011HR", "rr000000011hr"]}
    )
    assert result["data"][CONF_PARCELS] == [{CONF_TRACKING_CODE: "RR000000011HR"}]


async def test_options_remove_parcel(hass):
    entry = _hub(
        [
            {CONF_TRACKING_CODE: "RR000000011HR"},
            {CONF_TRACKING_CODE: "RR000000012HR"},
        ]
    )
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": ["RR000000012HR"]}
    )
    assert result["type"] == "create_entry"
    codes = {p[CONF_TRACKING_CODE] for p in result["data"][CONF_PARCELS]}
    assert codes == {"RR000000012HR"}


async def test_options_can_clear_the_tracked_code_list(hass):
    entry = _hub([{CONF_TRACKING_CODE: "RR000000011HR"}])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "parcels")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tracking_codes": []}
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_PARCELS] == []


async def test_options_changes_history_and_delivered(hass):
    entry = _hub([])
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "settings")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _settings_input(
            history=True,
            filter_type="parcels",
            amount=5,
        ),
    )
    assert result["type"] == "create_entry"
    assert result["data"][CONF_INCLUDE_HISTORY] is True
    assert result["data"][CONF_DELIVERED_FILTER_TYPE] == "parcels"
    assert result["data"][CONF_DELIVERED_FILTER_AMOUNT] == 5
