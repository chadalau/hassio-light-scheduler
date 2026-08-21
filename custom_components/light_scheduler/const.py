"""Constants for Light Scheduler."""
from homeassistant.const import Platform

DOMAIN = "light_scheduler"
NAME = "Light Scheduler"

CONF_NAME = "name"
CONF_ENABLED = "enabled"
CONF_TARGET_ENTITY_IDS = "target_entity_ids"
CONF_POWER_ENTITY_IDS = "power_entity_ids"
CONF_ENTITY_MAPPINGS = "entity_mappings"
CONF_DEFAULT_DURATION = "default_duration"
CONF_MAX_DURATION = "max_duration"
CONF_SCHEDULES = "schedules"
CONF_SCHEDULE_ID = "id"
CONF_SCHEDULE_TIME = "time"
CONF_SCHEDULE_DAYS = "days"
CONF_SCHEDULE_DURATION = "duration"
CONF_SCHEDULE_INTERVAL = "interval"
CONF_SCHEDULE_WARNING = "warning"
CONF_POWER_THRESHOLD = "power_threshold_w"

DEFAULT_DEFAULT_DURATION = 14400
DEFAULT_MAX_DURATION = 86400
MIN_DURATION = 1
MAX_SCHEDULE_DURATION = 86400
MAX_SCHEDULE_INTERVAL = 300
HISTORY_RETENTION_DAYS = 30
HISTORY_MAX_ENTRIES = 200
ACTUATION_GRACE = 15
# Typical idle/standby draw is well under 1 W; anything above is treated as "on".
# Overridable per entry: a sub-watt LED strip would never confirm turning on
# against a fixed global threshold.
DEFAULT_POWER_THRESHOLD_W = 1.0
MAX_POWER_THRESHOLD_W = 10000.0
# A device integration that never returns from its service call would
# otherwise hang the stop sequence, the finish timer and unload forever.
SERVICE_CALL_TIMEOUT = 30
# Reason codes persisted on a schedule the integration had to disable on its
# own, so the card can explain why a row stopped running.
WARNING_TARGETS_REMOVED = "targets_removed"
WARNING_AMBIGUOUS_TIME = "ambiguous_time"

SOURCE_SCHEDULE = "schedule"
SOURCE_MANUAL = "manual"
SOURCE_EXTERNAL = "external"

SERVICE_TURN_ON_NOW = "turn_on_now"
SERVICE_STOP = "stop"
SERVICE_ADD_SCHEDULE = "add_schedule"
SERVICE_UPDATE_SCHEDULE = "update_schedule"
SERVICE_REMOVE_SCHEDULE = "remove_schedule"
SERVICE_SET_SCHEDULES = "set_schedules"
SERVICE_SET_ZONE_OPTIONS = "set_zone_options"

SIGNAL_UPDATE = "light_scheduler_update_{entry_id}"
STORE_KEY = "light_scheduler.runtime"
STORE_VERSION = 1
CARD_JS_URL = "/light_scheduler/card.js"
CARD_JS_FILENAME = "light-schedule-card.js"

PLATFORMS = [Platform.SWITCH, Platform.SENSOR, Platform.BINARY_SENSOR]
