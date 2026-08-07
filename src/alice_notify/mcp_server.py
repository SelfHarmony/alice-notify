"""MCP-сервер alice-notify — управление умным домом с Алисой из LLM.

Запуск (stdio):
    pip install -e ".[mcp]"
    python -m alice_notify.mcp_server

Подключение в Claude Code:
    claude mcp add alice-notify -- python -m alice_notify.mcp_server
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from . import reminders as rem
from .service import flatten_devices, get_client

mcp = FastMCP("alice-notify")


# --- Речь / команды ---
@mcp.tool()
async def alice_say(text: str, device_id: str | None = None) -> dict[str, Any]:
    """Заставить колонку произнести произвольный текст (TTS). device_id опционален."""
    return await get_client().say(text, device_id=device_id)


@mcp.tool()
async def alice_command(text: str, device_id: str | None = None) -> dict[str, Any]:
    """Отправить колонке голосовую команду (Алиса выполнит как сказанную вслух)."""
    return await get_client().say(text, device_id=device_id, is_command=True)


# --- Напоминания ---
@mcp.tool()
async def alice_set_date_reminders(
    text: str, times: list[str], dates: list[str], device_id: str | None = None
) -> dict[str, Any]:
    """Пачка напоминаний на КОНКРЕТНЫЕ даты. times=['08:00',...], dates=['2026-08-15',...].
    Внимание: Алиса вслух подтверждает каждое напоминание при установке."""
    return await rem.set_specific_date_reminders(get_client(), text, times, dates, device_id)


@mcp.tool()
async def alice_set_recurring_reminder(
    text: str, time: str, days_of_week: list[str], device_id: str | None = None
) -> dict[str, Any]:
    """Беззвучное повторяющееся напоминание по дням недели (timetable-TTS сценарий).
    time='08:00'; days_of_week из monday..sunday."""
    return await rem.set_recurring_reminder(get_client(), text, time, days_of_week, device_id)


# --- Устройства ---
@mcp.tool()
async def alice_list_devices() -> list[dict[str, Any]]:
    """Список устройств умного дома: id, name, type, room, умения."""
    return flatten_devices(await get_client().devices())


@mcp.tool()
async def alice_device_action(
    device_id: str, capability_type: str, instance: str, value: Any, item_type: str = "device"
) -> dict[str, Any]:
    """Управление умением устройства. Напр. свет вкл:
    capability_type='devices.capabilities.on_off', instance='on', value=true."""
    return await get_client().device_action(device_id, capability_type, instance, value, item_type)


# --- Сценарии (CRUD) ---
@mcp.tool()
async def alice_list_scenarios() -> list[dict[str, Any]]:
    """Список сценариев: id и name."""
    return [{"id": s.get("id"), "name": s.get("name")} for s in await get_client().scenarios()]


@mcp.tool()
async def alice_get_scenario(scenario_id: str) -> dict[str, Any]:
    """Полная структура сценария (для редактирования)."""
    return await get_client().scenario_edit_info(scenario_id)


@mcp.tool()
async def alice_create_scenario(spec: dict[str, Any]) -> dict[str, Any]:
    """Создать сценарий из полного JSON-описания (triggers/steps/name/icon). Возвращает id."""
    return {"scenario_id": await get_client().create_scenario(spec)}


@mcp.tool()
async def alice_update_scenario(scenario_id: str, spec: dict[str, Any]) -> dict[str, Any]:
    """Заменить сценарий новым JSON-описанием."""
    return await get_client().update_scenario(scenario_id, spec)


@mcp.tool()
async def alice_delete_scenario(scenario_id: str) -> dict[str, Any]:
    """Удалить сценарий."""
    return await get_client().delete_scenario(scenario_id)


@mcp.tool()
async def alice_run_scenario(scenario_id: str) -> dict[str, Any]:
    """Запустить сценарий."""
    return await get_client().run_scenario(scenario_id)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
