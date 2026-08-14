"""Active scheduling state."""
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from .const import SIGNAL_UPDATE

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    async_add_entities([LightScheduleActive(entry.runtime_data, entry)])

class LightScheduleActive(BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "active"
    _attr_should_poll = False
    def __init__(self, scheduler, entry):
        self.scheduler, self.entry = scheduler, entry
        self._attr_unique_id = f"{entry.entry_id}_active"
        self._attr_device_info = {"identifiers": {(entry.domain, entry.entry_id)}, "name": entry.title}
    @property
    def is_on(self): return self.scheduler.active
    @property
    def extra_state_attributes(self):
        return {
            "entry_id": self.entry.entry_id,
            "zone_name": self.entry.title,
            "started_at": self.scheduler.started_at.isoformat() if self.scheduler.started_at else None,
            "finishes_at": self.scheduler.finishes_at.isoformat() if self.scheduler.finishes_at else None,
            "source": self.scheduler.source,
            "stopping": self.scheduler.stopping,
            "history": self.scheduler.history,
        }
    async def async_added_to_hass(self): self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATE.format(entry_id=self.entry.entry_id), self.async_write_ha_state))
