"""Стадия 2 — дискавери устройств и сценариев (гейты G_API / G_TTS_CAP).

Запуск:
    pip install -e .
    python scripts/discover.py

Печатает устройства (id, name, type, умения) и сценарии, находит станцию/колонку и
проверяет наличие умения TTS (devices.capabilities.quasar[.server_action]).
Требует в .env: YANDEX_X_TOKEN (см. scripts/get_token.py).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from alice_notify.config import get_settings, require  # noqa: E402
from alice_notify.quasar import QuasarClient  # noqa: E402

SPEAKER_MARK = "smart_speaker"


def _collect_devices(node: Any, out: list[dict]) -> None:
    """Рекурсивно собирает все объекты-устройства (есть 'id' и 'type')."""
    if isinstance(node, dict):
        if "id" in node and "type" in node and isinstance(node.get("type"), str):
            out.append(node)
        for v in node.values():
            _collect_devices(v, out)
    elif isinstance(node, list):
        for v in node:
            _collect_devices(v, out)


def _cap_types(device: dict) -> list[str]:
    caps = device.get("capabilities") or []
    return [c.get("type", "?") for c in caps if isinstance(c, dict)]


async def main() -> int:
    settings = get_settings()
    require(settings, "yandex_x_token")

    async with QuasarClient(settings) as q:
        devices_raw = await q.devices()
        scenarios_raw = await q.scenarios()

    devices: list[dict] = []
    _collect_devices(devices_raw, devices)
    # уникализируем по id
    seen: dict[str, dict] = {}
    for d in devices:
        seen.setdefault(str(d["id"]), d)
    devices = list(seen.values())

    print(f"[G_API] Устройств найдено: {len(devices)}\n")
    speakers: list[dict] = []
    for d in devices:
        caps = _cap_types(d)
        is_speaker = SPEAKER_MARK in d["type"] or bool(d.get("quasar_info"))
        mark = " ← КОЛОНКА" if is_speaker else ""
        print(f"  • {d.get('name','?')}{mark}")
        print(f"      id={d['id']}")
        print(f"      type={d['type']}")
        if caps:
            print(f"      умения: {', '.join(caps)}")
        if is_speaker:
            speakers.append(d)
    print()

    # G_TTS_CAP
    if speakers:
        station = speakers[0]
        caps = _cap_types(station)
        has_quasar = any("quasar" in c for c in caps)
        print("[G_TTS_CAP] Кандидат-станция:")
        print(f"      STATION_DEVICE_ID={station['id']}  (впишите в .env)")
        print(f"      умения: {', '.join(caps) or '—'}")
        if has_quasar:
            print("      ✓ есть умение quasar — прямой TTS/команды вероятно доступны.")
        else:
            print("      ⚠ умения quasar не видно в capabilities — TTS, вероятно, через сценарий.")
    else:
        print("[G_TTS_CAP] Колонок не найдено. Проверьте, что станция привязана к аккаунту.")

    # сценарии
    scen = scenarios_raw.get("scenarios") if isinstance(scenarios_raw, dict) else None
    if isinstance(scen, list):
        print(f"\nСценариев: {len(scen)}")
        for s in scen[:20]:
            print(f"  • {s.get('name','?')}  id={s.get('id')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
