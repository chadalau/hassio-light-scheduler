"""Light Scheduler integration and services."""

from __future__ import annotations

import logging
from collections.abc import Callable
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
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.target import TargetSelection, async_extract_referenced_entity_ids
from homeassistant.helpers.typing import ConfigType

from .const import (
    CARD_JS_FILENAME,
    CARD_JS_URL,
    CONF_DEFAULT_DURATION,
    CONF_ENABLED,
    CONF_ENTITY_MAPPINGS,
    CONF_NAME,
    CONF_POWER_ENTITY_IDS,
    CONF_POWER_THRESHOLD,
    CONF_SCHEDULE_DAYS,
    CONF_SCHEDULE_DURATION,
    CONF_SCHEDULE_ID,
    CONF_SCHEDULE_INTERVAL,
    CONF_SCHEDULE_TIME,
    CONF_SCHEDULE_WARNING,
    CONF_SCHEDULES,
    CONF_TARGET_ENTITY_IDS,
    DEFAULT_POWER_THRESHOLD_W,
    DOMAIN,
    MAX_POWER_THRESHOLD_W,
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
    WARNING_TARGETS_REMOVED,
)
from .power import is_power_sensor
from .scheduler import LightScheduler
from .schedules import new_schedule, prune_schedule_targets
from .store import RuntimeStore
from .zones import foreign_entities, newly_shared_entities, zone_targets

_LOGGER = logging.getLogger(__name__)

# Rebuilds one entry's options from whatever is current, inside its lock.
_OptionsBuilder = Callable[[dict[str, Any]], dict[str, Any]]

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
        # Empty means every light in the zone, so narrowing is opt-in.
        vol.Optional(CONF_TARGET_ENTITY_IDS, default=list): vol.All(
            cv.ensure_list,
            [
                vol.All(
                    cv.string,
                    vol.Match(
                        r"^(light|switch)\.",
                        msg="Cada luz do agendamento precisa ser light ou switch.",
                    ),
                )
            ],
        ),
        vol.Optional(CONF_ENABLED, default=True): cv.boolean,
        # Set by the integration itself when it has to disable a schedule.
        # Accepted here only so update() can round-trip an existing row.
        vol.Optional(CONF_SCHEDULE_WARNING, default=""): vol.In(
            ("", WARNING_TARGETS_REMOVED)
        ),
    }
)


def _service_data(call: ServiceCall) -> dict[str, Any]:
    """Return service fields without Home Assistant targeting metadata."""
    data = dict(call.data)
    for key in (*_TARGET_KEYS, _ENTRY_ID):
        data.pop(key, None)
    return data


def _normalize_mappings(
    hass: HomeAssistant, value: Any
) -> list[dict[str, str]]:
    """Validate ordered light-to-power mappings received from the card."""
    if not isinstance(value, list) or not value:
        raise ServiceValidationError("Adicione pelo menos uma entrada de luz.")
    result: list[dict[str, str]] = []
    used_targets: set[str] = set()
    used_powers: set[str] = set()
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
        if power in used_powers:
            raise ServiceValidationError(
                f"Medidor de potência repetido: {power}"
            )
        power_state = hass.states.get(power) if power else None
        if power_state and not is_power_sensor(power_state):
            raise ServiceValidationError(
                f"A entidade {power} não é um sensor de potência."
            )
        used_targets.add(target)
        if power:
            used_powers.add(power)
        result.append(
            {
                "name": name,
                "target_entity_id": target,
                "power_entity_id": power,
                CONF_POWER_THRESHOLD: _power_threshold(raw.get(CONF_POWER_THRESHOLD)),
            }
        )
    return result


def _power_threshold(value: Any) -> float:
    """Validate the watts above which an entry counts as drawing current.

    A fixed global threshold makes a sub-watt LED strip fail confirmation on
    every run, so each entry carries its own.
    """
    if value in (None, ""):
        return DEFAULT_POWER_THRESHOLD_W
    try:
        threshold = float(value)
    except (TypeError, ValueError):
        raise ServiceValidationError(
            f"Limiar de potência inválido: {value}"
        ) from None
    if not 0 < threshold <= MAX_POWER_THRESHOLD_W:
        raise ServiceValidationError(
            "O limiar de potência precisa estar entre 0 e "
            f"{MAX_POWER_THRESHOLD_W:.0f} W."
        )
    return round(threshold, 3)


def _assert_targets_in_zone(
    scheduler: LightScheduler, schedule: dict[str, Any]
) -> None:
    """Reject a schedule that selects lights the zone does not control.

    The schema only checks the domain. Without this, a call could persist an
    entity from another zone and the run would silently skip it, leaving the
    user with a schedule that never touches what they picked.
    """
    unknown = [
        entity_id
        for entity_id in schedule.get(CONF_TARGET_ENTITY_IDS) or []
        if entity_id not in scheduler.target_entity_ids
    ]
    if unknown:
        raise ServiceValidationError(
            "Estas luzes não pertencem a esta zona: " + ", ".join(unknown)
        )


def _assert_schedule_id_is_free(
    scheduler: LightScheduler, schedule_id: Any
) -> None:
    """Reject an id already used in this zone.

    Two rows sharing an id make the second one unreachable: update edits every
    match and remove deletes every match, so it can never be edited or deleted
    on its own.
    """
    if any(
        item.get(CONF_SCHEDULE_ID) == schedule_id
        for item in scheduler.options.get(CONF_SCHEDULES, [])
    ):
        raise ServiceValidationError(
            f"Já existe um agendamento com o id {schedule_id} nesta zona."
        )


def _find_schedule_or_fail(
    scheduler: LightScheduler, schedule_id: str
) -> dict[str, Any]:
    """Return the single schedule with this id, or raise."""
    matches = [
        item
        for item in scheduler.options.get(CONF_SCHEDULES, [])
        if item.get(CONF_SCHEDULE_ID) == schedule_id
    ]
    if not matches:
        raise ServiceValidationError(
            f"Agendamento desconhecido em {scheduler.entry.title}: {schedule_id}"
        )
    if len(matches) > 1:
        raise ServiceValidationError(
            f"Existem {len(matches)} agendamentos com o id {schedule_id} em "
            f"{scheduler.entry.title}; use set_schedules para corrigir a lista."
        )
    return matches[0]


def _with_pruned_schedules(options: dict[str, Any]) -> dict[str, Any]:
    """Drop lights the zone no longer controls from every schedule.

    A selection is only checked against the zone when the schedule is written.
    Nothing used to re-check it when the zone itself changed, so removing a
    light left every schedule narrowed to it resolving to an empty target list
    -- which never runs, while the card still showed the row as scheduled.

    An emptied selection is deliberately NOT read as "the whole zone": that
    would widen a schedule the user narrowed on purpose. The row is disabled and
    flagged so it can explain itself and ask for a new selection.
    """
    schedules, disabled = prune_schedule_targets(
        options.get(CONF_SCHEDULES, []), options.get(CONF_TARGET_ENTITY_IDS, [])
    )
    if disabled:
        _LOGGER.warning(
            "Disabled %s schedule(s) whose lights left the zone: %s",
            len(disabled), ", ".join(disabled),
        )
    return {**options, CONF_SCHEDULES: schedules}


def _assert_no_new_overlap(
    hass: HomeAssistant, scheduler: LightScheduler, targets: list[str]
) -> None:
    """Refuse to hand a light that another zone already owns to this one.

    Two zones driving one light fight each other silently: the first run to end
    turns it off and the second keeps reporting itself as on, because a zone
    ignores state changes on its own targets while active.

    Only overlap this call would add is refused, so a zone that already shares a
    light can still be edited -- and fixed.
    """
    shared = newly_shared_entities(
        zone_targets(hass.config_entries.async_entries(DOMAIN)),
        scheduler.target_entity_ids,
        targets,
        scheduler.entry.entry_id,
    )
    if shared:
        raise ServiceValidationError(
            "Estas luzes já pertencem a outra zona: " + ", ".join(shared)
        )


def _patched_schedule(
    scheduler: LightScheduler, schedule_id: str, patch: dict[str, Any]
) -> dict[str, Any]:
    """Return the edited schedule, validated against the zone it belongs to."""
    current = _find_schedule_or_fail(scheduler, schedule_id)
    warning = str(current.get(CONF_SCHEDULE_WARNING) or "")
    if (
        warning == WARNING_TARGETS_REMOVED
        and patch.get(CONF_ENABLED)
        and CONF_TARGET_ENTITY_IDS not in patch
    ):
        raise ServiceValidationError(
            "Este agendamento foi desativado porque as luzes dele saíram da "
            "zona. Escolha as luzes novamente antes de reativá-lo."
        )
    replacement = _validated_schedule(
        {
            **current,
            **patch,
            CONF_SCHEDULE_ID: schedule_id,
            # Choosing lights again is what clears the flag; any other edit
            # leaves the row marked so the user still sees what is wrong.
            CONF_SCHEDULE_WARNING: (
                "" if CONF_TARGET_ENTITY_IDS in patch else warning
            ),
        }
    )
    _assert_targets_in_zone(scheduler, replacement)
    return replacement


def _repaired_mappings(
    scheduler: LightScheduler, targets: list[str], powers: list[str]
) -> list[dict[str, Any]]:
    """Rebuild light-to-power pairs after a flat target/power list edit.

    Pairs the user already made are kept; only the sensors left over are handed
    out positionally, so editing the target list does not reshuffle everything.
    """
    existing = {
        item.get("target_entity_id"): item for item in scheduler.entity_mappings
    }
    retained_powers = {
        str(item.get("power_entity_id"))
        for target, item in existing.items()
        if target in targets and item.get("power_entity_id") in powers
    }
    available_powers = [power for power in powers if power not in retained_powers]
    mappings: list[dict[str, Any]] = []
    for target in targets:
        old = existing.get(target, {})
        old_power = str(old.get("power_entity_id") or "")
        power = old_power if old_power in powers else ""
        if not power and available_powers:
            power = available_powers.pop(0)
        mappings.append(
            {
                "name": str(old.get("name") or ""),
                "target_entity_id": target,
                "power_entity_id": power,
                CONF_POWER_THRESHOLD: old.get(CONF_POWER_THRESHOLD),
            }
        )
    return mappings


def _validated_schedule(value: Any) -> dict[str, Any]:
    """Validate service schedule input with a user-facing error."""
    if not isinstance(value, dict):
        raise ServiceValidationError("Cada agendamento precisa ser um objeto.")
    try:
        return new_schedule(SCHEDULE_SCHEMA(value))
    except vol.Invalid as err:
        raise ServiceValidationError(str(err)) from err


def _normalize_entity_ids(
    value: Any, domains: tuple[str, ...], *, required: bool = False
) -> list[str]:
    """Validate and deduplicate a service entity list."""
    raw = [value] if isinstance(value, str) else value
    if not isinstance(raw, list):
        raise ServiceValidationError("A seleção de entidades precisa ser uma lista.")
    result: list[str] = []
    for item in raw:
        entity_id = str(item or "").strip()
        if not entity_id.startswith(tuple(f"{domain}." for domain in domains)):
            raise ServiceValidationError(
                f"Entidade inválida para esta seleção: {entity_id or '(vazia)'}"
            )
        if entity_id not in result:
            result.append(entity_id)
    if required and not result:
        raise ServiceValidationError("Adicione pelo menos uma luz ou tomada.")
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
    await _register_frontend(hass)
    store = hass.data.setdefault(DOMAIN, {}).setdefault("store", RuntimeStore(hass))
    scheduler = LightScheduler(hass, entry, store)
    shared = foreign_entities(
        zone_targets(hass.config_entries.async_entries(DOMAIN)),
        scheduler.target_entity_ids,
        entry.entry_id,
    )
    if shared:
        _LOGGER.warning(
            "%s shares %s with another zone; whichever run ends first will turn "
            "them off for both. Give each light to a single zone.",
            entry.title, ", ".join(shared),
        )
    await scheduler.async_setup()
    entry.runtime_data = scheduler
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload one light zone.

    The platforms go first: tearing the scheduler down (which turns the running
    lights off) before knowing whether the entities could be removed would leave
    a still-loaded entry pointing at a dead scheduler.
    """
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unloaded:
        return False
    await entry.runtime_data.async_unload()
    if not hass.config_entries.async_loaded_entries(DOMAIN):
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
    domain_data = hass.data.setdefault(DOMAIN, {})
    if not domain_data.get("frontend_path_registered"):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_JS_URL, str(js_path), cache_headers=False)]
        )
        domain_data["frontend_path_registered"] = True
    if (
        hass.data.get(DATA_EXTRA_MODULE_URL) is not None
        and not domain_data.get("frontend_module_registered")
    ):
        add_extra_js_url(hass, CARD_JS_URL)
        domain_data["frontend_module_registered"] = True


def _unregister_frontend(hass: HomeAssistant) -> None:
    domain_data = hass.data.setdefault(DOMAIN, {})
    if (
        hass.data.get(DATA_EXTRA_MODULE_URL) is not None
        and domain_data.get("frontend_module_registered")
    ):
        remove_extra_js_url(hass, CARD_JS_URL)
        domain_data["frontend_module_registered"] = False


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
        raise ServiceValidationError(
            "A zona informada em entry_id não existe ou não está carregada."
        )

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


async def _apply_options(
    hass: HomeAssistant,
    scheduler: LightScheduler,
    build: _OptionsBuilder,
) -> None:
    """Serialize one entry's read-modify-write over its options.

    Every handler used to read ``scheduler.options``, build a new dict and write
    it back. Two concurrent calls -- two card toggles in the same second -- read
    the same list and the second write erased the first. ``build`` runs inside
    the lock and re-reads the current options, so it also gets to re-validate
    against whatever landed in the meantime.
    """
    async with scheduler.options_lock:
        options = build(scheduler.options)
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
            # Not awaited: the ramp holds for `interval` seconds per light plus
            # the confirmation windows, which would block the calling script for
            # up to twenty minutes.
            scheduler.async_request_turn_on(duration=duration)

    async def stop(call: ServiceCall) -> None:
        for scheduler in await _resolve(hass, call):
            scheduler.async_request_stop()

    # Every handler below validates all resolved zones BEFORE mutating any of
    # them. Validating inside the apply loop meant a call naming two zones
    # could persist the first and then raise on the second, leaving the caller
    # with an error and a half-applied change. The same validation runs again
    # inside each zone's lock, where the options cannot change under it.

    async def add(call: ServiceCall) -> None:
        schedule = _validated_schedule(_service_data(call))
        schedulers = await _resolve(hass, call)
        for scheduler in schedulers:
            _assert_targets_in_zone(scheduler, schedule)
            _assert_schedule_id_is_free(scheduler, schedule[CONF_SCHEDULE_ID])

        def build(scheduler: LightScheduler) -> _OptionsBuilder:
            def apply(options: dict[str, Any]) -> dict[str, Any]:
                _assert_targets_in_zone(scheduler, schedule)
                _assert_schedule_id_is_free(scheduler, schedule[CONF_SCHEDULE_ID])
                return {
                    **options,
                    # A fresh copy per zone: sharing one dict across entries
                    # would alias two independent configurations.
                    CONF_SCHEDULES: [
                        *options.get(CONF_SCHEDULES, []),
                        dict(schedule),
                    ],
                }

            return apply

        for scheduler in schedulers:
            await _apply_options(hass, scheduler, build(scheduler))

    async def remove(call: ServiceCall) -> None:
        schedule_id = _service_data(call).get(CONF_SCHEDULE_ID)
        if not isinstance(schedule_id, str) or not schedule_id:
            raise ServiceValidationError("Campo obrigatorio ausente: id")
        schedulers = await _resolve(hass, call)
        for scheduler in schedulers:
            _find_schedule_or_fail(scheduler, schedule_id)

        def build(scheduler: LightScheduler) -> _OptionsBuilder:
            def apply(options: dict[str, Any]) -> dict[str, Any]:
                _find_schedule_or_fail(scheduler, schedule_id)
                return {
                    **options,
                    CONF_SCHEDULES: [
                        item
                        for item in options.get(CONF_SCHEDULES, [])
                        if item.get(CONF_SCHEDULE_ID) != schedule_id
                    ],
                }

            return apply

        for scheduler in schedulers:
            await _apply_options(hass, scheduler, build(scheduler))

    async def set_schedules(call: ServiceCall) -> None:
        raw_schedules = _service_data(call).get(CONF_SCHEDULES, [])
        if not isinstance(raw_schedules, list):
            raise ServiceValidationError("schedules deve ser uma lista")
        schedules = [_validated_schedule(value) for value in raw_schedules]
        seen: set[str] = set()
        for schedule in schedules:
            schedule_id = schedule[CONF_SCHEDULE_ID]
            if schedule_id in seen:
                raise ServiceValidationError(
                    f"Id repetido na lista de agendamentos: {schedule_id}"
                )
            seen.add(schedule_id)
        schedulers = await _resolve(hass, call)
        for scheduler in schedulers:
            for schedule in schedules:
                _assert_targets_in_zone(scheduler, schedule)

        def build(scheduler: LightScheduler) -> _OptionsBuilder:
            def apply(options: dict[str, Any]) -> dict[str, Any]:
                for schedule in schedules:
                    _assert_targets_in_zone(scheduler, schedule)
                return {
                    **options,
                    CONF_SCHEDULES: [dict(schedule) for schedule in schedules],
                }

            return apply

        for scheduler in schedulers:
            await _apply_options(hass, scheduler, build(scheduler))

    async def update(call: ServiceCall) -> None:
        data = _service_data(call)
        schedule_id = data.pop(CONF_SCHEDULE_ID, None)
        if not isinstance(schedule_id, str) or not schedule_id:
            raise ServiceValidationError("Campo obrigatorio ausente: id")
        allowed = {
            CONF_SCHEDULE_TIME,
            CONF_SCHEDULE_DAYS,
            CONF_SCHEDULE_DURATION,
            CONF_SCHEDULE_INTERVAL,
            CONF_TARGET_ENTITY_IDS,
            CONF_ENABLED,
        }
        unknown = set(data) - allowed
        if unknown:
            raise ServiceValidationError(
                "Campos desconhecidos: " + ", ".join(sorted(unknown))
            )
        patch = {key: value for key, value in data.items() if key in allowed}
        if not patch:
            raise ServiceValidationError(
                "Informe pelo menos um campo para alterar o agendamento."
            )
        schedulers = await _resolve(hass, call)
        for scheduler in schedulers:
            _patched_schedule(scheduler, schedule_id, patch)

        def build(scheduler: LightScheduler) -> _OptionsBuilder:
            def apply(options: dict[str, Any]) -> dict[str, Any]:
                replacement = _patched_schedule(scheduler, schedule_id, patch)
                return {
                    **options,
                    CONF_SCHEDULES: [
                        replacement
                        if item.get(CONF_SCHEDULE_ID) == schedule_id
                        else item
                        for item in options.get(CONF_SCHEDULES, [])
                    ],
                }

            return apply

        for scheduler in schedulers:
            await _apply_options(hass, scheduler, build(scheduler))

    async def set_options(call: ServiceCall) -> None:
        data = _service_data(call)
        allowed = {
            CONF_ENTITY_MAPPINGS,
            CONF_DEFAULT_DURATION,
            CONF_NAME,
            CONF_TARGET_ENTITY_IDS,
            CONF_POWER_ENTITY_IDS,
        }
        unknown = set(data) - allowed
        if unknown:
            raise ServiceValidationError(
                "Campos desconhecidos: " + ", ".join(sorted(unknown))
            )
        if not data:
            raise ServiceValidationError(
                "Informe pelo menos uma opcao para alterar a zona."
            )
        schedulers = await _resolve(hass, call)
        name = data.pop(CONF_NAME, None)

        def build(scheduler: LightScheduler) -> _OptionsBuilder:
            def apply(current: dict[str, Any]) -> dict[str, Any]:
                options = dict(current)
                if CONF_ENTITY_MAPPINGS in data:
                    mappings = _normalize_mappings(hass, data[CONF_ENTITY_MAPPINGS])
                elif CONF_TARGET_ENTITY_IDS in data or CONF_POWER_ENTITY_IDS in data:
                    targets = _normalize_entity_ids(
                        data.get(CONF_TARGET_ENTITY_IDS, scheduler.target_entity_ids),
                        ("light", "switch"),
                        required=True,
                    )
                    powers = _normalize_entity_ids(
                        data.get(CONF_POWER_ENTITY_IDS, scheduler.power_entity_ids),
                        ("sensor",),
                    )
                    mappings = _normalize_mappings(
                        hass, _repaired_mappings(scheduler, targets, powers)
                    )
                else:
                    mappings = None
                if mappings is not None:
                    new_targets = [item["target_entity_id"] for item in mappings]
                    _assert_no_new_overlap(hass, scheduler, new_targets)
                    options[CONF_ENTITY_MAPPINGS] = mappings
                    options[CONF_TARGET_ENTITY_IDS] = new_targets
                    options[CONF_POWER_ENTITY_IDS] = [
                        item["power_entity_id"]
                        for item in mappings
                        if item["power_entity_id"]
                    ]
                if CONF_DEFAULT_DURATION in data:
                    try:
                        options[CONF_DEFAULT_DURATION] = vol.All(
                            vol.Coerce(int),
                            vol.Range(min=MIN_DURATION, max=MAX_SCHEDULE_DURATION),
                        )(data[CONF_DEFAULT_DURATION])
                    except vol.Invalid as err:
                        raise ServiceValidationError(str(err)) from err
                return _with_pruned_schedules(options)

            return apply

        for scheduler in schedulers:
            if name is not None:
                await scheduler.async_set_zone_name(name)
            if data:
                await _apply_options(hass, scheduler, build(scheduler))

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
