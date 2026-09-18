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

DIAL_TIMEOUT: Final = 5
DIAL_ST: Final = "urn:dial-multiscreen-org:service:dial:1"
