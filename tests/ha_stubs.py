"""Minimal Home Assistant stubs for testing the scheduler without HA.

Shared by the scheduler test modules so they all get the same stub surface:
whichever module installs the stubs first would otherwise decide what the
others see, and a stub that is too thin (a ``parse_datetime`` that always
returns ``None``, say) silently disables the logic under test.
"""

from __future__ import annotations

import asyncio
import sys
import types
from datetime import UTC, datetime
from pathlib import Path

PACKAGE_DIR = (
    Path(__file__).resolve().parents[1] / "custom_components" / "light_scheduler"
)


def _parse_datetime(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _as_utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def install_homeassistant_stubs() -> None:
    """Register the slice of Home Assistant the scheduler imports."""
    if "homeassistant" in sys.modules:
        return

    def module(name: str, **attributes: object) -> types.ModuleType:
        created = types.ModuleType(name)
        for key, value in attributes.items():
            setattr(created, key, value)
        sys.modules[name] = created
        return created

    module("homeassistant")
    module("homeassistant.config_entries", ConfigEntry=object)
    module(
        "homeassistant.const",
        EVENT_HOMEASSISTANT_STARTED="homeassistant_started",
        STATE_OFF="off",
        STATE_ON="on",
        STATE_UNAVAILABLE="unavailable",
        STATE_UNKNOWN="unknown",
        Platform=types.SimpleNamespace(
            SWITCH="switch", SENSOR="sensor", BINARY_SENSOR="binary_sensor"
        ),
    )
    module(
        "homeassistant.core",
        CoreState=types.SimpleNamespace(running="running"),
        Event=object,
        HomeAssistant=object,
        State=object,
        callback=lambda func: func,
    )
    module(
        "homeassistant.exceptions",
        ServiceValidationError=type("ServiceValidationError", (Exception,), {}),
    )
    module("homeassistant.helpers")
    module(
        "homeassistant.helpers.device_registry",
        async_get=lambda hass: types.SimpleNamespace(
            async_get_device=lambda **kwargs: None,
            async_update_device=lambda *args, **kwargs: None,
        ),
    )
    module(
        "homeassistant.helpers.dispatcher",
        async_dispatcher_send=lambda *a, **k: None,
    )
    module(
        "homeassistant.helpers.event",
        async_track_point_in_time=lambda *a, **k: (lambda: None),
        async_track_state_change_event=lambda *a, **k: (lambda: None),
    )
    module("homeassistant.helpers.storage", Store=object)
    module("homeassistant.util")
    module(
        "homeassistant.util.dt",
        utcnow=lambda: datetime.now(UTC),
        now=lambda: datetime.now(UTC),
        parse_datetime=_parse_datetime,
        as_utc=_as_utc,
    )


def load_scheduler() -> types.ModuleType:
    """Import the scheduler module without executing the package __init__."""
    install_homeassistant_stubs()
    if "light_scheduler" not in sys.modules:
        package = types.ModuleType("light_scheduler")
        package.__path__ = [str(PACKAGE_DIR)]
        sys.modules["light_scheduler"] = package
    import light_scheduler.scheduler as scheduler_module

    return scheduler_module


class FakeState:
    def __init__(self, state: str, **attributes: object) -> None:
        self.state = state
        self.attributes = dict(attributes)


class FakeHass:
    """Records service calls and serves whatever states the test sets."""

    def __init__(self, states: dict[str, FakeState], hang: bool = False) -> None:
        self.states = types.SimpleNamespace(get=states.get)
        self.calls: list[tuple[str, str]] = []
        self.hang = hang
        self.services = types.SimpleNamespace(async_call=self._async_call)
        self.tasks: list[object] = []

    def async_create_task(self, coroutine):
        """Schedule work the way Home Assistant does: queued, not run inline."""
        task = asyncio.ensure_future(coroutine)
        self.tasks.append(task)
        return task

    @property
    def loop(self):
        """Present when the real Home Assistant helpers are the ones running.

        ``install_homeassistant_stubs`` steps aside if the real package was
        imported first, and then the genuine dispatcher and event helpers run
        against this object. They expect these two members.
        """
        return asyncio.get_running_loop()

    def verify_event_loop_thread(self, what: str) -> None:
        """No-op: the tests drive everything from the loop thread already."""
        return None

    async def _async_call(self, domain, service, data, blocking=False):
        self.calls.append((service, data["entity_id"]))
        if self.hang:
            await asyncio.Event().wait()


def make_scheduler(scheduler_module, states, options=None, store=None):
    """Build a scheduler bound to fake hass/entry, ready to drive directly."""
    entry = types.SimpleNamespace(
        entry_id="entry",
        title="Sala",
        domain="light_scheduler",
        options=options or {},
    )
    hass = FakeHass(states)
    scheduler = scheduler_module.LightScheduler(hass, entry, store=store)
    return scheduler, hass
