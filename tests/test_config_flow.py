"""Tests for the YouTube on TV config flow."""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.youtube_on_tv.config_flow import (
    InvalidPairingCode,
    _async_pair_with_code,
    screen_unique_id,
)
from custom_components.youtube_on_tv.const import (
    CONF_PAIRING_CODE,
    CONF_SCREEN_ID,
    DOMAIN,
)
from custom_components.youtube_on_tv.dial import (
    DialConnectionError,
    DialNoScreenError,
)
from homeassistant.config_entries import SOURCE_SSDP, SOURCE_USER
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo

from .conftest import (
    ENTRY_DATA,
    HOST,
    MOCK_SCREEN,
    NEW_SCREEN_ID,
    SCREEN_ID,
    TITLE,
    UDN,
)

FLOW = "custom_components.youtube_on_tv.config_flow"
LOCATION = f"http://{HOST}:7678/nservice/"

SSDP_INFO = SsdpServiceInfo(
    ssdp_usn=f"{UDN}::urn:dial-multiscreen-org:service:dial:1",
    ssdp_st="urn:dial-multiscreen-org:service:dial:1",
    ssdp_location=LOCATION,
    upnp={},
)


@pytest.fixture(autouse=True)
def mock_setup_entry() -> Generator[AsyncMock]:
    """Don't set up the integration after creating an entry."""
    with patch(
        "custom_components.youtube_on_tv.async_setup_entry", return_value=True
    ) as mock:
        yield mock


@pytest.fixture
def mock_from_host() -> Generator[AsyncMock]:
    """Patch the DIAL lookup by host."""
    with patch(f"{FLOW}.async_get_screen_from_host", return_value=MOCK_SCREEN) as mock:
        yield mock


@pytest.fixture
def mock_from_location() -> Generator[AsyncMock]:
    """Patch the DIAL lookup by description URL."""
    with patch(
        f"{FLOW}.async_get_screen_from_location", return_value=MOCK_SCREEN
    ) as mock:
        yield mock


@pytest.fixture
def mock_pair() -> Generator[AsyncMock]:
    """Patch pairing with a TV code."""
    with patch(
        f"{FLOW}._async_pair_with_code", return_value=(SCREEN_ID, "YouTube on TV")
    ) as mock:
        yield mock


async def test_user_host(hass: HomeAssistant, mock_from_host: AsyncMock) -> None:
    """A TV is added by IP address."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "host"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "host"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: f" {HOST} "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == TITLE
    assert result["data"] == ENTRY_DATA
    assert result["result"].unique_id == UDN
    mock_from_host.assert_awaited_once_with(hass, HOST)


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (DialConnectionError, "cannot_connect"),
        (DialNoScreenError, "no_screen_id"),
        (RuntimeError, "unknown"),
    ],
)
async def test_user_host_errors(
    hass: HomeAssistant, mock_from_host: AsyncMock, error: Exception, reason: str
) -> None:
    """Errors are shown and the user can retry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "host"}
    )
    mock_from_host.side_effect = error
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": reason}

    mock_from_host.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_host_already_configured(
    hass: HomeAssistant, mock_from_host: AsyncMock
) -> None:
    """Adding the same TV again updates its data and aborts."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=UDN, data={**ENTRY_DATA, CONF_HOST: "10.0.0.2"}
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "host"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == HOST


async def test_user_pairing_code(hass: HomeAssistant, mock_pair: AsyncMock) -> None:
    """A TV is added with a TV code."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "pairing_code"}
    )
    assert result["step_id"] == "pairing_code"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PAIRING_CODE: "123 456 789 012"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "YouTube on TV"
    assert result["data"] == {CONF_SCREEN_ID: SCREEN_ID}
    assert result["result"].unique_id == screen_unique_id(SCREEN_ID)
    assert SCREEN_ID not in result["result"].unique_id


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (InvalidPairingCode, "invalid_pairing_code"),
        (aiohttp.ClientError, "cannot_connect"),
        (RuntimeError, "unknown"),
    ],
)
async def test_user_pairing_code_errors(
    hass: HomeAssistant, mock_pair: AsyncMock, error: Exception, reason: str
) -> None:
    """Pairing errors are shown and the user can retry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "pairing_code"}
    )
    mock_pair.side_effect = error
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PAIRING_CODE: "1"}
    )
    assert result["errors"] == {"base": reason}

    mock_pair.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PAIRING_CODE: "1"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_ssdp_discovery(
    hass: HomeAssistant, mock_from_location: AsyncMock
) -> None:
    """A discovered TV is confirmed and added."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_SSDP}, data=SSDP_INFO
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discovery_confirm"
    assert result["description_placeholders"] == {"name": TITLE}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == TITLE
    assert result["data"] == ENTRY_DATA
    mock_from_location.assert_awaited_once_with(hass, LOCATION)


async def test_ssdp_updates_existing_entry(
    hass: HomeAssistant, mock_from_location: AsyncMock
) -> None:
    """Rediscovery refreshes the host and a rotated screen id."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=UDN,
        data={**ENTRY_DATA, CONF_HOST: "10.0.0.2", CONF_SCREEN_ID: "old"},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_SSDP}, data=SSDP_INFO
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == HOST
    assert entry.data[CONF_SCREEN_ID] == SCREEN_ID


async def test_ssdp_skips_tv_added_by_code(
    hass: HomeAssistant, mock_from_location: AsyncMock
) -> None:
    """A TV added with a code isn't offered again when discovered."""
    MockConfigEntry(
        domain=DOMAIN,
        unique_id=screen_unique_id(SCREEN_ID),
        data={CONF_SCREEN_ID: SCREEN_ID},
    ).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_SSDP}, data=SSDP_INFO
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (DialNoScreenError, "not_supported"),
        (DialConnectionError, "cannot_connect"),
    ],
)
async def test_ssdp_errors(
    hass: HomeAssistant, mock_from_location: AsyncMock, error: Exception, reason: str
) -> None:
    """Devices without a YouTube screen are ignored."""
    mock_from_location.side_effect = error
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_SSDP}, data=SSDP_INFO
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason


async def test_ssdp_without_location(hass: HomeAssistant) -> None:
    """Discovery info without a location is ignored."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_SSDP},
        data=replace(SSDP_INFO, ssdp_location=None),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


async def test_reauth_with_host(hass: HomeAssistant, mock_from_host: AsyncMock) -> None:
    """Reauth reads a fresh screen id from the TV."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=UDN, data=dict(ENTRY_DATA))
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    mock_from_host.side_effect = DialConnectionError
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "cannot_connect"}

    mock_from_host.side_effect = DialNoScreenError
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "no_screen_id"}

    mock_from_host.side_effect = None
    mock_from_host.return_value = replace(MOCK_SCREEN, screen_id=NEW_SCREEN_ID)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_SCREEN_ID] == NEW_SCREEN_ID
    assert entry.unique_id == UDN


async def test_reauth_with_pairing_code(
    hass: HomeAssistant, mock_pair: AsyncMock
) -> None:
    """Reauth of a TV added by code asks for a new code."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=screen_unique_id(SCREEN_ID),
        data={CONF_SCREEN_ID: SCREEN_ID},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "pairing_code"

    mock_pair.return_value = (NEW_SCREEN_ID, "YouTube on TV")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PAIRING_CODE: "123456789012"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_SCREEN_ID] == NEW_SCREEN_ID


async def test_pair_with_code() -> None:
    """The TV code is cleaned up and exchanged for a screen id."""
    with patch(f"{FLOW}.YtLoungeApi") as api_class:
        api = api_class.return_value.__aenter__.return_value
        api.auth.screen_id = SCREEN_ID
        api.screen_name = "YouTube on TV"
        assert await _async_pair_with_code("123-456 789 012") == (
            SCREEN_ID,
            "YouTube on TV",
        )
    api.pair.assert_awaited_once_with("123456789012")


@pytest.mark.parametrize("code", ["", "no digits"])
async def test_pair_with_empty_code(code: str) -> None:
    """A code without digits is rejected without contacting YouTube."""
    with (
        patch(f"{FLOW}.YtLoungeApi") as api_class,
        pytest.raises(InvalidPairingCode),
    ):
        await _async_pair_with_code(code)
    api_class.assert_not_called()


async def test_pair_with_rejected_code() -> None:
    """YouTube rejecting the code raises InvalidPairingCode."""
    with patch(f"{FLOW}.YtLoungeApi") as api_class:
        api = api_class.return_value.__aenter__.return_value
        api.pair.side_effect = KeyError("screen")
        with pytest.raises(InvalidPairingCode):
            await _async_pair_with_code("123456789012")
