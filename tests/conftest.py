"""Fixtures for YouTube on TV tests."""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.youtube_on_tv.const import (
    CONF_APP_URL,
    CONF_MANUFACTURER,
    CONF_MODEL,
    CONF_SCREEN_ID,
    DOMAIN,
)
from custom_components.youtube_on_tv.dial import DialScreen
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

HOST = "192.0.2.10"
SCREEN_ID = "0123456789abcdef" * 4
NEW_SCREEN_ID = "fedcba9876543210" * 4
UDN = "uuid:0725d7dc-4013-44ab-8527-7f1cac2ee5a9"
APP_URL = f"http://{HOST}:8080/ws/app/YouTube"
TITLE = "Samsung Neo QLED"

MOCK_SCREEN = DialScreen(
    app_url=APP_URL,
    screen_id=SCREEN_ID,
    name=TITLE,
    udn=UDN,
    manufacturer="Samsung",
    model="TQ75QN900FTXXC",
)

ENTRY_DATA = {
    CONF_HOST: HOST,
    CONF_SCREEN_ID: SCREEN_ID,
    CONF_APP_URL: APP_URL,
    CONF_MANUFACTURER: "Samsung",
    CONF_MODEL: "TQ75QN900FTXXC",
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable custom integrations in all tests."""


@pytest.fixture(autouse=True)
def fast_reconnect() -> Generator[None]:
    """Don't wait between reconnection attempts."""
    module = "custom_components.youtube_on_tv.coordinator"
    with (
        patch(f"{module}.RECONNECT_MIN_DELAY", 0.01),
        patch(f"{module}.MIN_SUBSCRIBE_SECONDS", 0.01),
    ):
        yield


@pytest.fixture(autouse=True)
def mock_oembed(aioclient_mock: AiohttpClientMocker) -> None:
    """Answer video title lookups."""
    aioclient_mock.get(
        "https://www.youtube.com/oembed",
        json={"title": "Dolor y Gloria", "author_name": "VivaSueciaVEVO"},
    )


@pytest.fixture
def entity_registry_enabled_by_default() -> Generator[None]:
    """Create entities that are disabled by default as enabled."""
    with patch(
        "homeassistant.helpers.entity.Entity.entity_registry_enabled_default",
        new_callable=PropertyMock,
        return_value=True,
    ):
        yield


@pytest.fixture(autouse=True)
def mock_ssdp_setup() -> Generator[None]:
    """Don't open SSDP sockets in tests."""
    with patch("homeassistant.components.ssdp.async_setup", return_value=True):
        yield


class FakeLounge:
    """Stand-in for pyytlounge.YtLoungeApi."""

    def __init__(self, device_name, event_listener=None, logger=None) -> None:
        self.device_name = device_name
        self.listener = event_listener
        self.session: MagicMock | None = None
        self.screen_device_name = "Samsung TQ75QN900FTXXC"
        self._linked = False
        self._connected = False
        self.subscribed = asyncio.Event()
        self._stop = asyncio.Event()
        self.pair_with_screen_id = AsyncMock(side_effect=self._link)
        self.connect = AsyncMock(side_effect=self._connect)
        self.subscribe = AsyncMock(side_effect=self._subscribe)
        self.get_now_playing = AsyncMock(return_value=True)
        self.disconnect = AsyncMock(return_value=True)
        self.close = AsyncMock(side_effect=self._close)
        self.play = AsyncMock(return_value=True)
        self.pause = AsyncMock(return_value=True)
        self.seek_to = AsyncMock(return_value=True)
        self.next = AsyncMock(return_value=True)
        self.previous = AsyncMock(return_value=True)
        self.skip_ad = AsyncMock(return_value=True)
        self.play_video = AsyncMock(return_value=True)
        self.screen_name = "YouTube on TV"

    async def __aenter__(self) -> FakeLounge:
        self.session = MagicMock(closed=False)
        return self

    async def _close(self) -> None:
        self.session.closed = True

    def linked(self) -> bool:
        return self._linked

    def connected(self) -> bool:
        return self._connected

    def drop_connection(self) -> None:
        """Simulate the TV ending the session."""
        self._connected = False
        self._stop.set()

    async def _link(self, screen_id: str, screen_name: str | None = None) -> bool:
        self._linked = True
        return True

    async def _connect(self) -> bool:
        self._connected = True
        return True

    async def _subscribe(self) -> None:
        self.subscribed.set()
        await self._stop.wait()
        self._stop.clear()


async def wait_for(condition, timeout: float = 5) -> None:
    """Wait until condition() is true, for state set by background tasks."""
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


@pytest.fixture
def mock_lounge() -> Generator[list[FakeLounge]]:
    """Patch the Lounge client; yields the created instances."""
    instances: list[FakeLounge] = []

    def factory(*args, **kwargs) -> FakeLounge:
        instance = FakeLounge(*args, **kwargs)
        instances.append(instance)
        return instance

    with patch(
        "custom_components.youtube_on_tv.coordinator.YtLoungeApi", side_effect=factory
    ):
        yield instances


@pytest.fixture
def mock_app_state() -> Generator[AsyncMock]:
    """Patch the DIAL app state poll."""
    with patch(
        "custom_components.youtube_on_tv.coordinator.async_get_app_state",
        return_value="running",
    ) as mock:
        yield mock


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a config entry for a TV found through DIAL."""
    return MockConfigEntry(
        domain=DOMAIN, title=TITLE, unique_id=UDN, data=dict(ENTRY_DATA)
    )


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_lounge: list[FakeLounge],
    mock_app_state: AsyncMock,
) -> FakeLounge:
    """Set up the integration and wait until it's listening."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    api = mock_lounge[0]
    async with asyncio.timeout(5):
        await api.subscribed.wait()
    return api
