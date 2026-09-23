"""DIAL helpers: read the YouTube screen id and app state from a TV."""

from __future__ import annotations

from dataclasses import dataclass
import html
import re
from urllib.parse import urljoin, urlparse

import aiohttp

from homeassistant.components import ssdp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DIAL_ST, DIAL_TIMEOUT

# Where common DIAL servers publish their device description, tried when the
# TV hasn't been seen by SSDP (e.g. it's on another subnet). The description
# gives the TV's name and model. {host} is replaced.
KNOWN_DESCRIPTION_URLS = (
    "http://{host}:7678/nservice/",  # Samsung Tizen
    "http://{host}:8008/ssdp/device-desc.xml",  # Chromecast, Google/Android TV
)

# YouTube app URLs of common DIAL servers, the last resort (no TV name).
ORIGIN = "https://www.youtube.com"

KNOWN_APP_URLS = (
    "http://{host}:8080/ws/app/YouTube",  # Samsung Tizen
    "http://{host}:8008/apps/YouTube",  # Chromecast, Google/Android TV
    "http://{host}:8060/dial/YouTube",  # Roku
    "http://{host}:36866/apps/YouTube",  # LG webOS
)


class DialError(Exception):
    """Base error for DIAL lookups."""


class DialConnectionError(DialError):
    """The TV's DIAL endpoint couldn't be reached."""


class DialNoScreenError(DialError):
    """The TV answered but exposes no YouTube screen id."""


@dataclass(frozen=True, slots=True)
class DialScreen:
    """A YouTube screen found through DIAL."""

    app_url: str
    screen_id: str
    name: str | None = None
    udn: str | None = None
    manufacturer: str | None = None
    model: str | None = None


def _tag(xml: str, tag: str) -> str | None:
    """Return the text of the first <tag> element in a DIAL/UPnP document."""
    match = re.search(rf"<{tag}>\s*([^<]*?)\s*</{tag}>", xml)
    return html.unescape(match.group(1)) if match and match.group(1) else None


async def _get(session: aiohttp.ClientSession, url: str) -> aiohttp.ClientResponse:
    try:
        response = await session.get(
            url, timeout=aiohttp.ClientTimeout(total=DIAL_TIMEOUT)
        )
        await response.read()
    except (aiohttp.ClientError, TimeoutError) as err:
        raise DialConnectionError(f"Error requesting {url}: {err}") from err
    return response


async def async_get_app_state(hass: HomeAssistant, app_url: str) -> str | None:
    """Return the YouTube app state ("running", "stopped", ...) or None if unknown."""
    try:
        response = await _get(async_get_clientsession(hass), app_url)
    except DialConnectionError:
        return None
    if response.status != 200:
        return None
    state = _tag(await response.text(), "state")
    return state.lower() if state else None


async def async_launch_app(
    hass: HomeAssistant, app_url: str, video_id: str | None = None
) -> str | None:
    """Open the YouTube app on the TV, optionally playing a video.

    The TV may ask the viewer to allow the remote device the first time.
    Returns the URL of the running app, used to stop it again.
    """
    data = {"v": video_id, "t": "0"} if video_id else None
    session = async_get_clientsession(hass)
    try:
        response = await session.post(
            app_url,
            data=data,
            headers={"Origin": ORIGIN},
            timeout=aiohttp.ClientTimeout(total=DIAL_TIMEOUT),
        )
        await response.read()
    except (aiohttp.ClientError, TimeoutError) as err:
        raise DialConnectionError(f"Error launching {app_url}: {err}") from err
    if response.status not in (200, 201):
        raise DialError(f"{app_url} returned HTTP {response.status} on launch")
    return response.headers.get("LOCATION")


async def async_stop_app(hass: HomeAssistant, run_url: str) -> None:
    """Close the YouTube app on the TV."""
    session = async_get_clientsession(hass)
    try:
        response = await session.delete(
            run_url,
            headers={"Origin": ORIGIN},
            timeout=aiohttp.ClientTimeout(total=DIAL_TIMEOUT),
        )
        await response.read()
    except (aiohttp.ClientError, TimeoutError) as err:
        raise DialConnectionError(f"Error stopping {run_url}: {err}") from err
    if response.status not in (200, 204):
        raise DialError(f"{run_url} returned HTTP {response.status} on stop")


async def async_get_run_url(hass: HomeAssistant, app_url: str) -> str | None:
    """Return the URL of the running app, as the TV reports it."""
    try:
        response = await _get(async_get_clientsession(hass), app_url)
    except DialConnectionError:
        return None
    if response.status != 200:
        return None
    match = re.search(r'<link[^>]*rel="run"[^>]*href="([^"]+)"', await response.text())
    if not match:
        return None
    return urljoin(f"{app_url}/", match.group(1))


async def _async_screen_from_app_url(
    session: aiohttp.ClientSession, app_url: str
) -> DialScreen:
    response = await _get(session, app_url)
    if response.status != 200:
        raise DialNoScreenError(f"{app_url} returned HTTP {response.status}")
    screen_id = _tag(await response.text(), "screenId")
    if not screen_id:
        raise DialNoScreenError(f"{app_url} has no screenId")
    return DialScreen(app_url=app_url, screen_id=screen_id)


async def async_get_screen_from_location(
    hass: HomeAssistant, location: str
) -> DialScreen:
    """Get the YouTube screen from a DIAL device description URL."""
    session = async_get_clientsession(hass)
    response = await _get(session, location)
    if response.status != 200:
        raise DialConnectionError(f"{location} returned HTTP {response.status}")
    application_url = response.headers.get("Application-URL")
    if not application_url:
        raise DialNoScreenError(f"{location} is not a DIAL server")

    description = await response.text()

    if not application_url.endswith("/"):
        application_url += "/"
    screen = await _async_screen_from_app_url(session, f"{application_url}YouTube")
    return DialScreen(
        app_url=screen.app_url,
        screen_id=screen.screen_id,
        name=_tag(description, "friendlyName"),
        udn=_tag(description, "UDN"),
        manufacturer=_tag(description, "manufacturer"),
        model=_tag(description, "modelName"),
    )


async def async_get_screen_from_host(hass: HomeAssistant, host: str) -> DialScreen:
    """Get the YouTube screen of a TV given only its host name or IP address."""
    for discovery in await ssdp.async_get_discovery_info_by_st(hass, DIAL_ST):
        if discovery.ssdp_location and _host_of(discovery.ssdp_location) == host:
            return await async_get_screen_from_location(hass, discovery.ssdp_location)

    error: DialError = DialConnectionError(f"No DIAL server found on {host}")
    for template in KNOWN_DESCRIPTION_URLS:
        try:
            return await async_get_screen_from_location(
                hass, template.format(host=host)
            )
        except DialNoScreenError as err:
            error = err
        except DialConnectionError:
            continue

    session = async_get_clientsession(hass)
    for template in KNOWN_APP_URLS:
        try:
            return await _async_screen_from_app_url(session, template.format(host=host))
        except DialNoScreenError as err:
            error = err
        except DialConnectionError:
            continue
    raise error


def _host_of(url: str) -> str | None:
    return urlparse(url).hostname
