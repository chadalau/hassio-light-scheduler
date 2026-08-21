"""Small persistent runtime store shared by Light Scheduler zones."""
from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORE_KEY, STORE_VERSION


class RuntimeStore:
    """Persist bounded history and the active run needed for safe recovery.

    Every zone shares one instance, so load, mutate and save run under a lock:
    two zones starting together would otherwise each load the file and the
    second one would drop the first one's write.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self._store = Store[dict[str, Any]](hass, STORE_VERSION, STORE_KEY)
        self._data: dict[str, Any] | None = None
        self._lock = asyncio.Lock()

    async def _async_loaded(self) -> dict[str, Any]:
        if self._data is None:
            self._data = await self._store.async_load() or {}
        return self._data

    async def async_get(self, entry_id: str) -> dict[str, Any]:
        async with self._lock:
            data = await self._async_loaded()
            return dict(data.get(entry_id, {}))

    async def async_set(
        self, entry_id: str, value: dict[str, Any], *, immediate: bool = False
    ) -> None:
        async with self._lock:
            data = await self._async_loaded()
            data[entry_id] = value
            if immediate:
                await self._store.async_save(data)
            else:
                self._store.async_delay_save(lambda: self._data or {}, 2)
