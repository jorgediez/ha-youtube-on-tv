# Entities

Each TV becomes its own device, named **YouTube on _TV name_**, so it's easy to tell apart from the TV's own device. For a TV named "Samsung Neo QLED", the device has these entities.

## Playback

| Entity | Description |
|---|---|
| `media_player.youtube_on_samsung_neo_qled` | What's playing: state, video id, title, channel, thumbnail, duration and position. Supports play, pause, seek, next, previous, turn on/off, and playing or queueing a video. |
| `todo.youtube_on_samsung_neo_qled_queue` | The TV's play queue, in order. Played videos show as completed, the playing one says "Now playing". Add a link to queue it, drag to reorder, delete to remove; see [Queue videos](automations.md#queue-videos). |
| `sensor.youtube_on_samsung_neo_qled_video_quality` | Resolution being played, e.g. 1080, with an `available_levels` attribute |
| `sensor.youtube_on_samsung_neo_qled_up_next` | Title of the video autoplay plays next, with its `video_id` and thumbnail. Only set when the TV announces one, which some TVs rarely do. |

## Ads

| Entity | Description |
|---|---|
| `binary_sensor.youtube_on_samsung_neo_qled_ad_playing` | On while an ad plays, with a `skippable` attribute |
| `button.youtube_on_samsung_neo_qled_skip_ad` | Skips the ad. Unavailable until the TV allows skipping, which is what makes it useful as an automation trigger. |

## Settings

| Entity | Description |
|---|---|
| `switch.youtube_on_samsung_neo_qled_autoplay` | YouTube's autoplay setting |
| `switch.youtube_on_samsung_neo_qled_subtitles` | Turns subtitles off, or back on in the last language used |
| `sensor.youtube_on_samsung_neo_qled_subtitles` | Subtitles language, or `off`; kept across videos. Attributes: `language_code`, `track_name`, `kind` (`asr` means auto-generated) and the TV's display `style`. |
| `select.youtube_on_samsung_neo_qled_playback_speed` | 0.25× to 2× |
| `switch.youtube_on_samsung_neo_qled_remote_session` | Whether the TV sees Home Assistant as a connected remote. Turn it off to watch Shorts; see [Shorts](behavior.md#shorts). |

## Diagnostics

| Entity | Description |
|---|---|
| `binary_sensor.youtube_on_samsung_neo_qled_youtube_session` | The session with YouTube's servers. It stays on while the TV is off, so use **App state** to tell whether the TV is playing. |
| `sensor.youtube_on_samsung_neo_qled_app_state` | Disabled by default: `running`, `stopped`, `hidden` or `unreachable`, as reported by the TV. Only for TVs added by discovery or IP address. |

The TV lists the connection as a linked device named "Home Assistant".

See also: [automation examples](automations.md), [how it behaves](behavior.md).
