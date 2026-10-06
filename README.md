# YouTube on TV

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Validate](https://github.com/jorgediez/ha-youtube-on-tv/actions/workflows/validate.yml/badge.svg)](https://github.com/jorgediez/ha-youtube-on-tv/actions/workflows/validate.yml)
[![Tests](https://github.com/jorgediez/ha-youtube-on-tv/actions/workflows/tests.yml/badge.svg)](https://github.com/jorgediez/ha-youtube-on-tv/actions/workflows/tests.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

A Home Assistant integration that shows what the **YouTube app on your TV** is playing — video, title, channel, thumbnail, play/pause state and position — and lets you control playback.

It connects to the TV the same way the YouTube phone app does when you cast (YouTube's "Lounge" protocol, via [pyytlounge](https://github.com/FabioGNR/pyytlounge)). Updates are pushed by the TV instantly; nothing is polled from YouTube, and no Google account or API key is needed.

> **Not affiliated with Google or YouTube.** This uses an undocumented protocol that YouTube may change at any time.

Questions, ideas and reports of how it behaves on other TVs are welcome in the [Home Assistant community thread](https://community.home-assistant.io/t/youtube-on-tv-see-and-control-what-the-youtube-app-on-your-tv-is-playing/1026146). Bugs are best filed as [issues](https://github.com/jorgediez/ha-youtube-on-tv/issues), and [CONTRIBUTING](https://github.com/jorgediez/ha-youtube-on-tv/blob/main/CONTRIBUTING.md) says what makes a report useful and what belongs in the integration.

## Features

Each TV becomes a device with:

- **Media player** — state, video, title, channel, thumbnail, duration and position; play, pause, seek, next, previous, turn on/off, and playing any video by id or YouTube URL
- **Queue** to-do list mirroring the TV's play queue: paste a link to add a video, drag to reorder, delete to remove; the media player can also add to the end, play next or replace the queue
- **Ad playing** sensor and **Skip ad** button, for muting or skipping ads automatically
- **Autoplay**, **Subtitles** and **Remote session** switches, and a **Playback speed** select
- **Up next**, **Subtitles** and **Video quality** sensors
- **YouTube session** and **App state** diagnostics

It works whether playback was started from a phone or with the TV remote. See [all entities](https://github.com/jorgediez/ha-youtube-on-tv/blob/main/docs/entities.md) and [automation examples](https://github.com/jorgediez/ha-youtube-on-tv/blob/main/docs/automations.md).

## Supported devices

Any device whose YouTube app supports "Link with TV code" should work.

| Device | Reported | Thanks to |
|---|---|---|
| **Samsung Tizen** (Neo QLED QN900F) | Developed against it: discovery, IP address and TV code all work | |
| **Samsung Tizen** (other model) | Works, including Skip ad | [Nik_Fiend](https://community.home-assistant.io/u/nik_fiend) |
| **Onn 4K Android TV box** | Works, including Skip ad. Added with a TV code; discovery didn't find it | [j_quadrifrons](https://community.home-assistant.io/u/j_quadrifrons) |
| **NVIDIA Shield TV** | Playback, title, channel and thumbnail all shown. Added with a TV code; adding by IP address didn't work. Ads untested (YouTube Premium) | [kahilzinger](https://community.home-assistant.io/u/kahilzinger) |
| **Apple TV 4K** | Playback, controls and the queue all work. Added with a TV code: it publishes no DIAL at all, and its YouTube app closes whenever another app comes to the front | [davbebawy](https://github.com/davbebawy) |

LG webOS, Chromecast with Google TV, Fire TV and Roku use the same protocol but haven't been reported on yet.

**Android TV devices and the Apple TV need a TV code.** A Xiaomi Mi Box was checked with `scripts/dial_scan.py`: it publishes DIAL, but its DIAL server registers no YouTube app even while YouTube is playing, so there's no screen id to discover and no local address to use. The Shield and Onn behaved the same way, and an Apple TV publishes no DIAL at all. Everything works once added with a code, except what needs the TV's address: the **App state** sensor, and closing YouTube. Such a TV can still open YouTube, and play a video while the app is closed, through [open actions](https://github.com/jorgediez/ha-youtube-on-tv/blob/main/docs/automations.md#open-youtube-on-a-tv-added-with-a-code) you configure for it.

YouTube Kids is not supported.

## Installation

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jorgediez&repository=ha-youtube-on-tv&category=integration)

1. In HACS, open the menu (⋮) → **Custom repositories**, add `https://github.com/jorgediez/ha-youtube-on-tv` with type **Integration**.
2. Download **YouTube on TV** and restart Home Assistant.

Or copy `custom_components/youtube_on_tv` into your Home Assistant `config/custom_components/` folder and restart. Requires Home Assistant 2026.3 or newer.

## Setup

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=youtube_on_tv)

- **Discovered TVs** appear under *Settings → Devices & services*. Open YouTube on the TV once so it's found, then select **Add**.
- **Enter the TV's IP address:** open YouTube on the TV first.
- **Use a TV code:** needed for Android TV devices, and for a TV on another network. On the TV, open YouTube → *Settings* → *Link with TV code*, and enter the code shown there.

Each TV becomes its own device named **YouTube on _TV name_**, so its entities (`media_player.youtube_on_samsung_neo_qled` and friends) don't clash with the TV's own integration.

## Documentation

- [Entities](https://github.com/jorgediez/ha-youtube-on-tv/blob/main/docs/entities.md) — everything the integration creates, and what each one reports
- [Automations](https://github.com/jorgediez/ha-youtube-on-tv/blob/main/docs/automations.md) — skipping and muting ads, playing a video, taking a Shorts break
- [How it behaves](https://github.com/jorgediez/ha-youtube-on-tv/blob/main/docs/behavior.md) — player states, Shorts, and what the protocol doesn't offer
- [Troubleshooting](https://github.com/jorgediez/ha-youtube-on-tv/blob/main/docs/troubleshooting.md) — re-authentication, discovery, outages, debug logs
- [Development](https://github.com/jorgediez/ha-youtube-on-tv/blob/main/docs/development.md) — tests, and the diagnostic scripts for a new device

## Privacy

- The TV's screen id is stored in Home Assistant. It works like a password for controlling the TV's YouTube app, so it's redacted from diagnostics.
- The integration talks to `www.youtube.com` (the Lounge API and oEmbed) and `i.ytimg.com` (thumbnails).

## Credits

- [pyytlounge](https://github.com/FabioGNR/pyytlounge) by FabioGNR, which implements the Lounge protocol
- [iSponsorBlockTV](https://github.com/dmunozv04/iSponsorBlockTV), whose work on discovery through DIAL made pairing without a code possible
- Everyone testing it on their own hardware in the [community thread](https://community.home-assistant.io/t/youtube-on-tv-see-and-control-what-the-youtube-app-on-your-tv-is-playing/1026146), which is how devices beyond one Samsung TV got covered

## License

[GPL-3.0](LICENSE)
