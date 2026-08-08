"""Поиск мест с рейтингами через headless-браузер (Playwright).

`/maps/api/search` закрыт анти-ботом (динамическая подпись `s` + капча), сырой HTTP не проходит.
Настоящий браузер проходит: сам считает подпись и держит challenge-куки. Здесь headless
Chromium открывает Карты, вводит запрос в поле и перехватывает JSON-ответ поиска (с рейтингами).

Возможности:
- центр поиска задаётся координатами (lat/lon) ИЛИ адресом/названием (near — геокодится браузером);
- фильтр по радиусу (radius_m) и по рейтингу (min_rating);
- результат отсортирован по расстоянию, у каждого места есть map_url (ссылка на карточку по oid).

Браузер поднимается лениво и переиспользуется. Playwright импортируется внутри функций.
"""

from __future__ import annotations

import asyncio
import math
from typing import Any

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_lock = asyncio.Lock()
_holder: dict[str, Any] = {}


async def _context() -> Any:
    if _holder.get("ctx"):
        return _holder["ctx"]
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
    )
    ctx = await browser.new_context(
        locale="ru-RU", user_agent=UA, viewport={"width": 1360, "height": 900}
    )
    _holder.update(pw=pw, browser=browser, ctx=ctx)
    return ctx


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> int | None:
    if None in (lat1, lon1, lat2, lon2):
        return None
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return int(2 * r * math.asin(math.sqrt(a)))


def _features(it: dict) -> dict:
    out: dict[str, Any] = {}
    for f in it.get("features") or []:
        name, val, typ = f.get("name"), f.get("value"), f.get("type")
        if typ == "bool":
            out[name] = bool(val)
        elif typ == "enum":
            out[name] = [x.get("name") for x in (val or [])]
        elif val is not None:
            out[name] = val
    return out


def _parse(it: dict, clat: float, clon: float) -> dict:
    rd = it.get("ratingData") or {}
    coords = it.get("coordinates") or [None, None]
    lat, lon = coords[1], coords[0]
    rating = rd.get("ratingValue")
    oid = it.get("id")
    cws = it.get("currentWorkingStatus") or {}
    urls = it.get("urls") or []
    aspects = [
        {"text": a.get("text"), "count": a.get("count"),
         "positive": a.get("positive"), "negative": a.get("negative")}
        for a in (it.get("aspects") or [])
    ][:12]
    return {
        "name": it.get("title"),
        "rating": round(rating, 1) if isinstance(rating, (int, float)) else rating,
        "reviews": rd.get("reviewCount"),
        "open_now": cws.get("isOpenNow"),
        "open_text": cws.get("text"),
        "hours": it.get("workingTimeText"),
        "categories": [x.get("name") for x in (it.get("categories") or [])],
        "address": it.get("fullAddress") or it.get("address"),
        "lat": lat,
        "lon": lon,
        "distance_m": _haversine(clat, clon, lat, lon),
        "phone": ((it.get("phones") or [{}])[0]).get("number"),
        "website": urls[0] if urls else None,
        "features": _features(it),
        "review_aspects": aspects,
        "oid": oid,
        "map_url": f"https://yandex.ru/maps/org/{oid}" if oid else None,
    }


async def _typed_search(ctx: Any, query: str, ll: str | None = None) -> list[dict]:
    """Ввести query в поле поиска Карт (центр карты — ll="lon,lat") и вернуть items ответа."""
    page = await ctx.new_page()
    resps: list[Any] = []
    page.on("response", lambda r: resps.append(r))
    best: dict | None = None
    try:
        url = "https://yandex.ru/maps/" + (f"?ll={ll}&z=15" if ll else "")
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2500)
        inp = page.locator("input[type=text]").first
        await inp.click()
        await inp.fill(query)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(6000)
        best_n = -1
        for r in resps:
            if "/maps/api/search" in r.url and r.status == 200:
                try:
                    j = await r.json()
                except Exception:
                    continue
                n = len((j.get("data") or {}).get("items") or [])
                if n > best_n:
                    best_n, best = n, j
    finally:
        await page.close()
    return ((best or {}).get("data") or {}).get("items") or []


async def geocode(near: str) -> dict:
    """Адрес/название → координаты (через браузерный поиск). {lat, lon, name, address}."""
    async with _lock:
        ctx = await _context()
        items = await _typed_search(ctx, near)
    g = next((i for i in items if i.get("coordinates")), None)
    if not g:
        raise ValueError(f"Не удалось определить координаты для «{near}»")
    c = g["coordinates"]
    return {"lat": c[1], "lon": c[0], "name": g.get("title"), "address": g.get("fullAddress") or g.get("address")}


async def search_rated(
    text: str,
    lat: float | None = None,
    lon: float | None = None,
    near: str | None = None,
    radius_m: float | None = None,
    min_rating: float | None = None,
    open_now: bool | None = None,
    limit: int = 10,
) -> dict:
    """Найти места С РЕЙТИНГАМИ рядом с центром. Центр — (lat,lon) или near (адрес/название).

    radius_m — оставить только в этом радиусе; min_rating — фильтр по рейтингу;
    open_now=True — только открытые сейчас. Возвращает {"center": {lat,lon}, "results": [...]},
    results отсортированы по расстоянию. У каждого места: рейтинг, отзывы, открыто ли сейчас,
    часы, категории, адрес, дистанция, телефон, сайт, features (Wi-Fi/кухня/…),
    review_aspects (тональность отзывов), oid, map_url.
    """
    async with _lock:
        ctx = await _context()
        if lat is None or lon is None:
            if not near:
                raise ValueError("Задайте lat/lon или near (адрес/название)")
            gitems = await _typed_search(ctx, near)
            g = next((i for i in gitems if i.get("coordinates")), None)
            if not g:
                raise ValueError(f"Не удалось определить координаты для «{near}»")
            clat, clon = g["coordinates"][1], g["coordinates"][0]
        else:
            clat, clon = lat, lon
        items = await _typed_search(ctx, text, ll=f"{clon},{clat}")

    out = [_parse(it, clat, clon) for it in items if it.get("type") == "business"]
    if min_rating is not None:
        out = [x for x in out if (x.get("rating") or 0) >= min_rating]
    if radius_m is not None:
        out = [x for x in out if x.get("distance_m") is not None and x["distance_m"] <= radius_m]
    if open_now:
        out = [x for x in out if x.get("open_now")]
    out.sort(key=lambda x: x.get("distance_m") if x.get("distance_m") is not None else 1e12)
    return {"center": {"lat": clat, "lon": clon}, "results": out[:limit]}


async def aclose() -> None:
    for key in ("browser", "pw"):
        obj = _holder.get(key)
        if obj is not None:
            try:
                await (obj.close() if key == "browser" else obj.stop())
            except Exception:
                pass
    _holder.clear()
