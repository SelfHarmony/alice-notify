"""Общий ленивый QuasarClient для MCP и FastAPI + утилиты представления."""

from __future__ import annotations

from typing import Any

from .config import get_settings
from .quasar import QuasarClient

_client: QuasarClient | None = None


def get_client() -> QuasarClient:
    global _client
    if _client is None:
        _client = QuasarClient(get_settings())
    return _client


def flatten_devices(devices_raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Компактный список устройств: id, name, type, room, умения (типы)."""
    out: list[dict[str, Any]] = []
    for house in devices_raw.get("households", []):
        for d in house.get("all", []):
            out.append(
                {
                    "id": d.get("id"),
                    "name": d.get("name"),
                    "type": d.get("type"),
                    "room": d.get("room_name"),
                    "capabilities": [c.get("type") for c in (d.get("capabilities") or [])],
                }
            )
    return out
