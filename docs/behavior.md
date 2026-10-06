# How it behaves

The TV pushes everything the integration knows, over YouTube's Lounge protocol. Nothing is polled from YouTube, so most of what follows comes from how the TV reports things rather than from choices in the integration.

## Media player states

| Situation | State |
|---|---|
| Video playing (or an ad) | `playing` |
| Video paused | `paused` |
| Loading, seeking, between ad and video | `buffering` (only if it lasts more than 1.5 s) |
| YouTube open, nothing playing | `idle` |
| YouTube closed or sent to background, TV off | `off` |
| Can't reach YouTube's servers, or the session is turned off | `unavailable` |

## Shorts

The TV won't play Shorts while any device is connected to it as a remote — a phone that's casting does the same thing. Since this integration stays connected, the TV says Shorts can't play while a device is connected, and its own disconnect button doesn't help, because the integration reconnects right away.

Turn off the **Remote session** switch to watch Shorts, and on again afterwards. While it's off, the TV sees no connected device, the other entities are unavailable, and the setting survives a restart. See [taking a Shorts break](automations.md#taking-a-shorts-break).

## Ads

The **Skip ad** button does what pressing skip on the remote does: it works once YouTube offers the skip, normally after five seconds, and only for ads that are skippable at all. Bumpers and other unskippable ads play in full, so the **Ad playing** sensor is the one to use for muting them.

## Playback details

- While an ad plays, the position and duration are hidden, because the TV reports the ad's rather than the video's.
- Titles and channel names come from YouTube's public oEmbed endpoint, so no API key is needed.
- The TV may show its "Who's watching?" profile picker when Home Assistant links to it, which happens when the integration starts and again whenever YouTube expires the session token. So a restart can put the picker on the TV, interrupting what's playing. Nothing is wrong; pick a profile and playback carries on.
- The TV sends nothing when YouTube drops to its "Who's watching?" or home screen mid-video. To catch that, the integration asks the TV what's playing once a minute while a video plays, and the player goes `idle` within about 70 seconds if there's no answer. It also goes `idle` if the position runs more than 30 seconds past the end of the video.
- If the TV was added by discovery or IP address, the integration also asks it every 30 seconds whether YouTube is running. That catches the app closing or the TV turning off, which the session doesn't always report.
- A command sent while YouTube is closed is accepted by YouTube's servers and never reaches the TV, which is why playing a video opens the app instead.

## Queue

- The queue commands (`addVideo`, `insertVideo`, `setPlaylist`) are the ones the YouTube phone app sends. There is no command to move or remove one video, so every change to a known queue sends the whole queue again with the playing video and its position; the TV carries on without a visible gap.
- `addVideo` and `insertVideo` are only used when the TV reports no queue, and the TV is asked for it first, with 3 seconds to answer. They only work on a queue sent from here: against the list a TV builds for playback started with its own remote, a Samsung Tizen TV either played the added video at once or dropped it silently.
- Not every now playing report carries the queue, so there may be none known shortly after connecting. A debug log says which way a video was added: "Sending the queue" or "Queue unknown".
- The TV reports the queue in its now playing updates, but not the position in it, so a video queued twice is matched by the position it had last.
- After a change from Home Assistant the TV keeps reporting the old queue for a few seconds. Reports that don't match the change are ignored for up to 5 seconds, so the list doesn't jump back and forth.
- A video can play with no queue reported, for example after the session dropped and came back. Adding a video or playing one next still goes to the TV's own queue, and the integration asks the TV for it, so the playing video isn't replaced.
- Queue titles are looked up four at a time, nearest to the playing video first. A video oEmbed doesn't know (private or removed) shows its id; a lookup that failed for another reason is tried again on the next queue report.

## Settings

- Autoplay, playback speed and subtitles are known only once the TV reports them, so they show as unknown until then. The TV reports them when they change, so they're kept across videos and while playback is stopped. Up next belongs to the current video and is cleared with it.
- Changing autoplay or the playback speed from Home Assistant updates the entity at once, because the TV can take a few seconds to apply the change and report it back. If the TV then reports something different, its value wins.

## What the protocol doesn't offer

- **Volume:** YouTube reports its own internal volume, not the TV's. Use your TV's integration, such as [Samsung Smart TV](https://www.home-assistant.io/integrations/samsungtv/).
- **"Stats for nerds" and subtitle appearance:** no commands exist, so those stay on the TV's own remote.
- **Browsing or searching:** the protocol plays a video you already know the id of; it can't search YouTube.
