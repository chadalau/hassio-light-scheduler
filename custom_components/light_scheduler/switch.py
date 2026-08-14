"""Schedule enabled switch."""
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from .const import SIGNAL_UPDATE

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    async_add_entities([ScheduleEnabledSwitch(entry.runtime_data, entry)])

class ScheduleEnabledSwitch(SwitchEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "schedule_enabled"
    _attr_should_poll = False
    def __init__(self, scheduler, entry):
        self.scheduler, self.entry = scheduler, entry
        self._attr_unique_id = f"{entry.entry_id}_schedule_enabled"
        self._attr_device_info = {"identifiers": {(entry.domain, entry.entry_id)}, "name": entry.title}
    @property
    def is_on(self): return self.scheduler.enabled
    async def async_turn_on(self, **kwargs): await self.scheduler.async_set_enabled(True)
    async def async_turn_off(self, **kwargs): await self.scheduler.async_set_enabled(False)
    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATE.format(entry_id=self.entry.entry_id), self.async_write_ha_state))
