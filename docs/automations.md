# Automations and actions

Entity ids below assume a TV named "Samsung Neo QLED"; use your own. The full list is in [Entities](entities.md).

## Skip ads

The Skip ad button is unavailable until the TV allows skipping, so becoming available is the trigger:

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

This presses skip as soon as the TV offers it, usually five seconds in. It can't do more than the TV's own remote: an ad that YouTube doesn't let you skip still plays in full, so this isn't a replacement for Premium. Muting, below, covers those.

## Mute ads

This also covers ads that can't be skipped. Volume belongs to the TV, so this uses the TV's own media player entity, not this integration's. Thanks to [Nik_Fiend](https://community.home-assistant.io/u/nik_fiend) for [the idea](https://community.home-assistant.io/t/youtube-on-tv-see-and-control-what-the-youtube-app-on-your-tv-is-playing/1026146/8):

```yaml
automation:
  - alias: Mute YouTube ads
    triggers:
      - trigger: state
        entity_id: binary_sensor.youtube_on_samsung_neo_qled_ad_playing
        to: "on"
        id: mute
      - trigger: state
        entity_id: binary_sensor.youtube_on_samsung_neo_qled_ad_playing
        to: "off"
        id: unmute
    actions:
      - action: media_player.volume_mute
        target:
          entity_id: media_player.samsung_neo_qled   # your TV, not youtube_on_*
        data:
          is_volume_muted: "{{ trigger.id == 'mute' }}"
```

## Play a video

`media_player.play_media` takes a video id or any YouTube video URL (`youtube.com/watch`, `youtu.be`, Shorts or live links):

```yaml
action: media_player.play_media
target:
  entity_id: media_player.youtube_on_samsung_neo_qled
data:
  media_content_type: video
  media_content_id: https://www.youtube.com/watch?v=dQw4w9WgXcQ
```

It works while YouTube is open, including switching from a video that's already playing. If YouTube is closed, the integration opens it on the TV with that video. **The first time a device does that, the TV asks you to allow it**, so confirm on the TV once.

Turning the media player on or off opens and closes YouTube on the TV the same way. Both need the TV's address, so they aren't available for TVs added with a TV code.

## Queue videos

`media_player.play_media` takes Home Assistant's standard `enqueue` option:

| `enqueue` | Effect |
|---|---|
| `add` | Appends the video to the end of the queue |
| `next` | Plays the video after the current one |
| `replace` | Replaces the whole queue with the video |
| `play` or none | Plays the video now |

```yaml
action: media_player.play_media
target:
  entity_id: media_player.youtube_on_samsung_neo_qled
data:
  media_content_type: video
  media_content_id: https://youtu.be/dQw4w9WgXcQ
  enqueue: next
```

With nothing playing, `add` and `next` play the video now.

The **Queue** to-do list shows the same queue, including videos queued from a phone. Played videos show as completed. In the list:

- **Add** a YouTube link or video id to append it (or play it, if nothing plays).
- **Drag** a video to reorder the queue. The playing video keeps playing.
- **Delete** a video to remove it from the queue.

The playing video can't be moved or deleted, and a video dragged above it plays next instead. Reordering and deleting need a video playing, because the TV reloads the queue at the current position.

## Taking a Shorts break

The TV won't play Shorts while Home Assistant is connected to it; see [Shorts](behavior.md#shorts). The **Remote session** switch disconnects on demand.

A dashboard button to disconnect and reconnect:

```yaml
type: button
name: YouTube remote
icon: mdi:cast-connected
tap_action:
  action: perform-action
  perform_action: switch.toggle
  target:
    entity_id: switch.youtube_on_samsung_neo_qled_remote_session
```

To reconnect by itself after a while, so an evening of Shorts doesn't leave Home Assistant disconnected. The trigger measures how long the switch has been off, so a restart doesn't lose track:

```yaml
automation:
  - alias: Reconnect YouTube on TV after a Shorts break
    triggers:
      - trigger: state
        entity_id: switch.youtube_on_samsung_neo_qled_remote_session
        to: "off"
        for: "00:15:00"
    actions:
      - action: switch.turn_on
        target:
          entity_id: switch.youtube_on_samsung_neo_qled_remote_session
```

Or disconnect for a fixed time in one go, as a script:

```yaml
script:
  shorts_break:
    alias: Shorts break
    sequence:
      - action: switch.turn_off
        target:
          entity_id: switch.youtube_on_samsung_neo_qled_remote_session
      - delay: "00:15:00"
      - action: switch.turn_on
        target:
          entity_id: switch.youtube_on_samsung_neo_qled_remote_session
```

A script's `delay` is lost if Home Assistant restarts mid-break, which is why the automation above is the sturdier option.
