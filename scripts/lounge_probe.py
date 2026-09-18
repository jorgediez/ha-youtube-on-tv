"""Feasibility probe: listen to a TV's YouTube Lounge session without manual pairing.

Steps:
  1. Read the screenId from the TV's DIAL endpoint (YouTube app info).
  2. Exchange it for a lounge token (pair_with_screen_id).
  3. Connect as a remote and print now-playing / state events for a while.

The screenId and lounge token are credentials, so they are never printed.

Usage: python scripts/lounge_probe.py --host TV_IP [--seconds 120]
"""

import argparse
import asyncio
import logging
import re
import sys
import time

import aiohttp
from pyytlounge import (
    AdPlayingEvent,
    AdStateEvent,
    DisconnectedEvent,
    EventListener,
    NowPlayingEvent,
    PlaybackSpeedEvent,
    PlaybackStateEvent,
    VolumeChangedEvent,
    YtLoungeApi,
)

DIAL_PORT = 8080
DEVICE_NAME = "HA Lounge Probe"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


async def get_screen_id(session: aiohttp.ClientSession, host: str) -> str:
    url = f"http://{host}:{DIAL_PORT}/ws/app/YouTube"
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
        resp.raise_for_status()
        body = await resp.text()
    state = re.search(r"<state>(.*?)</state>", body)
    log(f"DIAL: YouTube app state = {state.group(1) if state else '?'}")
    match = re.search(r"<screenId>(.*?)</screenId>", body)
    if not match:
        raise RuntimeError("DIAL response has no screenId (is the YouTube app open?)")
    return match.group(1)


class TitleCache:
    """Resolves video titles through YouTube oEmbed (no API key needed)."""

    def __init__(self, session: aiohttp.ClientSession):
        self._session = session
        self._cache: dict[str, str] = {}

    async def get(self, video_id: str) -> str:
        if video_id not in self._cache:
            url = "https://www.youtube.com/oembed"
            params = {
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "format": "json",
            }
            try:
                async with self._session.get(url, params=params) as resp:
                    data = await resp.json(content_type=None)
                    self._cache[video_id] = f"{data['title']} — {data['author_name']}"
            except Exception as err:
                self._cache[video_id] = f"<title lookup failed: {err}>"
        return self._cache[video_id]


def fmt_time(seconds: float | None) -> str:
    if seconds is None:
        return "--:--"
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


class PrintListener(EventListener):
    def __init__(self, titles: TitleCache):
        super().__init__()
        self._titles = titles

    async def now_playing_changed(self, event: NowPlayingEvent) -> None:
        title = await self._titles.get(event.video_id) if event.video_id else "-"
        log(
            f"NOW PLAYING  video={event.video_id} state={event.state.name} "
            f"pos={fmt_time(event.current_time)}/{fmt_time(event.duration)}  {title}"
        )

    async def playback_state_changed(self, event: PlaybackStateEvent) -> None:
        log(
            f"STATE        {event.state.name} "
            f"pos={fmt_time(event.current_time)}/{fmt_time(event.duration)}"
        )

    async def volume_changed(self, event: VolumeChangedEvent) -> None:
        log(f"VOLUME       {event.volume} muted={event.muted}")

    async def ad_state_changed(self, event: AdStateEvent) -> None:
        log(f"AD STATE     {vars(event)}")

    async def ad_playing_changed(self, event: AdPlayingEvent) -> None:
        log(f"AD PLAYING   {getattr(event, 'ad_title', '')}")

    async def playback_speed_changed(self, event: PlaybackSpeedEvent) -> None:
        log(f"SPEED        {vars(event)}")

    async def disconnected(self, event: DisconnectedEvent) -> None:
        log(f"DISCONNECTED {vars(event)}")


async def listen(api: YtLoungeApi, screen_id: str) -> None:
    """Keep the lounge session alive: refresh auth, reconnect, re-subscribe."""
    while True:
        if not api.linked():
            log("Linking with screenId ...")
            if not await api.pair_with_screen_id(screen_id):
                raise RuntimeError("get_lounge_token_batch did not return a token")
            log("Linked (lounge token obtained)")
        if not api.connected():
            log("Connecting ...")
            if not await api.connect():
                log("Connect failed, retrying in 5s")
                await asyncio.sleep(5)
                continue
            log(f"Connected to screen '{api.screen_name}' ({api.screen_device_name})")
            await api.get_now_playing()
        await api.subscribe()  # long-poll; returns when the server ends the request


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True, help="TV IP address or hostname")
    parser.add_argument("--seconds", type=int, default=120)
    args = parser.parse_args()

    # pyytlounge logs the lounge token at INFO, so keep its logger at WARNING.
    lib_logger = logging.getLogger("pyytlounge")
    lib_logger.setLevel(logging.WARNING)
    logging.basicConfig(format="[lib] %(levelname)s %(message)s")

    async with aiohttp.ClientSession() as session:
        screen_id = await get_screen_id(session, args.host)
        log(f"DIAL: screenId found ({len(screen_id)} chars)")

        async with YtLoungeApi(
            DEVICE_NAME, PrintListener(TitleCache(session)), lib_logger
        ) as api:
            log(f"Listening for {args.seconds}s — play/pause/seek from your phone now")
            try:
                await asyncio.wait_for(listen(api, screen_id), timeout=args.seconds)
            except TimeoutError:
                log("Time is up")
            finally:
                if api.connected():
                    await api.disconnect()
                    log("Disconnected cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
