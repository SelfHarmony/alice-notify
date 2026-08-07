"""QuasarClient — обёртка над неофициальным API iot.quasar.yandex.ru.

Основано на AlexxIT/YandexStation. Ключевые факты:
- Озвучка/команды на колонку — прямой device-action server_action: phrase_action (произнести
  текст, лимит ~550) и text_action (голосовая команда, лимит 100).
- Управление устройствами (свет, розетки): POST /user/{item_type}s/{id}/actions.
- Сценарии — отдельный CRUD (/user/scenarios); повторяющиеся напоминания = timetable-TTS сценарий.
- Напоминания/будильники (структурные) — gproxy API (rpc.alice.yandex.ru).
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import httpx

# Минимальный интервал между запросами к API (антифлуд, как в AlexxIT)
MIN_REQUEST_INTERVAL = 0.3

# Лимит на голосовую КОМАНДУ (text_action) и на шаг сценария — 100 символов.
MAX_ALICE_TEXT = 100
# Лимит прямой ОЗВУЧКИ фразой (phrase_action на устройство) — ~550, берём с запасом.
MAX_TTS_TEXT = 500
# Оценка скорости речи Алисы (символов/сек) — для пауз между чанками при длинном тексте.
SPEAK_CHARS_PER_SEC = 14

# Именованные цвета палитры умного дома Яндекса (instance="color", value=<id>).
# Это ЕДИНСТВЕННЫЙ надёжный способ задать цвет через это облако: instance hsv/rgb/scene/
# temperature_k у color_setting облачным API не поддерживаются (возвращают 400 BAD_REQUEST).
PALETTE_COLORS: dict[str, str] = {
    "soft_white": "мягкий белый", "warm_white": "тёплый белый", "white": "белый",
    "daylight": "дневной белый", "cold_white": "холодный белый",
    "red": "красный", "coral": "коралловый", "orange": "оранжевый", "yellow": "жёлтый",
    "lime": "салатовый", "green": "зелёный", "emerald": "изумрудный",
    "turquoise": "бирюзовый", "cyan": "голубой", "blue": "синий", "moonlight": "лунный",
    "lavender": "сиреневый", "violet": "фиолетовый", "purple": "пурпурный",
    "orchid": "розовый", "raspberry": "малиновый", "mauve": "лиловый",
}

from ..config import Settings
from .auth import YandexAuth

# Заголовки для gproxy API будильников/напоминаний (rpc.alice.yandex.ru)
ALARM_HEADERS = {
    "accept": "application/json",
    "origin": "https://yandex.ru",
    "x-ya-app-type": "iot-app",
    "x-ya-application": '{"app_id":"unknown","uuid":"unknown","lang":"ru"}',
}


WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def chunk_text(text: str, max_len: int = 200) -> list[str]:
    """Режет длинный текст на части ≤ max_len по границам предложений/слов."""
    text = " ".join(text.split())
    if len(text) <= max_len:
        return [text] if text else []
    sentences = re.split(r"(?<=[.!?…])\s+", text)
    chunks: list[str] = []
    cur = ""
    for s in sentences:
        if not cur:
            cur = s
        elif len(cur) + 1 + len(s) <= max_len:
            cur += " " + s
        else:
            chunks.append(cur)
            cur = s
    if cur:
        chunks.append(cur)
    # добить слишком длинные части — по словам, затем жёстко
    out: list[str] = []
    for c in chunks:
        if len(c) <= max_len:
            out.append(c)
            continue
        cur = ""
        for w in c.split(" "):
            if not cur:
                cur = w
            elif len(cur) + 1 + len(w) <= max_len:
                cur += " " + w
            else:
                out.append(cur)
                cur = w
        if cur:
            out.append(cur)
    final: list[str] = []
    for c in out:
        while len(c) > max_len:
            final.append(c[:max_len])
            c = c[max_len:]
        if c:
            final.append(c)
    return final


def scenario_tts_timetable(
    name: str, device_id: str, text: str, time_offset: int, days_of_week: list[str]
) -> dict:
    """Сценарий: колонка произносит text по расписанию (день недели + время).

    time_offset — секунды от полуночи (напр. 8:00 = 28800). days_of_week — из WEEKDAYS.
    """
    cond = {"days_of_week": days_of_week, "time_offset": time_offset}
    return {
        "name": name,
        "icon": "home",
        "triggers": [
            {
                "trigger": {
                    "type": "scenario.trigger.timetable",
                    "value": {"condition": {"type": "specific_time", "value": cond}, **cond},
                }
            }
        ],
        "steps": [
            {
                "type": "scenarios.steps.actions.v2",
                "parameters": {
                    "items": [
                        {
                            "id": device_id,
                            "type": "step.action.item.device",
                            "value": {
                                "id": device_id,
                                "item_type": "device",
                                "capabilities": [
                                    {
                                        "type": "devices.capabilities.quasar",
                                        "state": {"instance": "tts", "value": {"text": text}},
                                    }
                                ],
                            },
                        }
                    ]
                },
            }
        ],
    }


class QuasarClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base = settings.quasar_base_url.rstrip("/")
        self.auth = YandexAuth(settings.yandex_x_token or "", settings.session_cache_path)
        self._last_ts = 0.0
        self._req_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self.auth.aclose()

    async def __aenter__(self) -> "QuasarClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # --- низкоуровневый запрос с ретраями ---
    @staticmethod
    def _check(data: Any, ctx: str) -> Any:
        """Бросает исключение, если API вернул status != ok (HTTP 200, но логическая ошибка)."""
        if isinstance(data, dict) and data.get("status") not in (None, "ok"):
            raise RuntimeError(f"quasar {ctx}: {data}")
        return data

    async def _throttle(self) -> None:
        async with self._req_lock:
            wait = self._last_ts + MIN_REQUEST_INTERVAL - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_ts = time.monotonic()

    async def _request(self, method: str, path: str, retry: int = 2, **kwargs: Any) -> httpx.Response:
        await self.auth.ensure()
        await self._throttle()
        url = path if path.startswith("http") else f"{self.base}{path}"

        # gproxy (будильники) не требует csrf; остальным POST/PUT/DELETE — нужен
        if method.lower() != "get" and not url.startswith("https://rpc.alice.yandex.ru"):
            headers = dict(kwargs.pop("headers", {}))
            headers["x-csrf-token"] = await self.auth.csrf_token()
            kwargs["headers"] = headers

        r = await self.auth.client.request(method, url, **kwargs)
        if r.status_code == 200:
            return r
        if retry > 0 and r.status_code == 401:  # cookie протухли
            await self.auth.login_token()
            self.auth._save_cache()
            return await self._request(method, path, retry - 1, **kwargs)
        if retry > 0 and r.status_code == 403:  # нет/устарел csrf
            self.auth.invalidate_csrf()
            return await self._request(method, path, retry - 1, **kwargs)
        raise httpx.HTTPStatusError(
            f"{method.upper()} {url} → {r.status_code}: {r.text[:600]}",
            request=r.request,
            response=r,
        )

    # --- устройства ---
    async def devices(self) -> dict[str, Any]:
        r = await self._request("get", "/v3/user/devices")
        return r.json()

    async def device_action(
        self,
        device_id: str,
        capability_type: str,
        instance: str,
        value: Any,
        item_type: str = "device",
    ) -> dict[str, Any]:
        body = {"actions": [{"type": capability_type, "state": {"instance": instance, "value": value}}]}
        r = await self._request("post", f"/user/{item_type}s/{device_id}/actions", json=body)
        return self._check(r.json(), "device_action")

    async def get_light_colors(self, device_id: str) -> dict[str, Any]:
        """Палитра, реально поддерживаемая КОНКРЕТНОЙ лампой (из её color_setting.parameters).

        Возвращает {palette:[id...], temperature_k:{min,max}|None, color_model|None}.
        Палитра у разных устройств различается — это точный источник, в отличие от общего списка.
        """
        r = await self._request("get", f"/user/devices/{device_id}")
        dev = self._check(r.json(), "get_device")
        for cap in dev.get("capabilities", []):
            if cap.get("type") == "devices.capabilities.color_setting":
                p = cap.get("parameters", {}) or {}
                return {
                    "palette": [c.get("id") for c in (p.get("palette") or [])],
                    "temperature_k": p.get("temperature_k"),
                    "color_model": p.get("color_model"),
                }
        return {"palette": [], "temperature_k": None, "color_model": None}

    async def set_light_color(
        self, device_id: str, color_id: str, item_type: str = "device"
    ) -> dict[str, Any]:
        """Задать именованный цвет/белый из палитры Яндекса (надёжный способ смены цвета)."""
        if color_id not in PALETTE_COLORS:
            raise ValueError(
                f"Неизвестный цвет '{color_id}'. Доступные id: {', '.join(PALETTE_COLORS)}"
            )
        return await self.device_action(
            device_id, "devices.capabilities.color_setting", "color", color_id, item_type
        )

    # --- сценарии (CRUD) ---
    async def scenarios(self) -> list[dict[str, Any]]:
        r = await self._request("get", "/user/scenarios")
        return r.json().get("scenarios", [])

    async def create_scenario(self, spec: dict[str, Any]) -> str:
        r = await self._request("post", "/v4/user/scenarios", json=spec)
        return self._check(r.json(), "create_scenario")["scenario_id"]

    async def scenario_edit_info(self, scenario_id: str) -> dict[str, Any]:
        r = await self._request("get", f"/v4/user/scenarios/{scenario_id}/edit")
        return r.json()["scenario"]

    async def update_scenario(self, scenario_id: str, spec: dict[str, Any]) -> dict[str, Any]:
        r = await self._request("put", f"/v4/user/scenarios/{scenario_id}", json=spec)
        return self._check(r.json(), "update_scenario")

    async def delete_scenario(self, scenario_id: str) -> dict[str, Any]:
        r = await self._request("delete", f"/user/scenarios/{scenario_id}")
        return self._check(r.json(), "delete_scenario")

    async def run_scenario(self, scenario_id: str) -> dict[str, Any]:
        r = await self._request("post", f"/user/scenarios/{scenario_id}/actions")
        return self._check(r.json(), "run_scenario")

    async def say(self, text: str, device_id: str | None = None, is_command: bool = False) -> dict[str, Any]:
        """Колонка произносит text (is_command=False) или выполняет как голосовую команду (True).

        Озвучка идёт прямой командой phrase_action на устройство (лимит ~550), длинный текст
        режется на несколько фраз подряд. Команда (is_command) — через text_action (лимит 100).
        """
        device_id = device_id or self.settings.station_device_id
        if not device_id:
            raise RuntimeError("Не задан STATION_DEVICE_ID (см. scripts/discover.py)")

        if is_command:
            # Голосовую команду/напоминание нельзя разбить — валидируем длину.
            if len(text) > MAX_ALICE_TEXT:
                raise ValueError(
                    f"Команда Алисе не длиннее {MAX_ALICE_TEXT} символов "
                    f"(сейчас {len(text)}). Сократите текст."
                )
            await self.device_action(
                device_id, "devices.capabilities.quasar.server_action", "text_action", text
            )
            return {"chunks": 1, "status": "ok"}

        # TTS: прямая озвучка phrase_action, чанки ≤ лимита прямой озвучки.
        size = min(self.settings.tts_chunk_size, MAX_TTS_TEXT)
        parts = chunk_text(text, size) or [""]
        for i, part in enumerate(parts):
            await self.device_action(
                device_id, "devices.capabilities.quasar.server_action", "phrase_action", part
            )
            if i < len(parts) - 1:
                # пауза ≈ время произнесения чанка, чтобы фразы не наложились
                await asyncio.sleep(len(part) / SPEAK_CHARS_PER_SEC + 1.5)
        return {"chunks": len(parts), "status": "ok"}

    # --- будильники / напоминания (gproxy) ---
    async def speaker_config(self, device_id: str) -> dict[str, Any]:
        """quasar_info колонки (device_id платформы + platform) — нужен для будильников."""
        r = await self._request("get", f"/user/devices/{device_id}/configuration")
        return r.json()["quasar_info"]

    async def get_alarms(self, device_id: str | None = None) -> dict[str, Any]:
        device_id = device_id or self.settings.station_device_id
        qinfo = await self.speaker_config(device_id)
        r = await self._request(
            "post",
            "https://rpc.alice.yandex.ru/gproxy/get_alarms",
            json={"device_ids": [qinfo["device_id"]]},
            headers=ALARM_HEADERS,
        )
        return r.json()

    async def create_alarm(self, alarm: dict[str, Any], device_id: str | None = None) -> dict[str, Any]:
        """Создать будильник/напоминание. alarm — словарь (см. схему из get_alarms)."""
        device_id = device_id or self.settings.station_device_id
        qinfo = await self.speaker_config(device_id)
        # device_type нужен серверу; берём из списка устройств по id
        device_type = await self._device_type(device_id)
        alarm = {**alarm, "device_id": qinfo["device_id"]}
        r = await self._request(
            "post",
            "https://rpc.alice.yandex.ru/gproxy/create_alarm",
            json={"alarm": alarm, "device_type": device_type},
            headers=ALARM_HEADERS,
        )
        return r.json()

    async def cancel_alarms(self, alarm_ids: list[str], device_id: str | None = None) -> dict[str, Any]:
        device_id = device_id or self.settings.station_device_id
        qinfo = await self.speaker_config(device_id)
        r = await self._request(
            "post",
            "https://rpc.alice.yandex.ru/gproxy/cancel_alarms",
            json={
                "device_alarm_ids": [
                    {"alarm_id": aid, "device_id": qinfo["device_id"]} for aid in alarm_ids
                ]
            },
            headers=ALARM_HEADERS,
        )
        return r.json()

    async def _device_type(self, device_id: str) -> str:
        data = await self.devices()
        for house in data.get("households", []):
            for d in house.get("all", []):
                if str(d.get("id")) == str(device_id):
                    return d.get("type", "")
        return ""
