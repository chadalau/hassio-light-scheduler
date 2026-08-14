"""Small persistent runtime store shared by Light Scheduler zones."""
from __future__ import annotations

from typing import Any
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from .const import STORE_KEY, STORE_VERSION


class RuntimeStore:
    """Persist bounded history and the active run needed for safe recovery."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store = Store[dict[str, Any]](hass, STORE_VERSION, STORE_KEY)
        self._data: dict[str, Any] | None = None

    async def async_get(self, entry_id: str) -> dict[str, Any]:
        if self._data is None:
            self._data = await self._store.async_load() or {}
        return dict(self._data.get(entry_id, {}))

    async def async_set(
        self, entry_id: str, value: dict[str, Any], *, immediate: bool = False
    ) -> None:
        if self._data is None:
            self._data = await self._store.async_load() or {}
        self._data[entry_id] = value
        if immediate:
            await self._store.async_save(self._data)
        else:
            self._store.async_delay_save(lambda: self._data or {}, 2)
