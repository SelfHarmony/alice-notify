"""Стадия 3 — MVP: заставить Алису произнести произвольный текст (гейт G_MVP).

Запуск:
    python scripts/say_probe.py                 # фраза по умолчанию
    python scripts/say_probe.py "Ужин готов"

Требует в .env: YANDEX_X_TOKEN и (желательно) STATION_DEVICE_ID (из scripts/discover.py).
Если STATION_DEVICE_ID не задан — берётся первая найденная колонка.

Пробует прямой способ A (quasar.server_action / phrase_action). Печатает сырой ответ API —
по нему при необходимости уточним способ (или перейдём к TTS через сценарий, путь B).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from alice_notify.config import get_settings, require  # noqa: E402
from alice_notify.quasar import QuasarClient  # noqa: E402

DEFAULT_PHRASE = "Привет из бриджа. Проверка озвучки прошла успешно."


def _find_speaker_id(devices_raw: dict) -> str | None:
    found: list[dict] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if "id" in node and isinstance(node.get("type"), str):
                found.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(devices_raw)
    for d in found:
        if "smart_speaker" in d["type"] or d.get("quasar_info"):
            return str(d["id"])
    return None


async def main() -> int:
    settings = get_settings()
    require(settings, "yandex_x_token")
    phrase = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PHRASE

    async with QuasarClient(settings) as q:
        device_id = settings.station_device_id
        if not device_id:
            print("STATION_DEVICE_ID не задан — ищу колонку в устройствах...")
            device_id = _find_speaker_id(await q.devices())
            if not device_id:
                print("Колонка не найдена. Запустите scripts/discover.py.")
                return 1
            print(f"Использую station_device_id={device_id}")

        print(f"[TTS] Через сценарий-озвучку: {phrase!r}")
        try:
            resp = await q.say(phrase, device_id=device_id)
            print(f"[TTS] Ответ API: {resp}")
            print("\n[G_MVP] Проверьте колонку: если произнесла текст — MVP достигнут.")
            return 0
        except Exception as exc:
            print(f"[TTS] Не сработало: {exc}")
            print("\n→ Пришлите этот вывод — уточним тело запроса под ваш аккаунт.")
            return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
