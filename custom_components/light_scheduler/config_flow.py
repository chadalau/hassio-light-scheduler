"""Config and options flows for Light Scheduler."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector

from .const import (
    CONF_DEFAULT_DURATION,
    CONF_ENABLED,
    CONF_ENTITY_MAPPINGS,
    CONF_MAX_DURATION,
    CONF_NAME,
    CONF_POWER_ENTITY_IDS,
    CONF_POWER_THRESHOLD,
    CONF_SCHEDULES,
    CONF_TARGET_ENTITY_IDS,
    DEFAULT_DEFAULT_DURATION,
    DEFAULT_MAX_DURATION,
    DOMAIN,
)
from .power import is_power_sensor
from .schedules import prune_schedule_targets
from .zones import newly_shared_entities, zone_targets


def _entity_list(value: Any) -> list[str]:
    """Normalize a multiple entity selector result."""
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return list(dict.fromkeys(value))


def _invalid_power_sensors(hass: HomeAssistant, powers: list[str]) -> list[str]:
    """Return selected sensors that are loaded but are not power meters.

    Sensors that are not loaded yet are accepted: rejecting them would break
    setup after a restart, and the runtime confirmation already ignores any
    reading it cannot verify as power.
    """
    return [
        entity_id
        for entity_id in powers
        if hass.states.get(entity_id) is not None
        and not is_power_sensor(hass.states.get(entity_id))
    ]


def _build_mappings(
    targets: list[str],
    powers: list[str],
    existing: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Build ordered mappings without changing existing target/power pairs."""
    existing_by_target = {
        item.get("target_entity_id"): item
        for item in (existing or [])
        if isinstance(item, dict) and item.get("target_entity_id")
    }
    retained_powers = {
        str(item.get("power_entity_id"))
        for target, item in existing_by_target.items()
        if target in targets
        and item.get("power_entity_id") in powers
    }
    available_powers = [power for power in powers if power not in retained_powers]
    result: list[dict[str, str]] = []
    used_powers: set[str] = set()
    for target in targets:
        existing_item = existing_by_target.get(target, {})
        existing_power = str(existing_item.get("power_entity_id") or "")
        power = (
            existing_power
            if existing_power in powers and existing_power not in used_powers
            else ""
        )
        if not power and available_powers:
            power = available_powers.pop(0)
        if power:
            used_powers.add(power)
        entry = {
            "name": str(existing_item.get("name") or ""),
            "target_entity_id": target,
            "power_entity_id": power,
        }
        threshold = existing_item.get(CONF_POWER_THRESHOLD)
        if threshold is not None:
            entry[CONF_POWER_THRESHOLD] = threshold
        result.append(entry)
    return result


def _duplicate_zone(
    entries: list[ConfigEntry], targets: list[str], skip_entry_id: str | None = None
) -> bool:
    """Return whether another zone already controls exactly these entities.

    Checked against what the zones control *right now*, not against a unique id
    frozen when they were created: editing a zone's lights used to leave that id
    behind, so a new zone could be rejected over a light the other zone had
    already given up.
    """
    wanted = frozenset(targets)
    return any(
        entry.entry_id != skip_entry_id
        and frozenset(entry.options.get(CONF_TARGET_ENTITY_IDS, [])) == wanted
        for entry in entries
    )


def _schema(values: dict[str, Any] | None = None, *, persisted: bool = False) -> vol.Schema:
    """Build the zone form with safe initial values."""
    values = values or {}
    # Stored values are seconds; anything coming back from the form is already
    # in minutes, including the default shown on a blank form.
    raw_duration = float(
        values.get(
            CONF_DEFAULT_DURATION,
            DEFAULT_DEFAULT_DURATION if persisted else DEFAULT_DEFAULT_DURATION // 60,
        )
    )
    duration_minutes = max(1 / 60, raw_duration / 60 if persisted else raw_duration)
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=values.get(CONF_NAME, "Sala")): (
                selector.TextSelector()
            ),
            vol.Required(
                CONF_TARGET_ENTITY_IDS,
                default=_entity_list(values.get(CONF_TARGET_ENTITY_IDS)),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain=["light", "switch"], multiple=True
                )
            ),
            vol.Optional(
                CONF_POWER_ENTITY_IDS,
                default=_entity_list(values.get(CONF_POWER_ENTITY_IDS)),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", multiple=True)
            ),
            vol.Required(
                CONF_DEFAULT_DURATION, default=duration_minutes
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    mode=selector.NumberSelectorMode.BOX,
                    min=1 / 60,
                    max=1440,
                    step=1 / 60,
                    unit_of_measurement="min",
                )
            ),
        }
    )


class LightSchedulerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create one config entry per light zone."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create a light zone."""
        errors: dict[str, str] = {}
        if user_input is not None:
            targets = _entity_list(user_input.get(CONF_TARGET_ENTITY_IDS))
            power_ids = _entity_list(user_input.get(CONF_POWER_ENTITY_IDS))
            if not targets:
                errors[CONF_TARGET_ENTITY_IDS] = "no_lights"
            elif _invalid_power_sensors(self.hass, power_ids):
                errors[CONF_POWER_ENTITY_IDS] = "not_power_sensor"
            elif _duplicate_zone(list(self._async_current_entries()), targets):
                errors[CONF_TARGET_ENTITY_IDS] = "duplicate_zone"
            elif newly_shared_entities(
                zone_targets(self._async_current_entries()), [], targets
            ):
                errors[CONF_TARGET_ENTITY_IDS] = "overlapping_zone"
            else:
                name = str(user_input[CONF_NAME]).strip()
                return self.async_create_entry(
                    title=name,
                    data={CONF_NAME: name},
                    options={
                        CONF_ENABLED: True,
                        CONF_TARGET_ENTITY_IDS: targets,
                        CONF_POWER_ENTITY_IDS: power_ids,
                        CONF_ENTITY_MAPPINGS: _build_mappings(
                            targets, power_ids
                        ),
                        CONF_DEFAULT_DURATION: round(
                            float(user_input[CONF_DEFAULT_DURATION]) * 60
                        ),
                        CONF_MAX_DURATION: DEFAULT_MAX_DURATION,
                        CONF_SCHEDULES: [],
                    },
                )
        return self.async_show_form(
            step_id="user", data_schema=_schema(user_input), errors=errors
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> LightSchedulerOptionsFlow:
        """Return the options flow."""
        return LightSchedulerOptionsFlow()


class LightSchedulerOptionsFlow(config_entries.OptionsFlow):
    """Edit lights, power sensors, name and default duration."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit an existing light zone."""
        if user_input is not None:
            targets = _entity_list(user_input.get(CONF_TARGET_ENTITY_IDS))
            power_ids = _entity_list(user_input.get(CONF_POWER_ENTITY_IDS))
            errors: dict[str, str] = {}
            if not targets:
                errors[CONF_TARGET_ENTITY_IDS] = "no_lights"
            elif _invalid_power_sensors(self.hass, power_ids):
                errors[CONF_POWER_ENTITY_IDS] = "not_power_sensor"
            elif _duplicate_zone(
                list(self.hass.config_entries.async_entries(DOMAIN)),
                targets,
                self.config_entry.entry_id,
            ):
                errors[CONF_TARGET_ENTITY_IDS] = "duplicate_zone"
            elif newly_shared_entities(
                zone_targets(self.hass.config_entries.async_entries(DOMAIN)),
                self.config_entry.options.get(CONF_TARGET_ENTITY_IDS, []),
                targets,
                self.config_entry.entry_id,
            ):
                errors[CONF_TARGET_ENTITY_IDS] = "overlapping_zone"
            if errors:
                return self.async_show_form(
                    step_id="init", data_schema=_schema(user_input), errors=errors
                )
            name = str(user_input[CONF_NAME]).strip()
            # A schedule narrowed to a light that just left the zone would
            # resolve to an empty target list and never run again, so the
            # selections are pruned along with the zone itself.
            schedules, _ = prune_schedule_targets(
                self.config_entry.options.get(CONF_SCHEDULES, []), targets
            )
            options = {
                **self.config_entry.options,
                CONF_TARGET_ENTITY_IDS: targets,
                CONF_POWER_ENTITY_IDS: power_ids,
                CONF_ENTITY_MAPPINGS: _build_mappings(
                    targets,
                    power_ids,
                    self.config_entry.options.get(CONF_ENTITY_MAPPINGS, []),
                ),
                CONF_DEFAULT_DURATION: round(
                    float(user_input[CONF_DEFAULT_DURATION]) * 60
                ),
                CONF_SCHEDULES: schedules,
            }
            # OptionsFlowManager persists the data returned by
            # async_create_entry as the entry options. Updating options here
            # and then returning an empty dict would erase the submitted form.
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                title=name,
                data={**self.config_entry.data, CONF_NAME: name},
            )
            return self.async_create_entry(title="", data=options)

        values = {
            **self.config_entry.options,
            CONF_NAME: self.config_entry.data.get(
                CONF_NAME, self.config_entry.title
            ),
        }
        return self.async_show_form(
            step_id="init", data_schema=_schema(values, persisted=True)
        )
