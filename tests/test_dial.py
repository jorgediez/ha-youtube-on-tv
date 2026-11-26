"""Tests for the DIAL helpers."""

from __future__ import annotations

import re
import socket
from typing import Any
from unittest.mock import patch

import pytest

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
from .harness import (
    AiohttpClientMocker,
)

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


def _resolve(names: dict[str, str]) -> Any:
    """Return a getaddrinfo stand-in resolving the given names."""

    async def getaddrinfo(host: str, *_args: Any, **_kwargs: Any) -> list[Any]:
        if host not in names:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (names[host], 0))]

    return getaddrinfo


@pytest.mark.parametrize(
    "application_url",
    [
        "http://192.0.2.99:8080/ws/app/",
        "http://router.lan/ws/app/",
        "file:///ws/app/",
    ],
)
async def test_location_app_url_on_another_host(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
    application_url: str,
) -> None:
    """An Application-URL on another host is not followed."""
    aioclient_mock.get(
        LOCATION, text=DESCRIPTION, headers={"Application-URL": application_url}
    )
    with (
        patch.object(
            hass.loop, "getaddrinfo", _resolve({HOST: HOST, "router.lan": "192.0.2.1"})
        ),
        pytest.raises(DialNoScreenError),
    ):
        await async_get_screen_from_location(hass, LOCATION)
    assert [call[1].host for call in aioclient_mock.mock_calls] == [HOST]
    assert "is on another host" in caplog.text


async def test_location_by_name_reports_app_by_address(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A TV reached by name may report its apps by IP address."""
    location = "http://tv.lan:7678/nservice/"
    aioclient_mock.get(
        location,
        text=DESCRIPTION,
        headers={"Application-URL": f"http://{HOST}:8080/ws/app/"},
    )
    aioclient_mock.get(APP_URL, text=APP_INFO)
    with patch.object(hass.loop, "getaddrinfo", _resolve({"tv.lan": HOST, HOST: HOST})):
        screen = await async_get_screen_from_location(hass, location)
    assert screen.app_url == APP_URL


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


async def test_run_url_on_another_host(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A stop link on another host is not used."""
    aioclient_mock.get(
        APP_URL, text=APP_INFO.replace('href="run"', 'href="http://192.0.2.1/run"')
    )
    with patch.object(hass.loop, "getaddrinfo", _resolve({HOST: HOST})):
        assert await async_get_run_url(hass, APP_URL) is None
    assert "stop link on another host" in caplog.text


@pytest.mark.parametrize("method", ["get", "post", "delete"])
async def test_dial_requests_do_not_follow_redirects(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, method: str
) -> None:
    """Every DIAL request is sent with redirects off."""
    with patch.object(
        aioclient_mock, "match_request", wraps=aioclient_mock.match_request
    ) as match_request:
        getattr(aioclient_mock, method)(APP_URL, status=302)
        if method == "get":
            assert await async_get_app_state(hass, APP_URL) is None
        elif method == "post":
            with pytest.raises(DialError):
                await async_launch_app(hass, APP_URL)
        else:
            with pytest.raises(DialError):
                await async_stop_app(hass, APP_URL)
    assert match_request.call_args.kwargs["allow_redirects"] is False
