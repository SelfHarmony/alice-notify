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
from .quasar.client import PALETTE_COLORS
from .service import flatten_devices, get_client

# Кросс-срезовая памятка. Специфику (напр. цвет) держим ЗДЕСЬ и в профильных тулах,
# а не в описаниях общих механизмов (сценарии), чтобы не путать агента в не-цветовых задачах.
SERVER_INSTRUCTIONS = """\
Управление умным домом Яндекса («Дом с Алисой»): колонки, лампы, розетки, сценарии.

ПРОВЕРКА РЕЗУЛЬТАТА: status="ok" сам по себе НЕ гарантирует нужный эффект. Если в ответе есть
devices[].capabilities[].state.action_result.status — убедись, что там "DONE".

ЦВЕТ/БЕЛЫЙ У ЛАМП (частый источник ошибок — читать перед сменой цвета):
- Меняй цвет ТОЛЬКО через палитру: alice_set_light_color(device_id, color_id) либо
  alice_device_action(color_setting, instance="color", value="<id строкой>").
- НЕ используй instance hsv / rgb / scene / temperature_k — облако Яндекса их не принимает
  (400 BAD_REQUEST или ложный успех без смены цвета).
- Набор цветов РАЗЛИЧАЕТСЯ по лампам: alice_get_light_colors(device_id) — точная палитра именно
  этой лампы; alice_list_colors() — общий список платформы (может быть шире палитры устройства).
- Тот же приём внутри сценария: шаг color_setting.state = {"instance":"color","value":"<id>"}
  (value — СТРОКА). Если create сценария сразу с цветом даёт 400 — создай шаги только с on_off,
  затем alice_update_scenario добавит цвет. Один цвет на группу ламп — item_type="group",
  разные цвета — отдельные device-шаги.

ЛИМИТЫ: команда Алисе и текст напоминания ≤100 символов; alice_say длинный текст режет сам.
"""

mcp = FastMCP("alice-notify", instructions=SERVER_INSTRUCTIONS)


# --- Речь / команды ---
@mcp.tool()
async def alice_say(text: str, device_id: str | None = None) -> dict[str, Any]:
    """Озвучить произвольный текст на колонке (TTS). Длинный текст режется автоматически.
    device_id опционален (без него — колонка по умолчанию). Для управления устройствами это
    не подходит — используй alice_device_action."""
    return await get_client().say(text, device_id=device_id)


@mcp.tool()
async def alice_command(text: str, device_id: str | None = None) -> dict[str, Any]:
    """Отправить колонке голосовую команду (Алиса выполнит как сказанную вслух). ≤100 символов.
    status=ok не значит, что Алиса поняла именно задуманное. Для точного управления устройствами
    надёжнее alice_device_action / сценарий, а не голос."""
    return await get_client().say(text, device_id=device_id, is_command=True)


# --- Напоминания ---
@mcp.tool()
async def alice_set_date_reminders(
    text: str, times: list[str], dates: list[str], device_id: str | None = None
) -> dict[str, Any]:
    """Пачка напоминаний на КОНКРЕТНЫЕ даты. times=['08:00',...], dates=['2026-08-15',...].
    Алиса вслух подтверждает каждое. Собранная команда ≤100 символов — text держи коротким."""
    return await rem.set_specific_date_reminders(get_client(), text, times, dates, device_id)


@mcp.tool()
async def alice_set_recurring_reminder(
    text: str, time: str, days_of_week: list[str], device_id: str | None = None
) -> dict[str, Any]:
    """Беззвучное повторяющееся напоминание по дням недели (timetable-TTS сценарий).
    time='08:00'; days_of_week из monday..sunday; text ≤100 символов."""
    return await rem.set_recurring_reminder(get_client(), text, time, days_of_week, device_id)


# --- Устройства ---
@mcp.tool()
async def alice_list_devices() -> list[dict[str, Any]]:
    """Список устройств умного дома: id, name, type, room, названия умений (capabilities)."""
    return flatten_devices(await get_client().devices())


@mcp.tool()
async def alice_device_action(
    device_id: str, capability_type: str, instance: str, value: Any, item_type: str = "device"
) -> dict[str, Any]:
    """Низкоуровневое управление умением устройства.
    Вкл/выкл: capability_type='devices.capabilities.on_off', instance='on', value=true|false (boolean).
    Цвет — только через палитру (см. памятку сервера / alice_set_light_color).
    item_type='device' (по умолчанию) или 'group'."""
    return await get_client().device_action(device_id, capability_type, instance, value, item_type)


# --- Цвет ламп (профильные тулы) ---
@mcp.tool()
async def alice_list_colors() -> dict[str, str]:
    """Общий список id цветов платформы Яндекса (id → русское название). Набор конкретной лампы
    может быть уже — точную палитру устройства даёт alice_get_light_colors(device_id)."""
    return dict(PALETTE_COLORS)


@mcp.tool()
async def alice_get_light_colors(device_id: str) -> dict[str, Any]:
    """Палитра, реально поддерживаемая ЭТОЙ лампой (из её color_setting): {palette:[id...],
    temperature_k:{min,max}|None, color_model|None}. Точный источник валидных цветов для лампы."""
    return await get_client().get_light_colors(device_id)


@mcp.tool()
async def alice_set_light_color(device_id: str, color_id: str) -> dict[str, Any]:
    """Задать именованный цвет/белый лампе (надёжный способ смены цвета).
    color_id — строка из alice_get_light_colors/alice_list_colors (red, blue, cold_white, ...)."""
    return await get_client().set_light_color(device_id, color_id)


@mcp.tool()
async def alice_set_lights(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Пакетно настроить несколько ламп (напр. радуга). items — список объектов:
    {"device_id": "...", "color": "<id палитры, опц.>", "on": true|false (опц.)}.
    Применяет on/off и/или цвет к каждой лампе; возвращает результат по каждой."""
    client = get_client()
    results: list[dict[str, Any]] = []
    for it in items:
        did = it.get("device_id")
        entry: dict[str, Any] = {"device_id": did}
        try:
            if "on" in it:
                await client.device_action(did, "devices.capabilities.on_off", "on", bool(it["on"]))
            if it.get("color"):
                await client.set_light_color(did, it["color"])
            entry["ok"] = True
        except Exception as exc:  # noqa: BLE001
            entry["ok"] = False
            entry["error"] = str(exc)
        results.append(entry)
    return {"results": results}


# --- Сценарии (общий механизм; специфику умений см. в памятке/профильных тулах) ---
@mcp.tool()
async def alice_list_scenarios() -> list[dict[str, Any]]:
    """Список сценариев: id и name."""
    return [{"id": s.get("id"), "name": s.get("name")} for s in await get_client().scenarios()]


@mcp.tool()
async def alice_get_scenario(scenario_id: str) -> dict[str, Any]:
    """Полная (обогащённая сервером) структура сценария. Не копируй её в create/update «как есть» —
    отправляй минимальную версию (лишние обогащённые поля могут дать 400)."""
    return await get_client().scenario_edit_info(scenario_id)


@mcp.tool()
async def alice_create_scenario(spec: dict[str, Any]) -> dict[str, Any]:
    """Создать сценарий. spec: name, icon, triggers, steps, settings.
    Голосовой триггер: {"trigger":{"type":"scenario.trigger.voice","value":"ФразаБезСловаАлиса"}}.
    Шаги: type='scenarios.steps.actions.v2' → items (item_type 'device' или 'group').
    Значения в состояниях умений — по тем же правилам, что и в alice_device_action.
    Отправляй минимальный spec (не обогащённый вывод alice_get_scenario). Возвращает scenario_id."""
    return {"scenario_id": await get_client().create_scenario(spec)}


@mcp.tool()
async def alice_update_scenario(scenario_id: str, spec: dict[str, Any]) -> dict[str, Any]:
    """Заменить сценарий новым JSON (схема как в alice_create_scenario). Значения умений — по тем же
    правилам, что и в alice_device_action; отправляй минимальный spec."""
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
