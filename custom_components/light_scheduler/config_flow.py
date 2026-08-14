"""Config and options flows for Light Scheduler."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_DEFAULT_DURATION,
    CONF_ENABLED,
    CONF_MAX_DURATION,
    CONF_NAME,
    CONF_POWER_ENTITY_IDS,
    CONF_SCHEDULES,
    CONF_TARGET_ENTITY_IDS,
    DEFAULT_DEFAULT_DURATION,
    DEFAULT_MAX_DURATION,
    DOMAIN,
)


def _entity_list(value: Any) -> list[str]:
    """Normalize a multiple entity selector result."""
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return list(dict.fromkeys(value))


def _schema(values: dict[str, Any] | None = None, *, persisted: bool = False) -> vol.Schema:
    """Build the zone form with safe initial values."""
    values = values or {}
    raw_duration = int(values.get(CONF_DEFAULT_DURATION, DEFAULT_DEFAULT_DURATION))
    duration_minutes = max(1, raw_duration // 60) if persisted else max(1, raw_duration)
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
                    min=1,
                    max=1440,
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
            if not targets:
                errors[CONF_TARGET_ENTITY_IDS] = "no_lights"
            else:
                await self.async_set_unique_id("|".join(sorted(targets)))
                self._abort_if_unique_id_configured()
                name = str(user_input[CONF_NAME]).strip()
                return self.async_create_entry(
                    title=name,
                    data={CONF_NAME: name},
                    options={
                        CONF_ENABLED: True,
                        CONF_TARGET_ENTITY_IDS: targets,
                        CONF_POWER_ENTITY_IDS: _entity_list(
                            user_input.get(CONF_POWER_ENTITY_IDS)
                        ),
                        CONF_DEFAULT_DURATION: int(
                            user_input[CONF_DEFAULT_DURATION]
                        )
                        * 60,
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
            if not targets:
                return self.async_show_form(
                    step_id="init",
                    data_schema=_schema(user_input),
                    errors={CONF_TARGET_ENTITY_IDS: "no_lights"},
                )
            name = str(user_input[CONF_NAME]).strip()
            options = {
                **self.config_entry.options,
                CONF_TARGET_ENTITY_IDS: targets,
                CONF_POWER_ENTITY_IDS: _entity_list(
                    user_input.get(CONF_POWER_ENTITY_IDS)
                ),
                CONF_DEFAULT_DURATION: int(user_input[CONF_DEFAULT_DURATION]) * 60,
            }
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                title=name,
                data={CONF_NAME: name},
                options=options,
            )
            return self.async_create_entry(title="", data={})

        values = {
            **self.config_entry.options,
            CONF_NAME: self.config_entry.data.get(
                CONF_NAME, self.config_entry.title
            ),
        }
        return self.async_show_form(
            step_id="init", data_schema=_schema(values, persisted=True)
        )
