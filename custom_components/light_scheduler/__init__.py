"""Light Scheduler integration and services."""
from __future__ import annotations
from pathlib import Path
from typing import Any
import voluptuous as vol
from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL, add_extra_js_url, remove_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.target import TargetSelection, async_extract_referenced_entity_ids
from homeassistant.helpers.typing import ConfigType
from .const import *
from .schedules import new_schedule, serialize_schedule
from .scheduler import LightScheduler
from .store import RuntimeStore

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
_TARGET_KEYS = ("entity_id", "device_id", "area_id", "floor_id", "label_id", "metadata")
SCHEDULE_SCHEMA = vol.Schema({vol.Optional(CONF_SCHEDULE_ID): cv.string, vol.Required(CONF_SCHEDULE_TIME): cv.time, vol.Required(CONF_SCHEDULE_DAYS): vol.All(cv.ensure_list, [vol.All(vol.Coerce(int), vol.Range(min=0, max=6))], vol.Length(min=1)), vol.Required(CONF_SCHEDULE_DURATION): vol.All(vol.Coerce(int), vol.Range(min=MIN_DURATION, max=MAX_SCHEDULE_DURATION)), vol.Optional(CONF_ENABLED, default=True): cv.boolean})

def _data(call: ServiceCall) -> dict[str, Any]:
    data = dict(call.data)
    for key in _TARGET_KEYS: data.pop(key, None)
    return data

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hass.data.setdefault(DOMAIN, {})
    await _register_services(hass); await _register_frontend(hass)
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await _register_services(hass)
    store = hass.data.setdefault(DOMAIN, {}).setdefault("store", RuntimeStore(hass))
    scheduler = LightScheduler(hass, entry, store)
    await scheduler.async_setup(); entry.runtime_data = scheduler
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await entry.runtime_data.async_unload()
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and not hass.config_entries.async_loaded_entries(DOMAIN):
        _unregister_services(hass); _unregister_frontend(hass)
    return unloaded

async def _options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None: await entry.runtime_data.async_options_updated()

async def _register_frontend(hass: HomeAssistant) -> None:
    if hass.http is None: return
    js = Path(hass.config.path("custom_components", DOMAIN, "frontend", CARD_JS_FILENAME))
    if not js.is_file(): return
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_JS_URL, str(js), cache_headers=False)])
    if hass.data.get(DATA_EXTRA_MODULE_URL) is not None: add_extra_js_url(hass, CARD_JS_URL)

def _unregister_frontend(hass: HomeAssistant) -> None:
    if hass.data.get(DATA_EXTRA_MODULE_URL) is not None: remove_extra_js_url(hass, CARD_JS_URL)

async def _resolve(hass: HomeAssistant, call: ServiceCall) -> list[LightScheduler]:
    selected = async_extract_referenced_entity_ids(hass, TargetSelection(call.data))
    registry = er.async_get(hass); found = []
    for entity_id in selected.referenced | selected.indirectly_referenced:
        item = registry.async_get(entity_id)
        entry = hass.config_entries.async_get_entry(item.config_entry_id) if item and item.config_entry_id else None
        if entry and entry.domain == DOMAIN and entry.runtime_data not in found: found.append(entry.runtime_data)
    if not found: raise ServiceValidationError("Target a Light Scheduler entity")
    return found

async def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_TURN_ON_NOW): return
    async def turn_on(call):
        duration = _data(call).get("duration")
        for item in await _resolve(hass, call): await item.async_turn_on(duration=duration)
    async def stop(call):
        for item in await _resolve(hass, call): await item.async_stop()
    async def add(call):
        schedule = new_schedule(SCHEDULE_SCHEMA(_data(call)))
        for item in await _resolve(hass, call):
            options = {**item.options, CONF_SCHEDULES: [*item.options.get(CONF_SCHEDULES, []), schedule]}
            hass.config_entries.async_update_entry(item.entry, options=options); await item.async_options_updated()
    async def remove(call):
        schedule_id = _data(call).get(CONF_SCHEDULE_ID)
        if not isinstance(schedule_id, str) or not schedule_id:
            raise ServiceValidationError("Missing required field 'id'")
        for item in await _resolve(hass, call):
            options = {**item.options, CONF_SCHEDULES: [x for x in item.options.get(CONF_SCHEDULES, []) if x.get(CONF_SCHEDULE_ID) != schedule_id]}
            hass.config_entries.async_update_entry(item.entry, options=options); await item.async_options_updated()
    async def set_schedules(call):
        schedules = [new_schedule(SCHEDULE_SCHEMA(value)) for value in _data(call).get(CONF_SCHEDULES, [])]
        for item in await _resolve(hass, call): hass.config_entries.async_update_entry(item.entry, options={**item.options, CONF_SCHEDULES: schedules}); await item.async_options_updated()
    async def update(call):
        data = _data(call)
        schedule_id = data.pop(CONF_SCHEDULE_ID, None)
        if not isinstance(schedule_id, str) or not schedule_id:
            raise ServiceValidationError("Missing required field 'id'")
        allowed = {CONF_SCHEDULE_TIME, CONF_SCHEDULE_DAYS, CONF_SCHEDULE_DURATION, CONF_ENABLED}
        patch = {key: value for key, value in data.items() if key in allowed}
        for item in await _resolve(hass, call):
            schedules, matched = [], False
            for schedule in item.options.get(CONF_SCHEDULES, []):
                if schedule.get(CONF_SCHEDULE_ID) == schedule_id:
                    schedules.append(new_schedule(SCHEDULE_SCHEMA({**schedule, **patch, CONF_SCHEDULE_ID: schedule_id})))
                    matched = True
                else:
                    schedules.append(schedule)
            if not matched:
                raise ServiceValidationError(f"Unknown schedule id '{schedule_id}'")
            hass.config_entries.async_update_entry(item.entry, options={**item.options, CONF_SCHEDULES: schedules}); await item.async_options_updated()
    async def set_options(call):
        data = _data(call)
        for item in await _resolve(hass, call):
            options = dict(item.options)
            for key in (CONF_DEFAULT_DURATION, CONF_TARGET_ENTITY_IDS, CONF_POWER_ENTITY_IDS):
                if key in data: options[key] = data[key]
            hass.config_entries.async_update_entry(item.entry, options=options); await item.async_options_updated()
    for name, handler in ((SERVICE_TURN_ON_NOW, turn_on), (SERVICE_STOP, stop), (SERVICE_ADD_SCHEDULE, add), (SERVICE_UPDATE_SCHEDULE, update), (SERVICE_REMOVE_SCHEDULE, remove), (SERVICE_SET_SCHEDULES, set_schedules), (SERVICE_SET_ZONE_OPTIONS, set_options)):
        hass.services.async_register(DOMAIN, name, handler)

def _unregister_services(hass: HomeAssistant) -> None:
    for name in (SERVICE_TURN_ON_NOW, SERVICE_STOP, SERVICE_ADD_SCHEDULE, SERVICE_UPDATE_SCHEDULE, SERVICE_REMOVE_SCHEDULE, SERVICE_SET_SCHEDULES, SERVICE_SET_ZONE_OPTIONS):
        if hass.services.has_service(DOMAIN, name): hass.services.async_remove(DOMAIN, name)
