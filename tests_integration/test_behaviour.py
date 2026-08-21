"""Runs, persistence, services and ownership, against a real Home Assistant.

Everything here goes through Home Assistant's own service registry, config
entry options and Store. The stub suite can only assert that we call the right
methods; these assert that Home Assistant does what we expect with them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

DOMAIN = "light_scheduler"
STORE_KEY = "light_scheduler.runtime"


@pytest.fixture
def obedient_lights(hass: HomeAssistant, lights):
    """Make homeassistant.turn_on/turn_off actually move the states.

    Without this the calls vanish into a mock and confirmation would burn the
    full grace period before failing, so the test would be asserting nothing.
    """
    calls: list[tuple[str, str]] = []

    async def _apply(call: ServiceCall) -> None:
        target = call.data["entity_id"]
        entity_ids = [target] if isinstance(target, str) else list(target)
        state = "on" if call.service == "turn_on" else "off"
        for entity_id in entity_ids:
            calls.append((call.service, entity_id))
            hass.states.async_set(entity_id, state)
            if entity_id == "light.sala_a":
                hass.states.async_set(
                    "sensor.sala_a_w",
                    "42" if state == "on" else "0",
                    {"device_class": "power", "unit_of_measurement": "W"},
                )

    hass.services.async_register("homeassistant", "turn_on", _apply)
    hass.services.async_register("homeassistant", "turn_off", _apply)
    return calls


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _zone_sensor(hass: HomeAssistant, entry: MockConfigEntry):
    return next(
        state
        for state in hass.states.async_all("sensor")
        if state.attributes.get("entry_id") == entry.entry_id
    )


async def test_turn_on_now_lights_the_zone(
    hass: HomeAssistant, zone_entry, obedient_lights
) -> None:
    entry = zone_entry()
    await _setup(hass, entry)

    await hass.services.async_call(
        DOMAIN, "turn_on_now", {"entry_id": entry.entry_id}, blocking=True
    )
    await hass.async_block_till_done()

    assert obedient_lights == [
        ("turn_on", "light.sala_a"),
        ("turn_on", "light.sala_b"),
    ]
    assert _zone_sensor(hass, entry).attributes["active"] is True


async def test_stop_turns_the_zone_off_again(
    hass: HomeAssistant, zone_entry, obedient_lights
) -> None:
    entry = zone_entry()
    await _setup(hass, entry)

    await hass.services.async_call(
        DOMAIN, "turn_on_now", {"entry_id": entry.entry_id}, blocking=True
    )
    await hass.async_block_till_done()
    await hass.services.async_call(
        DOMAIN, "stop", {"entry_id": entry.entry_id}, blocking=True
    )
    await hass.async_block_till_done()

    assert ("turn_off", "light.sala_a") in obedient_lights
    assert ("turn_off", "light.sala_b") in obedient_lights
    assert _zone_sensor(hass, entry).attributes["active"] is False


async def test_the_active_run_is_written_to_the_store(
    hass: HomeAssistant, zone_entry, obedient_lights, hass_storage
) -> None:
    entry = zone_entry()
    await _setup(hass, entry)

    await hass.services.async_call(
        DOMAIN, "turn_on_now", {"entry_id": entry.entry_id}, blocking=True
    )
    await hass.async_block_till_done()

    stored = hass_storage[STORE_KEY]["data"][entry.entry_id]
    assert stored["active_run"]["targets"] == ["light.sala_a", "light.sala_b"]
    assert stored["active_run"]["source"] == "manual"


async def test_a_run_that_outlived_a_restart_is_resumed(
    hass: HomeAssistant, zone_entry, obedient_lights, hass_storage
) -> None:
    """The off time must survive a restart, not the lights staying on forever."""
    entry = zone_entry()
    started = datetime.now(UTC) - timedelta(minutes=10)
    hass_storage[STORE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": STORE_KEY,
        "data": {
            entry.entry_id: {
                "history": [],
                "active_run": {
                    "started_at": started.isoformat(),
                    "finishes_at": (started + timedelta(hours=2)).isoformat(),
                    "source": "schedule",
                    "interval": 0,
                    "targets": ["light.sala_a"],
                    "schedule_id": None,
                },
            }
        },
    }

    await _setup(hass, entry)

    attributes = _zone_sensor(hass, entry).attributes
    assert attributes["active"] is True
    assert attributes["source"] == "schedule"
    # Resuming must not re-actuate anything; it only re-arms the off time.
    assert obedient_lights == []


async def test_a_run_whose_off_time_passed_during_downtime_is_closed(
    hass: HomeAssistant, zone_entry, obedient_lights, hass_storage
) -> None:
    entry = zone_entry()
    started = datetime.now(UTC) - timedelta(hours=5)
    hass_storage[STORE_KEY] = {
        "version": 1,
        "minor_version": 1,
        "key": STORE_KEY,
        "data": {
            entry.entry_id: {
                "history": [],
                "active_run": {
                    "started_at": started.isoformat(),
                    "finishes_at": (started + timedelta(hours=1)).isoformat(),
                    "source": "schedule",
                    "interval": 0,
                    "targets": ["light.sala_a"],
                    "schedule_id": None,
                },
            }
        },
    }

    await _setup(hass, entry)

    assert ("turn_off", "light.sala_a") in obedient_lights
    assert _zone_sensor(hass, entry).attributes["active"] is False


async def test_a_schedule_round_trips_through_the_config_entry(
    hass: HomeAssistant, zone_entry, lights
) -> None:
    entry = zone_entry()
    await _setup(hass, entry)

    await hass.services.async_call(
        DOMAIN,
        "add_schedule",
        {
            "entry_id": entry.entry_id,
            "time": "18:30:00",
            "days": [0, 1, 2, 3, 4],
            "duration": 7200,
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    schedules = entry.options["schedules"]
    assert len(schedules) == 1
    assert schedules[0]["time"] == "18:30"
    assert schedules[0]["duration"] == 7200
    assert schedules[0]["target_entity_ids"] == []

    schedule_id = schedules[0]["id"]
    await hass.services.async_call(
        DOMAIN,
        "update_schedule",
        {"entry_id": entry.entry_id, "id": schedule_id, "enabled": False},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert entry.options["schedules"][0]["enabled"] is False
    # A paused schedule is no longer a candidate, so the zone has nothing next.
    assert _zone_sensor(hass, entry).state in ("unknown", "unavailable")


async def test_removing_a_light_disables_the_schedule_pinned_to_it(
    hass: HomeAssistant, zone_entry, lights
) -> None:
    """The 0.8.0 C2 fix, through the real service instead of the helper."""
    entry = zone_entry()
    await _setup(hass, entry)

    await hass.services.async_call(
        DOMAIN,
        "add_schedule",
        {
            "entry_id": entry.entry_id,
            "time": "18:30:00",
            "days": [0],
            "duration": 3600,
            "target_entity_ids": ["light.sala_b"],
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    await hass.services.async_call(
        DOMAIN,
        "set_zone_options",
        {
            "entry_id": entry.entry_id,
            "entity_mappings": [
                {"name": "", "target_entity_id": "light.sala_a", "power_entity_id": ""}
            ],
        },
        blocking=True,
    )
    await hass.async_block_till_done()

    schedule = entry.options["schedules"][0]
    assert schedule["target_entity_ids"] == []
    assert schedule["enabled"] is False
    assert schedule["warning"] == "targets_removed"


async def test_a_light_cannot_be_taken_from_another_zone(
    hass: HomeAssistant, zone_entry, lights
) -> None:
    """The audit's M2, enforced by the service the card calls."""
    first = zone_entry()
    await _setup(hass, first)

    second = MockConfigEntry(
        domain=DOMAIN,
        title="Corredor",
        data={"name": "Corredor"},
        options={
            "enabled": True,
            "target_entity_ids": ["light.corredor"],
            "power_entity_ids": [],
            "entity_mappings": [
                {"name": "", "target_entity_id": "light.corredor", "power_entity_id": ""}
            ],
            "default_duration": 3600,
            "max_duration": 86400,
            "schedules": [],
        },
    )
    second.add_to_hass(hass)
    hass.states.async_set("light.corredor", "off")
    await _setup(hass, second)

    with pytest.raises(ServiceValidationError, match="outra zona"):
        await hass.services.async_call(
            DOMAIN,
            "set_zone_options",
            {
                "entry_id": second.entry_id,
                "entity_mappings": [
                    {
                        "name": "",
                        "target_entity_id": "light.corredor",
                        "power_entity_id": "",
                    },
                    {
                        "name": "",
                        "target_entity_id": "light.sala_a",
                        "power_entity_id": "",
                    },
                ],
            },
            blocking=True,
        )

    assert second.options["target_entity_ids"] == ["light.corredor"]


async def test_the_card_resource_is_served(hass: HomeAssistant, zone_entry, lights) -> None:
    entry = zone_entry()
    await _setup(hass, entry)

    assert hass.data[DOMAIN].get("frontend_path_registered") is True
