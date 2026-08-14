"""UI configuration for one scheduled group of lights."""
from __future__ import annotations

from typing import Any
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.helpers import selector
from .const import (CONF_DEFAULT_DURATION, CONF_ENABLED, CONF_MAX_DURATION, CONF_NAME,
                    CONF_POWER_ENTITY_IDS, CONF_SCHEDULES, CONF_TARGET_ENTITY_IDS, DOMAIN,
                    DEFAULT_DEFAULT_DURATION, DEFAULT_MAX_DURATION)


def _schema(values: dict[str, Any] | None = None) -> vol.Schema:
    values = values or {}
    return vol.Schema({
        vol.Required(CONF_NAME, default=values.get(CONF_NAME, "Sala")): selector.TextSelector(),
        vol.Required(CONF_TARGET_ENTITY_IDS, default=values.get(CONF_TARGET_ENTITY_IDS, [])): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="light", multiple=True)),
        vol.Optional(CONF_POWER_ENTITY_IDS, default=values.get(CONF_POWER_ENTITY_IDS, [])): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="sensor", multiple=True)),
        vol.Required(CONF_DEFAULT_DURATION, default=int(values.get(CONF_DEFAULT_DURATION, DEFAULT_DEFAULT_DURATION)) // 60): selector.NumberSelector(
            selector.NumberSelectorConfig(mode=selector.NumberSelectorMode.BOX, min=1, max=1440, unit_of_measurement="min")),
    })


class LightSchedulerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create one config entry per light zone."""
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            targets = list(dict.fromkeys(user_input[CONF_TARGET_ENTITY_IDS]))
            if not targets:
                errors[CONF_TARGET_ENTITY_IDS] = "no_lights"
            else:
                await self.async_set_unique_id("|".join(sorted(targets)))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=user_input[CONF_NAME], data={CONF_NAME: user_input[CONF_NAME]}, options={
                    CONF_ENABLED: True, CONF_TARGET_ENTITY_IDS: targets,
                    CONF_POWER_ENTITY_IDS: list(dict.fromkeys(user_input.get(CONF_POWER_ENTITY_IDS, []))),
                    CONF_DEFAULT_DURATION: int(user_input[CONF_DEFAULT_DURATION]) * 60,
                    CONF_MAX_DURATION: DEFAULT_MAX_DURATION, CONF_SCHEDULES: [],
                })
        return self.async_show_form(step_id="user", data_schema=_schema(user_input), errors=errors)

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> LightSchedulerOptionsFlow:
        return LightSchedulerOptionsFlow()


class LightSchedulerOptionsFlow(config_entries.OptionsFlow):
    """Edit the room's lights, power sensors and default duration."""
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            targets = list(dict.fromkeys(user_input[CONF_TARGET_ENTITY_IDS]))
            if not targets:
                return self.async_show_form(step_id="init", data_schema=_schema({**self.config_entry.options, **user_input}), errors={CONF_TARGET_ENTITY_IDS: "no_lights"})
            options = {**self.config_entry.options, CONF_TARGET_ENTITY_IDS: targets,
                       CONF_POWER_ENTITY_IDS: list(dict.fromkeys(user_input.get(CONF_POWER_ENTITY_IDS, []))),
                       CONF_DEFAULT_DURATION: int(user_input[CONF_DEFAULT_DURATION]) * 60}
            self.hass.config_entries.async_update_entry(self.config_entry, title=user_input[CONF_NAME], data={CONF_NAME: user_input[CONF_NAME]}, options=options)
            return self.async_create_entry(title="", data={})
        values = {**self.config_entry.options, CONF_NAME: self.config_entry.data.get(CONF_NAME, self.config_entry.title)}
        return self.async_show_form(step_id="init", data_schema=_schema(values))
