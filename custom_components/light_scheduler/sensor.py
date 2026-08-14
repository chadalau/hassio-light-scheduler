"""Next action and live light/power data for the custom card."""
from __future__ import annotations
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_state_change_event
from .const import SIGNAL_UPDATE

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    async_add_entities([LightScheduleStatus(entry.runtime_data, entry)])

class LightScheduleStatus(SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "next_run"
    _attr_should_poll = False
    def __init__(self, scheduler, entry):
        self.scheduler, self.entry = scheduler, entry
        self._attr_unique_id = f"{entry.entry_id}_next_run"
        self._attr_device_info = {"identifiers": {(entry.domain, entry.entry_id)}, "name": entry.title}
    @property
    def native_value(self):
        next_run = self.scheduler.next_run
        return next_run.isoformat() if next_run else None
    @property
    def extra_state_attributes(self):
        targets, powers = self.scheduler.target_entity_ids, self.scheduler.power_entity_ids
        lights = []
        total = 0.0
        for index, entity_id in enumerate(targets):
            light = self.hass.states.get(entity_id)
            power_id = powers[index] if index < len(powers) else None
            power_state = self.hass.states.get(power_id) if power_id else None
            try: watts = float(power_state.state) if power_state and power_state.state not in ("unknown", "unavailable") else None
            except ValueError: watts = None
            if watts is not None: total += watts
            lights.append({"entity_id": entity_id, "name": light.name if light else entity_id, "state": light.state if light else "unavailable", "power_entity_id": power_id, "power_w": watts})
        return {"schedules": self.scheduler.options.get("schedules", []), "target_entity_ids": targets, "power_entity_ids": powers, "default_duration": self.scheduler.options.get("default_duration"), "lights": lights, "lights_on": sum(1 for light in lights if light["state"] == STATE_ON), "total_power_w": round(total, 2), "active": self.scheduler.active, "started_at": self.scheduler.started_at.isoformat() if self.scheduler.started_at else None, "finishes_at": self.scheduler.finishes_at.isoformat() if self.scheduler.finishes_at else None, "source": self.scheduler.source, "enabled": self.scheduler.enabled}
    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_UPDATE.format(entry_id=self.entry.entry_id), self.async_write_ha_state))
        self.async_on_remove(async_track_state_change_event(self.hass, self.scheduler.target_entity_ids + self.scheduler.power_entity_ids, lambda _: self.async_write_ha_state()))
