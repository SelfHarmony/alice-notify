"""Гео-функции через Яндекс.Карты: поиск мест (подсказки) и ссылки-маршруты.

Поиск использует suggest-geo (автокомплит) — он доступен по сессии без анти-бот-подписи.
Полный поиск `/maps/api/search` (с рейтингами) закрыт капчей/подписью `s` — здесь не используется.
Маршрут — deeplink на Яндекс.Карты, авторизация не нужна.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from .quasar import QuasarClient

SUGGEST_URL = "https://suggest-maps.yandex.ru/suggest-geo"

_ROUTE_MODES = {
    "auto": "auto", "car": "auto", "driving": "auto",
    "public": "mt", "transit": "mt", "mt": "mt",
    "pedestrian": "pd", "walk": "pd", "pd": "pd",
    "bike": "bc", "bicycle": "bc", "bc": "bc",
}


async def find_places(
    client: QuasarClient, text: str, lat: float, lon: float, results: int = 10
) -> list[dict[str, Any]]:
    """Найти места рядом по запросу (подсказки Яндекс.Карт).

    Возвращает список: type, title, subtitle (адрес/категория), tags, distance_m,
    distance_text, oid (id организации, если это конкретное место). Без рейтингов.
    lat/lon — координаты пользователя.
    """
    await client.auth.ensure()
    ll = f"{lon},{lat}"  # Яндекс ждёт lon,lat
    params = {
        "part": text,
        "ll": ll,
        "ull": ll,
        "spn": "0.4,0.2",
        "lang": "ru_RU",
        "client_id": "touch-maps",
        "bases": "geo,biz,transit",
        "add_rubrics_loc": "1",
        "add_chains_loc": "1",
        "fullpath": "1",
        "outformat": "json",
        "v": "9",
    }
    r = await client.auth.client.get(
        SUGGEST_URL + "?" + urllib.parse.urlencode(params),
        headers={"referer": "https://yandex.ru/maps/"},
    )
    r.raise_for_status()
    data = r.json()
    out: list[dict[str, Any]] = []
    for item in data.get("results", [])[:results]:
        entry: dict[str, Any] = {
            "type": item.get("type"),
            "title": (item.get("title") or {}).get("text"),
            "subtitle": (item.get("subtitle") or {}).get("text"),
            "tags": item.get("tags"),
        }
        uri = item.get("uri") or ""
        if "oid=" in uri:
            entry["oid"] = uri.split("oid=")[1].split("&")[0]
        dist = item.get("distance") or {}
        if dist:
            entry["distance_m"] = dist.get("value")
            entry["distance_text"] = dist.get("text")
        out.append(entry)
    return out


def build_route(points: list[Any], mode: str = "auto") -> str:
    """Ссылка-маршрут Яндекс.Карт по точкам (в порядке следования).

    points — список точек: [lat, lon] / "lat,lon" / адрес строкой.
    mode: auto | public | pedestrian | bike. Ссылку открыть на телефоне.
    """
    if not points or len(points) < 2:
        raise ValueError("Нужно минимум 2 точки (откуда и куда)")

    def fmt(p: Any) -> str:
        if isinstance(p, (list, tuple)):
            return f"{p[0]},{p[1]}"
        return str(p)

    rtext = "~".join(fmt(p) for p in points)
    rtype = _ROUTE_MODES.get(mode.lower(), "auto")
    query = urllib.parse.urlencode({"rtext": rtext, "rtype": rtype})
    return "https://yandex.ru/maps/?" + query
