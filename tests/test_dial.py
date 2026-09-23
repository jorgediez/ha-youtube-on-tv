"""Tests for the DIAL helpers."""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
)

from custom_components.youtube_on_tv.dial import (
    DialConnectionError,
    DialError,
    DialNoScreenError,
    async_get_app_state,
    async_get_run_url,
    async_get_screen_from_host,
    async_get_screen_from_location,
    async_launch_app,
    async_stop_app,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo

from .conftest import APP_URL, HOST, SCREEN_ID, UDN

LOCATION = f"http://{HOST}:7678/nservice/"

DESCRIPTION = f"""<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <device>
    <deviceType>urn:dial-multiscreen-org:device:dialreceiver:1</deviceType>
    <friendlyName>Samsung Neo QLED &amp; more</friendlyName>
    <manufacturer>Samsung</manufacturer>
    <modelName>TQ75QN900FTXXC</modelName>
    <UDN>{UDN}</UDN>
  </device>
</root>"""

APP_INFO = f"""<?xml version="1.0" encoding="UTF-8"?>
<service xmlns="urn:dial-multiscreen-org:schemas:dial" dialVer="2.1">
  <name>YouTube</name>
  <options allowStop="true"/>
  <state>running</state>
  <link rel="run" href="run"/>
  <additionalData>
    <screenId>{SCREEN_ID}</screenId>
  </additionalData>
</service>"""

APP_INFO_NO_SCREEN = """<service xmlns="urn:dial-multiscreen-org:schemas:dial">
  <name>YouTube</name><state>stopped</state>
</service>"""


async def test_screen_from_location(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The screen id and device details are read from DIAL."""
    aioclient_mock.get(
        LOCATION,
        text=DESCRIPTION,
        headers={"Application-URL": f"http://{HOST}:8080/ws/app"},
    )
    aioclient_mock.get(APP_URL, text=APP_INFO)

    screen = await async_get_screen_from_location(hass, LOCATION)

    assert screen.screen_id == SCREEN_ID
    assert screen.app_url == APP_URL
    assert screen.name == "Samsung Neo QLED & more"
    assert screen.udn == UDN
    assert screen.manufacturer == "Samsung"
    assert screen.model == "TQ75QN900FTXXC"


async def test_location_not_dial(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A UPnP device without an Application-URL isn't a DIAL server."""
    aioclient_mock.get(LOCATION, text=DESCRIPTION)
    with pytest.raises(DialNoScreenError):
        await async_get_screen_from_location(hass, LOCATION)


async def test_location_unreachable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Connection errors are reported as such."""
    aioclient_mock.get(LOCATION, exc=TimeoutError)
    with pytest.raises(DialConnectionError):
        await async_get_screen_from_location(hass, LOCATION)
    aioclient_mock.clear_requests()
    aioclient_mock.get(LOCATION, status=404)
    with pytest.raises(DialConnectionError):
        await async_get_screen_from_location(hass, LOCATION)


async def test_app_without_screen_id(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A YouTube app that exposes no screen id is reported."""
    aioclient_mock.get(
        LOCATION,
        text=DESCRIPTION,
        headers={"Application-URL": f"http://{HOST}:8080/ws/app/"},
    )
    aioclient_mock.get(APP_URL, text=APP_INFO_NO_SCREEN)
    with pytest.raises(DialNoScreenError):
        await async_get_screen_from_location(hass, LOCATION)


async def test_screen_from_host_uses_ssdp_cache(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A TV already seen by SSDP is looked up through its description."""
    aioclient_mock.get(
        LOCATION,
        text=DESCRIPTION,
        headers={"Application-URL": f"http://{HOST}:8080/ws/app/"},
    )
    aioclient_mock.get(APP_URL, text=APP_INFO)
    discovery = SsdpServiceInfo(
        ssdp_usn=UDN, ssdp_st="st", ssdp_location=LOCATION, upnp={}
    )
    with patch(
        "custom_components.youtube_on_tv.dial.ssdp.async_get_discovery_info_by_st",
        return_value=[discovery],
    ):
        screen = await async_get_screen_from_host(hass, HOST)
    assert screen.udn == UDN


async def test_screen_from_host_known_urls(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Without SSDP or a description, well-known app URLs are tried in turn."""
    aioclient_mock.get(LOCATION, exc=TimeoutError)
    aioclient_mock.get(f"http://{HOST}:8008/ssdp/device-desc.xml", status=404)
    aioclient_mock.get(APP_URL, exc=TimeoutError)
    aioclient_mock.get(f"http://{HOST}:8008/apps/YouTube", text=APP_INFO)
    with patch(
        "custom_components.youtube_on_tv.dial.ssdp.async_get_discovery_info_by_st",
        return_value=[],
    ):
        screen = await async_get_screen_from_host(hass, HOST)
    assert screen.screen_id == SCREEN_ID
    assert screen.app_url == f"http://{HOST}:8008/apps/YouTube"
    assert screen.udn is None


async def test_screen_from_host_known_description(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Without SSDP, a known description URL still gives the TV's name."""
    aioclient_mock.get(
        LOCATION,
        text=DESCRIPTION,
        headers={"Application-URL": f"http://{HOST}:8080/ws/app/"},
    )
    aioclient_mock.get(APP_URL, text=APP_INFO)
    with patch(
        "custom_components.youtube_on_tv.dial.ssdp.async_get_discovery_info_by_st",
        return_value=[],
    ):
        screen = await async_get_screen_from_host(hass, HOST)
    assert screen.name == "Samsung Neo QLED & more"
    assert screen.udn == UDN
    assert screen.app_url == APP_URL


@pytest.mark.parametrize(
    ("mocks", "error"),
    [
        ({}, DialConnectionError),
        ({APP_URL: APP_INFO_NO_SCREEN}, DialNoScreenError),
    ],
)
async def test_screen_from_host_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mocks: dict[str, str],
    error: type[Exception],
) -> None:
    """The most specific error is raised when no URL works."""
    for url, text in mocks.items():
        aioclient_mock.get(url, text=text)
    aioclient_mock.get(re.compile(r"^http://"), exc=TimeoutError)
    with (
        patch(
            "custom_components.youtube_on_tv.dial.ssdp.async_get_discovery_info_by_st",
            return_value=[],
        ),
        pytest.raises(error),
    ):
        await async_get_screen_from_host(hass, HOST)


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"text": APP_INFO}, "running"),
        ({"text": APP_INFO_NO_SCREEN}, "stopped"),
        ({"status": 404}, None),
        ({"exc": TimeoutError}, None),
        ({"text": "garbage"}, None),
    ],
)
async def test_app_state(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    kwargs: dict,
    expected: str | None,
) -> None:
    """The app state is read, or None when unknown."""
    aioclient_mock.get(APP_URL, **kwargs)
    assert await async_get_app_state(hass, APP_URL) == expected


async def test_launch_app(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Launching returns the URL of the running app."""
    run_url = f"http://{HOST}:8080/ws/apps/YouTube/run"
    aioclient_mock.post(APP_URL, text="", headers={"LOCATION": run_url})
    assert await async_launch_app(hass, APP_URL, "Yeke1krzPFM") == run_url
    method, _url, data, _headers = aioclient_mock.mock_calls[-1]
    assert method == "POST"
    assert data == {"v": "Yeke1krzPFM", "t": "0"}

    aioclient_mock.clear_requests()
    aioclient_mock.post(APP_URL, text="")
    assert await async_launch_app(hass, APP_URL) is None
    assert aioclient_mock.mock_calls[-1][2] is None


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"status": 403}, DialError),
        ({"exc": TimeoutError}, DialConnectionError),
    ],
)
async def test_launch_app_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    kwargs: dict,
    error: type[Exception],
) -> None:
    """A TV that refuses the launch is reported."""
    aioclient_mock.post(APP_URL, **kwargs)
    with pytest.raises(error):
        await async_launch_app(hass, APP_URL)


async def test_stop_app(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Stopping deletes the running app."""
    run_url = f"{APP_URL}/run"
    aioclient_mock.delete(run_url, status=200)
    await async_stop_app(hass, run_url)
    assert aioclient_mock.mock_calls[-1][0] == "DELETE"

    aioclient_mock.clear_requests()
    aioclient_mock.delete(run_url, status=404)
    with pytest.raises(DialError):
        await async_stop_app(hass, run_url)


async def test_get_run_url(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The running app's URL comes from the TV's own link."""
    aioclient_mock.get(APP_URL, text=APP_INFO)
    assert await async_get_run_url(hass, APP_URL) == f"{APP_URL}/run"

    aioclient_mock.clear_requests()
    aioclient_mock.get(APP_URL, text=APP_INFO_NO_SCREEN)
    assert await async_get_run_url(hass, APP_URL) is None
