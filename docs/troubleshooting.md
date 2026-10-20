# Troubleshooting

## Shorts won't play

Expected: the TV refuses Shorts while a device is connected to it. Turn off the **Remote session** switch, watch, and turn it back on. See [Shorts](behavior.md#shorts).

## Re-authentication requested

YouTube occasionally rotates a TV's screen id; it did so for every TV in April 2026. Open YouTube on the TV, then select **Reconfigure** on the integration in *Settings → Devices & services*. A TV added with a code asks for a new code.

## The TV wasn't discovered

Discovery needs Home Assistant and the TV on the same network segment, with YouTube having been opened on the TV at least once.

Android TV devices (Shield, Onn, Mi Box) don't publish their YouTube app over DIAL at all, so neither discovery nor adding by IP address finds them. Add them with a TV code. See [supported devices](../README.md#supported-devices).

## "Failed to connect" when adding with a TV code

Codes expire after a few minutes. Open YouTube → *Settings* → *Link with TV code* on the TV for a fresh one. If the code was rejected, the integration says so; "failed to connect" means Home Assistant couldn't reach YouTube's servers.

## Entities went unavailable for a while

The integration keeps retrying on its own, waiting a little longer after each attempt, up to five minutes, so these usually clear up by themselves.

The **YouTube session** sensor's history shows exactly when the connection dropped and came back. A session that stays down for more than five minutes is logged as a warning naming the reason, with a matching message when it reconnects.

## The ad sensor or skip button behaves oddly

Ad reporting differs between TVs. Turn on debug logging, let an ad play, and the log will show every event the TV sent, which is what a report needs:

```yaml
logger:
  logs:
    custom_components.youtube_on_tv: debug
```

Add `pyytlounge: debug` as well to see events the library doesn't handle. That also logs the session token, so remove the token before sharing the log.

## Removing the integration

1. In *Settings → Devices & services → YouTube on TV*, select **⋮ → Delete** on each TV.
2. If you installed through HACS, remove **YouTube on TV** there; otherwise delete `config/custom_components/youtube_on_tv/`.
3. Restart Home Assistant.

The TV's YouTube app may still list Home Assistant as a linked device. Where the app's settings offer to unlink devices, that removes it.

## Reporting a problem

[Open an issue](https://github.com/jorgediez/ha-youtube-on-tv/issues/new/choose) with the integration's diagnostics (*Settings → Devices & services → YouTube on TV → ⋮ → Download diagnostics*), and the debug log above if the problem involves playback. The diagnostics leave out the screen id, the TV's address and what was being watched. Questions and device reports are welcome in the [community thread](https://community.home-assistant.io/t/youtube-on-tv-see-and-control-what-the-youtube-app-on-your-tv-is-playing/1026146).

A security problem should not go in an issue or the thread: see [SECURITY.md](../SECURITY.md) for how to report it privately.
