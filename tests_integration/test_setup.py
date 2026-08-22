"""Setup, entities, services and unload, against a real Home Assistant."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr

DOMAIN = "light_scheduler"

SERVICES = (
    "turn_on_now",
    "stop",
    "add_schedule",
    "update_schedule",
    "remove_schedule",
    "set_schedules",
    "set_zone_options",
)


@pytest.mark.asyncio
async def test_a_zone_sets_up_and_creates_its_three_entities(
    hass: HomeAssistant, zone_entry, lights
) -> None:
    entry = zone_entry()

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    states = {
        state.entity_id: state
        for state in hass.states.async_all()
        if state.attributes.get("entry_id") == entry.entry_id
    }
    domains = sorted(entity_id.split(".")[0] for entity_id in states)
    assert domains == ["binary_sensor", "sensor", "switch"]


@pytest.mark.asyncio
async def test_every_service_is_registered(
    hass: HomeAssistant, zone_entry, lights
) -> None:
    entry = zone_entry()

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    for service in SERVICES:
        assert hass.services.has_service(DOMAIN, service), service


@pytest.mark.asyncio
async def test_the_status_sensor_publishes_what_the_card_reads(
    hass: HomeAssistant, zone_entry, lights
) -> None:
    entry = zone_entry()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    sensor = next(
        state
        for state in hass.states.async_all("sensor")
        if state.attributes.get("entry_id") == entry.entry_id
    )

    attributes = sensor.attributes
    assert attributes["zone_name"] == "Sala"
    assert attributes["target_entity_ids"] == ["light.sala_a", "light.sala_b"]
    assert [light["entity_id"] for light in attributes["lights"]] == [
        "light.sala_a",
        "light.sala_b",
    ]
    assert attributes["lights_on"] == 0
    assert attributes["enabled"] is True


@pytest.mark.asyncio
async def test_set_zone_options_renames_the_card_entry_and_device(
    hass: HomeAssistant, zone_entry, lights
) -> None:
    """Rename every user-facing surface without changing entity ids."""
    entry = zone_entry()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_ids = {
        domain: _zone_entity(hass, entry, domain)
        for domain in ("sensor", "switch", "binary_sensor")
    }
    registry = dr.async_get(hass)
    device = registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert device is not None
    assert device.name == "Sala"

    await hass.services.async_call(
        DOMAIN,
        "set_zone_options",
        {"entry_id": entry.entry_id, "name": "  Floração  "},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert entry.data["name"] == "Floração"
    assert entry.title == "Floração"
    device = registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    assert device is not None
    assert device.name == "Floração"
    assert {
        domain: _zone_entity(hass, entry, domain)
        for domain in ("sensor", "switch", "binary_sensor")
    } == entity_ids
    assert hass.states.get(entity_ids["sensor"]).attributes["zone_name"] == "Floração"


@pytest.mark.asyncio
async def test_set_zone_options_rejects_an_empty_zone_name(
    hass: HomeAssistant, zone_entry, lights
) -> None:
    entry = zone_entry()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError, match="não pode ficar vazio"):
        await hass.services.async_call(
            DOMAIN,
            "set_zone_options",
            {"entry_id": entry.entry_id, "name": "   "},
            blocking=True,
        )

    assert entry.data["name"] == "Sala"
    assert entry.title == "Sala"


@pytest.mark.asyncio
async def test_large_attributes_are_kept_out_of_the_recorder(
    hass: HomeAssistant, zone_entry, lights
) -> None:
    """A power reading must not rewrite the schedule list into the database.

    Read off the entity objects Home Assistant actually built, so the check
    follows the real platform setup rather than trusting a class attribute we
    could have spelled wrong.
    """
    entry = zone_entry()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    sensor = _entity_for(hass, _zone_entity(hass, entry, "sensor"))
    binary = _entity_for(hass, _zone_entity(hass, entry, "binary_sensor"))

    unrecorded_sensor = sensor._Entity__combined_unrecorded_attributes
    unrecorded_binary = binary._Entity__combined_unrecorded_attributes

    assert {"schedules", "lights", "entity_mappings"} <= unrecorded_sensor
    assert "history" in unrecorded_binary
    # Still published on the live state, which is where the card reads them.
    assert "schedules" in hass.states.get(sensor.entity_id).attributes
    assert "history" in hass.states.get(binary.entity_id).attributes


def _zone_entity(hass, entry, domain):
    return next(
        state.entity_id
        for state in hass.states.async_all(domain)
        if state.attributes.get("entry_id") == entry.entry_id
    )


def _entity_for(hass, entity_id):
    for component in hass.data["entity_components"].values():
        for entity in component.entities:
            if entity.entity_id == entity_id:
                return entity
    raise AssertionError(f"{entity_id} was not found in any platform")


@pytest.mark.asyncio
async def test_unload_removes_the_entities_and_the_services(
    hass: HomeAssistant, zone_entry, lights
) -> None:
    entry = zone_entry()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    remaining = [
        state.entity_id
        for state in hass.states.async_all()
        if state.attributes.get("entry_id") == entry.entry_id
    ]
    assert remaining == []
    # The last zone going away takes the services with it.
    for service in SERVICES:
        assert not hass.services.has_service(DOMAIN, service), service
