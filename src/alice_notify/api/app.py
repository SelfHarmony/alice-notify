"""FastAPI поверх QuasarClient. Bearer-авторизация через API_AUTH_TOKEN.

Запуск:
    pip install -e ".[api]"
    uvicorn alice_notify.api.app:app --port 8000
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from .. import geo_browser, maps
from .. import reminders as rem
from ..config import get_settings
from ..service import flatten_devices, get_client

app = FastAPI(title="alice-notify", version="0.1.0")


async def auth(authorization: str = Header(default="")) -> None:
    token = get_settings().api_auth_token
    if not token:
        raise HTTPException(500, "API_AUTH_TOKEN не настроен")
    if authorization != f"Bearer {token}":
        raise HTTPException(401, "Неверный токен")


class SayIn(BaseModel):
    text: str
    device_id: str | None = None


class DateRemindersIn(BaseModel):
    text: str
    times: list[str]
    dates: list[str]
    device_id: str | None = None


class RecurringReminderIn(BaseModel):
    text: str
    time: str
    days_of_week: list[str]
    device_id: str | None = None


class DeviceActionIn(BaseModel):
    capability_type: str
    instance: str
    value: Any
    item_type: str = "device"


class GeoFindIn(BaseModel):
    text: str
    lat: float | None = None
    lon: float | None = None
    near: str | None = None  # адрес/название — центр поиска (геокодится)
    radius_m: float | None = None
    min_rating: float | None = None
    open_now: bool | None = None


class GeoRouteIn(BaseModel):
    points: list[Any]  # [[lat,lon], ...] или адреса; минимум 2
    mode: str = "auto"


@app.post("/geo/find_rated", dependencies=[Depends(auth)])
async def geo_find_rated(body: GeoFindIn) -> dict[str, Any]:
    """Поиск мест с рейтингами/атрибутами рядом (браузер, обход анти-бота). Центр — lat/lon или near."""
    return await geo_browser.search_rated(
        body.text, lat=body.lat, lon=body.lon, near=body.near,
        radius_m=body.radius_m, min_rating=body.min_rating, open_now=body.open_now,
    )


@app.post("/geo/find", dependencies=[Depends(auth)])
async def geo_find(body: GeoFindIn) -> list[dict[str, Any]]:
    """Быстрые подсказки мест (без браузера, без рейтингов). Требует lat/lon."""
    return await maps.find_places(get_client(), body.text, body.lat or 0.0, body.lon or 0.0)


@app.post("/geo/route", dependencies=[Depends(auth)])
async def geo_route(body: GeoRouteIn) -> dict[str, str]:
    """Ссылка-маршрут Яндекс.Карт по точкам (координаты). mode: auto|public|pedestrian|bike."""
    return {"url": maps.build_route(body.points, body.mode)}


@app.get("/geo/place_url/{oid}", dependencies=[Depends(auth)])
async def geo_place_url(oid: str) -> dict[str, str]:
    """Ссылка на карточку места по oid."""
    return {"url": maps.place_url(oid)}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/say", dependencies=[Depends(auth)])
async def say(body: SayIn) -> dict[str, Any]:
    return await get_client().say(body.text, device_id=body.device_id)


@app.post("/command", dependencies=[Depends(auth)])
async def command(body: SayIn) -> dict[str, Any]:
    return await get_client().say(body.text, device_id=body.device_id, is_command=True)


@app.post("/reminders/dates", dependencies=[Depends(auth)])
async def reminders_dates(body: DateRemindersIn) -> dict[str, Any]:
    return await rem.set_specific_date_reminders(
        get_client(), body.text, body.times, body.dates, body.device_id
    )


@app.post("/reminders/recurring", dependencies=[Depends(auth)])
async def reminders_recurring(body: RecurringReminderIn) -> dict[str, Any]:
    return await rem.set_recurring_reminder(
        get_client(), body.text, body.time, body.days_of_week, body.device_id
    )


@app.get("/devices", dependencies=[Depends(auth)])
async def devices() -> list[dict[str, Any]]:
    return flatten_devices(await get_client().devices())


@app.post("/devices/{device_id}/action", dependencies=[Depends(auth)])
async def device_action(device_id: str, body: DeviceActionIn) -> dict[str, Any]:
    return await get_client().device_action(
        device_id, body.capability_type, body.instance, body.value, body.item_type
    )


@app.get("/scenarios", dependencies=[Depends(auth)])
async def scenarios() -> list[dict[str, Any]]:
    return [{"id": s.get("id"), "name": s.get("name")} for s in await get_client().scenarios()]


@app.get("/scenarios/{scenario_id}", dependencies=[Depends(auth)])
async def get_scenario(scenario_id: str) -> dict[str, Any]:
    return await get_client().scenario_edit_info(scenario_id)


@app.post("/scenarios", dependencies=[Depends(auth)])
async def create_scenario(spec: dict[str, Any]) -> dict[str, Any]:
    return {"scenario_id": await get_client().create_scenario(spec)}


@app.put("/scenarios/{scenario_id}", dependencies=[Depends(auth)])
async def update_scenario(scenario_id: str, spec: dict[str, Any]) -> dict[str, Any]:
    return await get_client().update_scenario(scenario_id, spec)


@app.delete("/scenarios/{scenario_id}", dependencies=[Depends(auth)])
async def delete_scenario(scenario_id: str) -> dict[str, Any]:
    return await get_client().delete_scenario(scenario_id)


@app.post("/scenarios/{scenario_id}/run", dependencies=[Depends(auth)])
async def run_scenario(scenario_id: str) -> dict[str, Any]:
    return await get_client().run_scenario(scenario_id)
