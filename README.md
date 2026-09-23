# YouTube on TV

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Validate](https://github.com/jorgediez/ha-youtube-on-tv/actions/workflows/validate.yml/badge.svg)](https://github.com/jorgediez/ha-youtube-on-tv/actions/workflows/validate.yml)
[![Tests](https://github.com/jorgediez/ha-youtube-on-tv/actions/workflows/tests.yml/badge.svg)](https://github.com/jorgediez/ha-youtube-on-tv/actions/workflows/tests.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

A Home Assistant integration that shows what the **YouTube app on your TV** is playing — video, title, channel, thumbnail, play/pause state and position — and lets you control playback.

It connects to the TV the same way the YouTube phone app does when you cast (YouTube's "Lounge" protocol, via [pyytlounge](https://github.com/FabioGNR/pyytlounge)). Updates are pushed by the TV instantly; nothing is polled from YouTube, and no Google account or API key is needed.

> **Not affiliated with Google or YouTube.** This uses an undocumented protocol that YouTube may change at any time.

## Features

- **Media player** entity with:
  - State: `playing`, `paused`, `buffering`, `idle`, `off`
  - Video id, title, channel, thumbnail, duration and position
  - Play, pause, seek, next and previous
  - Play any video by id or YouTube URL, opening YouTube on the TV if it's closed
  - Turn on and off, which opens and closes YouTube on the TV
- **Ad playing** binary sensor, with a `skippable` attribute
- **Skip ad** button, available as soon as the ad can be skipped
- **Autoplay** and **Subtitles** switches, and a **Playback speed** select
- **Video quality** sensor
- **Up next** and **Subtitles** sensors
- Diagnostic entities for the connection and the YouTube app state
- Automatic discovery of TVs on your network (DIAL/SSDP), no pairing code needed
- Works whether playback was started from a phone or with the TV remote

## Supported devices

Any device whose YouTube app supports "Link with TV code" should work. It's developed against a **Samsung Tizen** TV (Neo QLED QN900F). LG webOS, Android/Google TV, Chromecast with Google TV, Fire TV and Roku use the same protocol, but they haven't been tested yet.

YouTube Kids is not supported.

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jorgediez&repository=ha-youtube-on-tv&category=integration)

1. In HACS, open the menu (⋮) → **Custom repositories**, add `https://github.com/jorgediez/ha-youtube-on-tv` with type **Integration**.
2. Download **YouTube on TV** and restart Home Assistant.

### Manual

Copy `custom_components/youtube_on_tv` into your Home Assistant `config/custom_components/` folder and restart.

Requires Home Assistant 2026.3 or newer.

## Setup

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=youtube_on_tv)

- **Discovered TVs** appear under *Settings → Devices & services*. Open YouTube on the TV once so it's found, then select **Add**.
- **Enter the TV's IP address:** open YouTube on the TV first.
- **Use a TV code:** use this when the TV is on another network or isn't discovered. On the TV, open YouTube → *Settings* → *Link with TV code*, and enter the code shown there.

Each TV becomes its own device, named **YouTube on _TV name_**, so it's easy to tell apart from the TV's own device. For a TV named "Samsung Neo QLED", the device has these entities:

| Entity | Description |
|---|---|
| `media_player.youtube_on_samsung_neo_qled` | What's playing, with playback controls |
| `binary_sensor.youtube_on_samsung_neo_qled_ad_playing` | On while an ad plays; `skippable` attribute |
| `button.youtube_on_samsung_neo_qled_skip_ad` | Skips the ad; unavailable until it can be skipped |
| `binary_sensor.youtube_on_samsung_neo_qled_youtube_session` | Diagnostic: the session with YouTube's servers. It stays on while the TV is off; use **App state** to tell whether the TV is playing. |
| `switch.youtube_on_samsung_neo_qled_autoplay` | YouTube's autoplay setting (config) |
| `select.youtube_on_samsung_neo_qled_playback_speed` | 0.25× to 2× |
| `sensor.youtube_on_samsung_neo_qled_up_next` | Title of the video autoplay plays next; `video_id` attribute and thumbnail. Only set when the TV announces one, which some TVs rarely do. |
| `switch.youtube_on_samsung_neo_qled_subtitles` | Turns subtitles off, or back on in the last language used |
| `sensor.youtube_on_samsung_neo_qled_subtitles` | Subtitles language, or `off`; kept across videos. Attributes: `language_code`, `track_name`, `kind` (`asr` means auto-generated) and the TV's display `style` |
| `sensor.youtube_on_samsung_neo_qled_video_quality` | Resolution being played, e.g. 1080, with an `available_levels` attribute |
| `sensor.youtube_on_samsung_neo_qled_app_state` | Diagnostic, disabled by default: `running`, `stopped`, `hidden` or `unreachable`, as reported by the TV. Only for TVs added by discovery or IP address. |

The TV will list the connection as a linked device named "Home Assistant".

## How it works

| Situation | Media player state |
|---|---|
| Video playing (or an ad) | `playing` |
| Video paused | `paused` |
| Loading, seeking, between ad and video | `buffering` (only if it lasts more than 1.5 s) |
| YouTube open, nothing playing | `idle` |
| YouTube closed or sent to background, TV off | `off` |
| Can't reach YouTube's servers | `unavailable` |

- While an ad plays, the position and duration are hidden, because the TV reports the ad's instead of the video's.
- Titles and channel names come from YouTube's public oEmbed endpoint.
- If the TV was set up by discovery or IP address, the integration also asks the TV every 30 seconds whether YouTube is running. This catches the app closing or the TV turning off, which the Lounge session doesn't always report.
- If YouTube leaves the video without saying so, the TV stops sending updates. This happens when it drops to the "Who's watching?" screen or the home screen. To catch it, the integration asks the TV what's playing once a minute while a video plays. If there's no answer, the player switches to `idle` within about 70 seconds. It also goes `idle` if the position runs more than 30 seconds past the end of the video.
- Autoplay, playback speed and subtitles are known only once the TV reports them, so they show as unknown until then. The TV reports them when they change, so they're kept across videos and while playback is stopped. Up next belongs to the current video and is cleared with it.
- Changing autoplay or the playback speed from Home Assistant updates the entity at once, because the TV can take a few seconds to apply the change and report it back. If the TV then reports something different, its value wins.
- The protocol has no way to toggle "stats for nerds" or to change how subtitles are displayed, so those stay on the TV's own remote.
- A Lounge command sent while YouTube is closed is accepted by YouTube's servers and never reaches the TV, which is why playing a video falls back to opening the app instead.
- Volume isn't exposed: YouTube reports its own internal volume, not the TV's. Use your TV's integration (e.g. [Samsung Smart TV](https://www.home-assistant.io/integrations/samsungtv/)) for that.

## Playing a video

Use `media_player.play_media` with a video id or any YouTube video URL (`youtube.com/watch`, `youtu.be`, Shorts or live links):

```yaml
action: media_player.play_media
target:
  entity_id: media_player.youtube_on_samsung_neo_qled
data:
  media_content_type: video
  media_content_id: https://www.youtube.com/watch?v=dQw4w9WgXcQ
```

It works while YouTube is open, including switching from a video that's already playing. If YouTube is closed, the integration opens it on the TV with the video, using DIAL. **The first time a device does that, the TV asks you to allow it**, so you have to confirm on the TV once.

Turning the media player on or off opens and closes YouTube on the TV the same way. Both need the TV's address, so they aren't available for TVs added with a TV code.

## Skipping ads automatically

```yaml
automation:
  - alias: Skip YouTube ads
    triggers:
      - trigger: state
        entity_id: button.youtube_on_samsung_neo_qled_skip_ad
        from: unavailable
    actions:
      - action: button.press
        target:
          entity_id: button.youtube_on_samsung_neo_qled_skip_ad
```

## Troubleshooting

- **Re-authentication requested:** YouTube occasionally rotates a TV's screen id (it did so for all TVs in April 2026). Open YouTube on the TV, then select **Reconfigure** on the integration in *Settings → Devices & services*. TVs added with a TV code ask for a new code.
- **Not discovered:** discovery needs Home Assistant and the TV on the same network segment, with YouTube having been opened on the TV. Add it by IP address or TV code instead.
- **Debug logs:** add this to `configuration.yaml`:

  ```yaml
  logger:
    logs:
      custom_components.youtube_on_tv: debug
      pyytlounge: debug
  ```

  At debug level, `pyytlounge` logs the session token. Remove it from logs before sharing them.

## Privacy

- The TV's screen id is stored in Home Assistant. It works like a password for controlling the TV's YouTube app, so it's redacted from diagnostics.
- The integration talks to `www.youtube.com` (the Lounge API and oEmbed) and `i.ytimg.com` (thumbnails).

## Development

```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/pytest --cov
```

The Home Assistant test harness only runs on Linux and macOS. On Windows, use WSL.

`scripts/lounge_probe.py` is a standalone tool that prints the Lounge events of a TV. It's useful for checking how a new device behaves:

```bash
.venv/bin/python scripts/lounge_probe.py --host 192.168.1.50 --seconds 300
```

It assumes a Samsung DIAL endpoint (port 8080).

## Credits

- [pyytlounge](https://github.com/FabioGNR/pyytlounge) by FabioGNR, which implements the Lounge protocol
- [iSponsorBlockTV](https://github.com/dmunozv04/iSponsorBlockTV), whose work on discovery through DIAL made pairing without a code possible

## License

[GPL-3.0](LICENSE)
