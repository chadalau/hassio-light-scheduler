"""Fixtures for the tests that run against a real Home Assistant.

These do not use ``tests/ha_stubs.py``. The point is the opposite: exercise the
integration through Home Assistant's own loader, config entry machinery,
dispatcher, entity platforms and Store, so that setup, unload, service
registration and restart recovery are checked against the real APIs instead of
against our idea of them.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

pytest_plugins = "pytest_homeassistant_custom_component"

REPO = Path(__file__).parents[1]
DOMAIN = "light_scheduler"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load anything under custom_components."""
    yield


@pytest.fixture(autouse=True)
def install_integration(hass):
    """Place the integration where Home Assistant's loader looks for it.

    The harness runs Home Assistant against its own throwaway config directory,
    so the component has to be copied in rather than imported from the repo.
    Copying (not linking) also proves the shipped tree is self-contained: a file
    left out of the package would fail here.
    """
    target = Path(hass.config.config_dir) / "custom_components" / DOMAIN
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(
        REPO / "custom_components" / DOMAIN,
        target,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    yield
    shutil.rmtree(target, ignore_errors=True)


@pytest.fixture
def zone_entry(hass):
    """A configured zone with two lights, one of them metered."""

    def _make(**overrides):
        options = {
            "enabled": True,
            "target_entity_ids": ["light.sala_a", "light.sala_b"],
            "power_entity_ids": ["sensor.sala_a_w"],
            "entity_mappings": [
                {
                    "name": "",
                    "target_entity_id": "light.sala_a",
                    "power_entity_id": "sensor.sala_a_w",
                    "power_threshold_w": 1.0,
                },
                {
                    "name": "",
                    "target_entity_id": "light.sala_b",
                    "power_entity_id": "",
                    "power_threshold_w": 1.0,
                },
            ],
            "default_duration": 3600,
            "max_duration": 86400,
            "schedules": [],
        }
        options.update(overrides)
        entry = MockConfigEntry(
            domain=DOMAIN,
            title="Sala",
            data={"name": "Sala"},
            options=options,
        )
        entry.add_to_hass(hass)
        return entry

    return _make


@pytest.fixture
def lights(hass):
    """Put the zone's entities in the state machine, switched off."""
    hass.states.async_set("light.sala_a", "off", {"friendly_name": "Sala A"})
    hass.states.async_set("light.sala_b", "off", {"friendly_name": "Sala B"})
    hass.states.async_set(
        "sensor.sala_a_w",
        "0",
        {"device_class": "power", "unit_of_measurement": "W"},
    )
