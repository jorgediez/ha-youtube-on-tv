"""Constants for the YouTube on TV integration."""

from datetime import timedelta
import logging
from typing import Final

DOMAIN: Final = "youtube_on_tv"
LOGGER = logging.getLogger(__package__)

CONF_SCREEN_ID: Final = "screen_id"
CONF_APP_URL: Final = "app_url"
CONF_PAIRING_CODE: Final = "pairing_code"
CONF_MANUFACTURER: Final = "manufacturer"
CONF_MODEL: Final = "model"
# Entry option: whether the TV should see Home Assistant as a connected
# remote. A connected remote stops the TV playing Shorts.
CONF_SESSION_ENABLED: Final = "session_enabled"

# Name shown on the TV in the list of connected devices.
LOUNGE_DEVICE_NAME: Final = "Home Assistant"

# Seconds a transient playback state (seek, buffering, ad transition) must
# hold before it is published, to avoid flickering entity states.
SETTLE_DELAY: Final = 1.5

# Positions reported within this many seconds of the extrapolated position
# are treated as unchanged, so event bursts don't cause state writes.
POSITION_TOLERANCE: Final = 2.0

RECONNECT_MIN_DELAY: Final = 5
RECONNECT_MAX_DELAY: Final = 300

# A session that stays down this long is worth one warning in the log, so a
# silent gap can be explained afterwards without debug logging.
OUTAGE_WARNING_DELAY: Final = timedelta(minutes=5)

# How often the TV's DIAL endpoint is polled to detect the app closing or the
# TV going to standby, which the Lounge session doesn't always report.
APP_STATE_INTERVAL: Final = timedelta(seconds=30)

# The TV sends no event when YouTube drops to its profile picker or home
# screen mid-video. While playing, it's asked what's playing this often; no
# answer within STALE_REPLY_TIMEOUT seconds means the player has stopped.
STALE_CHECK_INTERVAL: Final = timedelta(seconds=60)
STALE_REPLY_TIMEOUT: Final = 10

# A playing video whose extrapolated position is this many seconds past its
# end is assumed to have stopped.
POSITION_OVERRUN: Final = 30

# Option of a TV entry: actions that open YouTube on the TV, for TVs without
# DIAL (added with a TV code), e.g. wake a streaming box and pick the app.
# The Lounge protocol can't start the app itself.
CONF_OPEN_ACTIONS: Final = "open_actions"
# Seconds to wait for the TV's YouTube app to come online after the open
# actions ran: waking the box, starting the app and its Lounge session.
OPEN_TIMEOUT: Final = 45

DIAL_TIMEOUT: Final = 5
DIAL_ST: Final = "urn:dial-multiscreen-org:service:dial:1"
