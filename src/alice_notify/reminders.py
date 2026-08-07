"""Напоминания пачками (гибрид).

- Конкретные даты → нативные напоминания Алисы через голосовую команду (Алиса произносит
  текст; при установке вслух подтверждает каждое).
- Повтор по дням недели → беззвучный timetable-TTS сценарий.

Формат входа:
- times: список "HH:MM" (напр. ["08:00","09:00","10:00","11:00"])
- dates: список "YYYY-MM-DD" (для конкретных дат)
- days_of_week: список из WEEKDAYS (для повторяющихся)
"""

from __future__ import annotations

import asyncio
from datetime import date

from .quasar.client import MAX_ALICE_TEXT, QuasarClient, WEEKDAYS, scenario_tts_timetable

MONTHS_GEN = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля", 5: "мая", 6: "июня",
    7: "июля", 8: "августа", 9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


def human_date(iso: str) -> str:
    """'2026-08-15' → '15 августа'."""
    d = date.fromisoformat(iso)
    return f"{d.day} {MONTHS_GEN[d.month]}"


def time_offset(hhmm: str) -> int:
    """'08:30' → секунды от полуночи."""
    h, m = hhmm.split(":")
    return int(h) * 3600 + int(m) * 60


def expand(times: list[str], dates: list[str]) -> list[tuple[str, str]]:
    """Декартово произведение дат×времён (для конкретных дат)."""
    return [(d, t) for d in dates for t in times]


async def set_specific_date_reminders(
    client: QuasarClient,
    text: str,
    times: list[str],
    dates: list[str],
    device_id: str | None = None,
    delay: float = 2.0,
) -> dict:
    """Ставит по одному нативному напоминанию на каждую (дата, время). Возвращает отчёт.

    ВНИМАНИЕ: Алиса вслух подтверждает каждое напоминание.
    """
    pairs = expand(times, dates)
    if not pairs:
        raise ValueError("Пустой список дат/времён")
    # Валидируем длину заранее (fail-fast): команда Алисе ≤ MAX_ALICE_TEXT.
    phrases = {(d, t): f"поставь напоминание на {human_date(d)} в {t} {text}" for d, t in pairs}
    longest = max(phrases.values(), key=len)
    if len(longest) > MAX_ALICE_TEXT:
        raise ValueError(
            f"Текст напоминания слишком длинный: команда «{longest}» = {len(longest)} символов "
            f"(лимит {MAX_ALICE_TEXT}). Сократите text (на дату/время уходит ~"
            f"{len(longest) - len(text)} символов)."
        )
    results = []
    for d, t in pairs:
        phrase = phrases[(d, t)]
        try:
            await client.say(phrase, device_id=device_id, is_command=True)
            results.append({"date": d, "time": t, "ok": True})
        except Exception as exc:  # noqa: BLE001
            results.append({"date": d, "time": t, "ok": False, "error": str(exc)})
        await asyncio.sleep(delay)  # дать Алисе обработать / не спамить API
    return {"kind": "voice", "count": len(pairs), "results": results}


async def set_recurring_reminder(
    client: QuasarClient,
    text: str,
    time: str,
    days_of_week: list[str],
    device_id: str | None = None,
    name: str | None = None,
) -> dict:
    """Создаёт беззвучный timetable-TTS сценарий (повтор по дням недели)."""
    bad = [d for d in days_of_week if d not in WEEKDAYS]
    if bad:
        raise ValueError(f"Неверные дни недели: {bad}. Допустимо: {WEEKDAYS}")
    # Сценарий произносит text одним TTS-шагом — тоже лимит 100 символов.
    if len(text) > MAX_ALICE_TEXT:
        raise ValueError(
            f"Текст напоминания не длиннее {MAX_ALICE_TEXT} символов (сейчас {len(text)})."
        )
    device_id = device_id or client.settings.station_device_id
    if not device_id:
        raise RuntimeError("Не задан STATION_DEVICE_ID")
    scen_name = name or f"alice-notify reminder {time} {text[:20]}"
    spec = scenario_tts_timetable(scen_name, device_id, text, time_offset(time), days_of_week)
    scenario_id = await client.create_scenario(spec)
    return {"kind": "scenario", "scenario_id": scenario_id, "name": scen_name}
