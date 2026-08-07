"""QuasarClient — обёртка над неофициальным API iot.quasar.yandex.ru.

Основано на AlexxIT/YandexStation. Ключевые факты:
- TTS/голосовые команды на колонку идут НЕ прямым device-action, а через сценарий:
  на колонку заводится сценарий со скрытым голосовым триггером (encode(device_id)),
  перед запуском в него подставляется нужный текст, затем сценарий запускается.
- Прямой device-action (свет, розетки и т.п.): POST /user/{item_type}s/{id}/actions.
- Напоминания/будильники — отдельный gproxy API (rpc.alice.yandex.ru).
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import httpx

# Минимальный интервал между запросами к API (антифлуд, как в AlexxIT)
MIN_REQUEST_INTERVAL = 0.3

# Лимит Яндекса на длину фразы/команды Алисе (QUASAR_SERVER_ACTION_LENGTH_ERROR)
MAX_ALICE_TEXT = 100

from ..config import Settings
from .auth import YandexAuth

# --- кодирование device_id в скрытый голосовой триггер (из AlexxIT) ---
MASK_EN = "0123456789abcdef-"
MASK_RU = "оеаинтсрвлкмдпуяы"

# Заголовки для gproxy API будильников/напоминаний (rpc.alice.yandex.ru)
ALARM_HEADERS = {
    "accept": "application/json",
    "origin": "https://yandex.ru",
    "x-ya-app-type": "iot-app",
    "x-ya-application": '{"app_id":"unknown","uuid":"unknown","lang":"ru"}',
}


def encode(uid: str) -> str:
    """UID (hex + дефисы) → русские буквы: скрытый голосовой триггер сценария."""
    return "".join(MASK_RU[MASK_EN.index(s)] for s in uid)


def scenario_speaker_tts(name: str, trigger: str, device_id: str, text: str) -> dict:
    """Сценарий: колонка произносит text дословно (умение quasar/tts)."""
    return {
        "name": name,
        "icon": "home",
        "triggers": [{"trigger": {"type": "scenario.trigger.voice", "value": trigger}}],
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


def _tts_step(device_id: str, text: str) -> dict:
    return {
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


def scenario_multi_tts(name: str, trigger: str, device_id: str, chunks: list[str]) -> dict:
    """Сценарий: колонка последовательно произносит все chunks (по TTS-шагу на чанк)."""
    return {
        "name": name,
        "icon": "home",
        "triggers": [{"trigger": {"type": "scenario.trigger.voice", "value": trigger}}],
        "steps": [_tts_step(device_id, ch) for ch in chunks],
    }


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


def scenario_speaker_action(name: str, trigger: str, device_id: str, action: str) -> dict:
    """Сценарий: колонка выполняет action как голосовую команду (server_action/text_action)."""
    payload = scenario_speaker_tts(name, trigger, device_id, "")
    payload["steps"][0]["parameters"]["items"][0]["value"]["capabilities"] = [
        {
            "type": "devices.capabilities.quasar.server_action",
            "state": {"instance": "text_action", "value": action},
        }
    ]
    return payload


class QuasarClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base = settings.quasar_base_url.rstrip("/")
        self.auth = YandexAuth(settings.yandex_x_token or "", settings.session_cache_path)
        self._scenario_cache: dict[str, str] = {}
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
            f"{method.upper()} {url} → {r.status_code}: {r.text[:300]}",
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

    # --- TTS / голосовые команды через сценарий ---
    async def _find_scenario_id(self, device_id: str) -> str | None:
        if device_id in self._scenario_cache:
            return self._scenario_cache[device_id]
        trigger = encode(device_id)
        for s in await self.scenarios():
            try:
                if s["triggers"][0]["value"] == trigger:
                    self._scenario_cache[device_id] = s["id"]
                    return s["id"]
            except (KeyError, IndexError, TypeError):
                continue
        return None

    @staticmethod
    def _first_step_text(info: dict) -> Any:
        """Текст/значение первого шага сценария (для верификации, что спека применилась)."""
        try:
            cap = info["steps"][0]["parameters"]["items"][0]["value"]["capabilities"][0]
            val = cap["state"]["value"]
            return val.get("text") if isinstance(val, dict) else val
        except (KeyError, IndexError, TypeError):
            return None

    async def _ensure_scenario(self, device_id: str, spec: dict, expected_first: Any) -> str:
        """Создаёт (с реальным содержимым) или обновляет сценарий колонки и ГАРАНТИРУЕТ,
        что нужная спека реально сохранилась: ретраи с бэкоффом на транзиентные ошибки API
        (status=error / rate-limit) + проверка содержимого перед запуском.
        """
        sid = await self._find_scenario_id(device_id)
        last_err: Exception | None = None
        for attempt in range(6):
            try:
                if sid is None:
                    sid = await self.create_scenario(spec)
                    self._scenario_cache[device_id] = sid
                else:
                    await self.update_scenario(sid, spec)
                info = await self.scenario_edit_info(sid)
                if self._first_step_text(info) == expected_first:
                    return sid
                last_err = RuntimeError("содержимое сценария не совпало после сохранения")
            except Exception as exc:  # noqa: BLE001 — транзиентные ошибки API
                last_err = exc
            await asyncio.sleep(0.6 * (attempt + 1))
        raise RuntimeError(f"Не удалось применить сценарий озвучки: {last_err}")

    async def say(self, text: str, device_id: str | None = None, is_command: bool = False) -> dict[str, Any]:
        """Колонка произносит text (is_command=False) или выполняет как голосовую команду (True).

        Длинный текст (> tts_chunk_size) автоматически режется на несколько TTS-шагов,
        которые колонка произносит подряд в одном сценарии.
        """
        device_id = device_id or self.settings.station_device_id
        if not device_id:
            raise RuntimeError("Не задан STATION_DEVICE_ID (см. scripts/discover.py)")
        name = f"alice-notify {device_id}"
        trigger = encode(device_id)
        chunks = 1
        if is_command:
            # Команду (голосовую/напоминание) нельзя разбить — валидируем длину.
            if len(text) > MAX_ALICE_TEXT:
                raise ValueError(
                    f"Команда Алисе не длиннее {MAX_ALICE_TEXT} символов "
                    f"(сейчас {len(text)}). Сократите текст."
                )
            spec = scenario_speaker_action(name, trigger, device_id, text)
            expected_first = text
        else:
            # TTS-текст режем на чанки строго ≤ лимита (даже если в конфиге больше).
            size = min(self.settings.tts_chunk_size, MAX_ALICE_TEXT)
            parts = chunk_text(text, size)
            chunks = len(parts)
            spec = (
                scenario_multi_tts(name, trigger, device_id, parts)
                if chunks > 1
                else scenario_speaker_tts(name, trigger, device_id, parts[0] if parts else text)
            )
            expected_first = parts[0] if parts else text
        sid = await self._ensure_scenario(device_id, spec, expected_first)
        result = await self.run_scenario(sid)
        return {"chunks": chunks, **result}

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
