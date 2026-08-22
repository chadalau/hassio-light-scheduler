"""Power pairing must survive a slow start.

Home Assistant does not promise that the plug integration has published its
sensor states by the time this integration is set up. When it has not, every
pairing resolves to nothing -- and the result used to be cached forever, so the
card showed 0,0 W and an empty pill on every light until the entry was reloaded.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant

DOMAIN = "light_scheduler"


def _zone_sensor(hass: HomeAssistant, entry):
    return next(
        state.entity_id
        for state in hass.states.async_all("sensor")
        if state.attributes.get("entry_id") == entry.entry_id
    )


async def _setup_without_meters(hass: HomeAssistant, zone_entry):
    """Set the zone up while its power sensors do not exist yet."""
    hass.states.async_set("light.sala_a", "off", {"friendly_name": "Sala A"})
    hass.states.async_set("light.sala_b", "off", {"friendly_name": "Sala B"})
    assert hass.states.get("sensor.sala_a_w") is None

    entry = zone_entry()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_a_meter_that_appears_after_setup_is_still_paired(
    hass: HomeAssistant, zone_entry
) -> None:
    entry = await _setup_without_meters(hass, zone_entry)
    sensor_id = _zone_sensor(hass, entry)

    # Nothing to read yet, which is fine.
    assert hass.states.get(sensor_id).attributes["total_power_w"] == 0

    # The plug finishes loading and publishes its meter.
    hass.states.async_set(
        "sensor.sala_a_w",
        "613.5",
        {"device_class": "power", "unit_of_measurement": "W"},
    )
    await hass.async_block_till_done()

    attributes = hass.states.get(sensor_id).attributes
    paired = {
        light["entity_id"]: light["power_entity_id"] for light in attributes["lights"]
    }
    assert paired["light.sala_a"] == "sensor.sala_a_w"
    assert attributes["total_power_w"] == 613.5


async def test_the_card_updates_without_waiting_for_a_zone_signal(
    hass: HomeAssistant, zone_entry
) -> None:
    """The meter appearing must refresh the card by itself.

    Nothing else may be relied on to do it: while the zone is idle its own
    signal only fires when a light changes, which can be hours away.
    """
    entry = await _setup_without_meters(hass, zone_entry)
    sensor_id = _zone_sensor(hass, entry)

    hass.states.async_set(
        "sensor.sala_a_w",
        "100",
        {"device_class": "power", "unit_of_measurement": "W"},
    )
    await hass.async_block_till_done()
    assert hass.states.get(sensor_id).attributes["total_power_w"] == 100

    # And it keeps following the meter afterwards.
    hass.states.async_set(
        "sensor.sala_a_w",
        "250",
        {"device_class": "power", "unit_of_measurement": "W"},
    )
    await hass.async_block_till_done()
    assert hass.states.get(sensor_id).attributes["total_power_w"] == 250


async def test_a_meter_that_goes_unavailable_and_returns_is_read_again(
    hass: HomeAssistant, zone_entry, lights
) -> None:
    entry = zone_entry()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    sensor_id = _zone_sensor(hass, entry)

    hass.states.async_set(
        "sensor.sala_a_w",
        "unavailable",
        {"device_class": "power", "unit_of_measurement": "W"},
    )
    await hass.async_block_till_done()
    assert hass.states.get(sensor_id).attributes["total_power_w"] == 0

    hass.states.async_set(
        "sensor.sala_a_w",
        "77.5",
        {"device_class": "power", "unit_of_measurement": "W"},
    )
    await hass.async_block_till_done()
    assert hass.states.get(sensor_id).attributes["total_power_w"] == 77.5
