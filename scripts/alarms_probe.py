"""Read-only: показать текущие будильники/напоминания станции (для изучения схемы).

    python scripts/alarms_probe.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from alice_notify.config import get_settings, require  # noqa: E402
from alice_notify.quasar import QuasarClient  # noqa: E402


async def main() -> int:
    settings = get_settings()
    require(settings, "yandex_x_token", "station_device_id")
    async with QuasarClient(settings) as q:
        qinfo = await q.speaker_config(settings.station_device_id)
        print("quasar_info:", json.dumps(qinfo, ensure_ascii=False))
        alarms = await q.get_alarms()
        print("\nget_alarms:")
        print(json.dumps(alarms, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
