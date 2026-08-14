"""Light Scheduler integration and services."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components.frontend import (
    DATA_EXTRA_MODULE_URL,
    add_extra_js_url,
    remove_extra_js_url,
)
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.target import TargetSelection, async_extract_referenced_entity_ids
from homeassistant.helpers.typing import ConfigType

from .const import (
    CARD_JS_FILENAME,
    CARD_JS_URL,
    CONF_DEFAULT_DURATION,
    CONF_ENABLED,
    CONF_ENTITY_MAPPINGS,
    CONF_POWER_ENTITY_IDS,
    CONF_SCHEDULE_DAYS,
    CONF_SCHEDULE_DURATION,
    CONF_SCHEDULE_ID,
    CONF_SCHEDULE_INTERVAL,
    CONF_SCHEDULE_TIME,
    CONF_SCHEDULES,
    CONF_TARGET_ENTITY_IDS,
    DOMAIN,
    MAX_SCHEDULE_DURATION,
    MAX_SCHEDULE_INTERVAL,
    MIN_DURATION,
    PLATFORMS,
    SERVICE_ADD_SCHEDULE,
    SERVICE_REMOVE_SCHEDULE,
    SERVICE_SET_SCHEDULES,
    SERVICE_SET_ZONE_OPTIONS,
    SERVICE_STOP,
    SERVICE_TURN_ON_NOW,
    SERVICE_UPDATE_SCHEDULE,
)
from .schedules import new_schedule
from .scheduler import LightScheduler
from .store import RuntimeStore

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
_TARGET_KEYS = (
    "entity_id",
    "device_id",
    "area_id",
    "floor_id",
    "label_id",
    "metadata",
)
_ENTRY_ID = "entry_id"

SCHEDULE_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_SCHEDULE_ID): cv.string,
        vol.Required(CONF_SCHEDULE_TIME): cv.time,
        vol.Required(CONF_SCHEDULE_DAYS): vol.All(
            cv.ensure_list,
            [vol.All(vol.Coerce(int), vol.Range(min=0, max=6))],
            vol.Length(min=1),
        ),
        vol.Required(CONF_SCHEDULE_DURATION): vol.All(
            vol.Coerce(int),
            vol.Range(min=MIN_DURATION, max=MAX_SCHEDULE_DURATION),
        ),
        vol.Optional(CONF_SCHEDULE_INTERVAL, default=0): vol.All(
            vol.Coerce(int),
            vol.Range(min=0, max=MAX_SCHEDULE_INTERVAL),
        ),
        vol.Optional(CONF_ENABLED, default=True): cv.boolean,
    }
)


def _service_data(call: ServiceCall) -> dict[str, Any]:
    """Return service fields without Home Assistant targeting metadata."""
    data = dict(call.data)
    for key in (*_TARGET_KEYS, _ENTRY_ID):
        data.pop(key, None)
    return data


def _normalize_mappings(value: Any) -> list[dict[str, str]]:
    """Validate ordered light-to-power mappings received from the card."""
    if not isinstance(value, list) or not value:
        raise ServiceValidationError("Adicione pelo menos uma entrada de luz.")
    result: list[dict[str, str]] = []
    used_targets: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            raise ServiceValidationError("Entrada de luz inválida.")
        target = str(raw.get("target_entity_id") or "").strip()
        power = str(raw.get("power_entity_id") or "").strip()
        name = str(raw.get("name") or "").strip()
        if not target.startswith(("light.", "switch.")):
            raise ServiceValidationError(
                "Cada entrada precisa de uma entidade light ou switch."
            )
        if target in used_targets:
            raise ServiceValidationError(f"Entidade repetida: {target}")
        if power and not power.startswith("sensor."):
            raise ServiceValidationError(
                "O medidor de potência precisa ser uma entidade sensor."
            )
        used_targets.add(target)
        result.append(
            {
                "name": name,
                "target_entity_id": target,
                "power_entity_id": power,
            }
        )
    return result


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration and its frontend resource."""
    hass.data.setdefault(DOMAIN, {})
    await _register_services(hass)
    await _register_frontend(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one configured light zone."""
    await _register_services(hass)
    store = hass.data.setdefault(DOMAIN, {}).setdefault("store", RuntimeStore(hass))
    scheduler = LightScheduler(hass, entry, store)
    await scheduler.async_setup()
    entry.runtime_data = scheduler
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload one light zone."""
    await entry.runtime_data.async_unload()
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and not hass.config_entries.async_loaded_entries(DOMAIN):
        _unregister_services(hass)
        _unregister_frontend(hass)
    return unloaded


async def _options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply edited options without reloading the entire integration."""
    await entry.runtime_data.async_options_updated()


async def _register_frontend(hass: HomeAssistant) -> None:
    """Expose and automatically load the custom Lovelace card."""
    if hass.http is None:
        return
    js_path = Path(
        hass.config.path(
            "custom_components", DOMAIN, "frontend", CARD_JS_FILENAME
        )
    )
    if not js_path.is_file():
        return
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_JS_URL, str(js_path), cache_headers=False)]
    )
    if hass.data.get(DATA_EXTRA_MODULE_URL) is not None:
        add_extra_js_url(hass, CARD_JS_URL)


def _unregister_frontend(hass: HomeAssistant) -> None:
    if hass.data.get(DATA_EXTRA_MODULE_URL) is not None:
        remove_extra_js_url(hass, CARD_JS_URL)


async def _resolve(
    hass: HomeAssistant, call: ServiceCall
) -> list[LightScheduler]:
    """Resolve a zone from either entry_id (card) or HA service targets."""
    found: list[LightScheduler] = []
    raw_entry_ids = call.data.get(_ENTRY_ID)
    if raw_entry_ids:
        entry_ids = (
            [raw_entry_ids] if isinstance(raw_entry_ids, str) else list(raw_entry_ids)
        )
        for entry_id in entry_ids:
            entry = hass.config_entries.async_get_entry(str(entry_id))
            if (
                entry
                and entry.domain == DOMAIN
                and getattr(entry, "runtime_data", None) is not None
                and entry.runtime_data not in found
            ):
                found.append(entry.runtime_data)
        if found:
            return found

    selected = async_extract_referenced_entity_ids(
        hass, TargetSelection(call.data)
    )
    registry = er.async_get(hass)
    for entity_id in selected.referenced | selected.indirectly_referenced:
        registry_entry = registry.async_get(entity_id)
        entry = (
            hass.config_entries.async_get_entry(registry_entry.config_entry_id)
            if registry_entry and registry_entry.config_entry_id
            else None
        )
        if (
            entry
            and entry.domain == DOMAIN
            and getattr(entry, "runtime_data", None) is not None
            and entry.runtime_data not in found
        ):
            found.append(entry.runtime_data)

    if not found:
        raise ServiceValidationError(
            "Selecione uma entidade do Light Scheduler ou informe entry_id."
        )
    return found


async def _update_options(
    hass: HomeAssistant, scheduler: LightScheduler, options: dict[str, Any]
) -> None:
    """Persist an options change; the config-entry listener applies it."""
    hass.config_entries.async_update_entry(scheduler.entry, options=options)


async def _register_services(hass: HomeAssistant) -> None:
    """Register all zone and schedule services once."""
    if hass.services.has_service(DOMAIN, SERVICE_TURN_ON_NOW):
        return

    async def turn_on(call: ServiceCall) -> None:
        raw_duration = _service_data(call).get(CONF_SCHEDULE_DURATION)
        duration = None
        if raw_duration is not None:
            try:
                duration = vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_DURATION, max=MAX_SCHEDULE_DURATION),
                )(raw_duration)
            except vol.Invalid as err:
                raise ServiceValidationError(str(err)) from err
        for scheduler in await _resolve(hass, call):
            await scheduler.async_turn_on(duration=duration)

    async def stop(call: ServiceCall) -> None:
        for scheduler in await _resolve(hass, call):
            await scheduler.async_stop()

    async def add(call: ServiceCall) -> None:
        schedule = new_schedule(SCHEDULE_SCHEMA(_service_data(call)))
        for scheduler in await _resolve(hass, call):
            options = {
                **scheduler.options,
                CONF_SCHEDULES: [
                    *scheduler.options.get(CONF_SCHEDULES, []),
                    schedule,
                ],
            }
            await _update_options(hass, scheduler, options)

    async def remove(call: ServiceCall) -> None:
        schedule_id = _service_data(call).get(CONF_SCHEDULE_ID)
        if not isinstance(schedule_id, str) or not schedule_id:
            raise ServiceValidationError("Campo obrigatório ausente: id")
        for scheduler in await _resolve(hass, call):
            schedules = scheduler.options.get(CONF_SCHEDULES, [])
            if not any(
                item.get(CONF_SCHEDULE_ID) == schedule_id for item in schedules
            ):
                raise ServiceValidationError(
                    f"Agendamento desconhecido: {schedule_id}"
                )
            options = {
                **scheduler.options,
                CONF_SCHEDULES: [
                    item
                    for item in schedules
                    if item.get(CONF_SCHEDULE_ID) != schedule_id
                ],
            }
            await _update_options(hass, scheduler, options)

    async def set_schedules(call: ServiceCall) -> None:
        raw_schedules = _service_data(call).get(CONF_SCHEDULES, [])
        if not isinstance(raw_schedules, list):
            raise ServiceValidationError("schedules deve ser uma lista")
        schedules = [
            new_schedule(SCHEDULE_SCHEMA(value)) for value in raw_schedules
        ]
        for scheduler in await _resolve(hass, call):
            await _update_options(
                hass,
                scheduler,
                {**scheduler.options, CONF_SCHEDULES: schedules},
            )

    async def update(call: ServiceCall) -> None:
        data = _service_data(call)
        schedule_id = data.pop(CONF_SCHEDULE_ID, None)
        if not isinstance(schedule_id, str) or not schedule_id:
            raise ServiceValidationError("Campo obrigatório ausente: id")
        allowed = {
            CONF_SCHEDULE_TIME,
            CONF_SCHEDULE_DAYS,
            CONF_SCHEDULE_DURATION,
            CONF_SCHEDULE_INTERVAL,
            CONF_ENABLED,
        }
        patch = {key: value for key, value in data.items() if key in allowed}
        for scheduler in await _resolve(hass, call):
            schedules: list[dict[str, Any]] = []
            matched = False
            for schedule in scheduler.options.get(CONF_SCHEDULES, []):
                if schedule.get(CONF_SCHEDULE_ID) == schedule_id:
                    schedules.append(
                        new_schedule(
                            SCHEDULE_SCHEMA(
                                {
                                    **schedule,
                                    **patch,
                                    CONF_SCHEDULE_ID: schedule_id,
                                }
                            )
                        )
                    )
                    matched = True
                else:
                    schedules.append(schedule)
            if not matched:
                raise ServiceValidationError(
                    f"Agendamento desconhecido: {schedule_id}"
                )
            await _update_options(
                hass,
                scheduler,
                {**scheduler.options, CONF_SCHEDULES: schedules},
            )

    async def set_options(call: ServiceCall) -> None:
        data = _service_data(call)
        for scheduler in await _resolve(hass, call):
            options = dict(scheduler.options)
            if CONF_ENTITY_MAPPINGS in data:
                mappings = _normalize_mappings(data[CONF_ENTITY_MAPPINGS])
                options[CONF_ENTITY_MAPPINGS] = mappings
                options[CONF_TARGET_ENTITY_IDS] = [
                    item["target_entity_id"] for item in mappings
                ]
                options[CONF_POWER_ENTITY_IDS] = [
                    item["power_entity_id"]
                    for item in mappings
                    if item["power_entity_id"]
                ]
            for key in (
                CONF_DEFAULT_DURATION,
                CONF_TARGET_ENTITY_IDS,
                CONF_POWER_ENTITY_IDS,
            ):
                if key in data and CONF_ENTITY_MAPPINGS not in data:
                    options[key] = data[key]
            await _update_options(hass, scheduler, options)

    handlers = (
        (SERVICE_TURN_ON_NOW, turn_on),
        (SERVICE_STOP, stop),
        (SERVICE_ADD_SCHEDULE, add),
        (SERVICE_UPDATE_SCHEDULE, update),
        (SERVICE_REMOVE_SCHEDULE, remove),
        (SERVICE_SET_SCHEDULES, set_schedules),
        (SERVICE_SET_ZONE_OPTIONS, set_options),
    )
    for name, handler in handlers:
        hass.services.async_register(DOMAIN, name, handler)


def _unregister_services(hass: HomeAssistant) -> None:
    """Remove integration services after its last entry is unloaded."""
    for name in (
        SERVICE_TURN_ON_NOW,
        SERVICE_STOP,
        SERVICE_ADD_SCHEDULE,
        SERVICE_UPDATE_SCHEDULE,
        SERVICE_REMOVE_SCHEDULE,
        SERVICE_SET_SCHEDULES,
        SERVICE_SET_ZONE_OPTIONS,
    ):
        if hass.services.has_service(DOMAIN, name):
            hass.services.async_remove(DOMAIN, name)
