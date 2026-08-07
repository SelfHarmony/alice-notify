"""Дамп структуры сценария (edit-info) по id — для изучения схемы триггеров/шагов.

    python scripts/scenario_dump.py <scenario_id>
    python scripts/scenario_dump.py --name "Прием лекарств напоминание Ижевск"
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
    require(settings, "yandex_x_token")
    args = sys.argv[1:]
    async with QuasarClient(settings) as q:
        if args and args[0] == "--name":
            name = args[1]
            sid = next((s["id"] for s in await q.scenarios() if s.get("name") == name), None)
            if not sid:
                print(f"Сценарий '{name}' не найден")
                return 1
        else:
            sid = args[0]
        info = await q.scenario_edit_info(sid)
        print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
