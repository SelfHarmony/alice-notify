"""Поиск мест с рейтингами через headless-браузер (Playwright).

`/maps/api/search` закрыт анти-ботом (динамическая подпись `s` + капча), сырой HTTP не проходит.
Настоящий браузер проходит: сам считает подпись и держит challenge-куки. Здесь headless
Chromium открывает Карты, вводит запрос в поле и перехватывает JSON-ответ поиска (с рейтингами).

Браузер поднимается лениво и переиспользуется между запросами (один page за раз через lock).
Playwright импортируется внутри функций, чтобы модуль грузился и без него (не-гео пути не ломались).
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


def _parse(it: dict, ulat: float, ulon: float) -> dict:
    rd = it.get("ratingData") or {}
    coords = it.get("coordinates") or [None, None]
    lat, lon = coords[1], coords[0]
    rating = rd.get("ratingValue")
    return {
        "name": it.get("title"),
        "rating": round(rating, 1) if isinstance(rating, (int, float)) else rating,
        "reviews": rd.get("reviewCount"),
        "categories": [x.get("name") for x in (it.get("categories") or [])],
        "address": it.get("fullAddress") or it.get("address"),
        "lat": lat,
        "lon": lon,
        "distance_m": _haversine(ulat, ulon, lat, lon),
        "oid": it.get("id"),
        "hours": it.get("workingTimeText"),
    }


async def search_rated(
    text: str,
    lat: float,
    lon: float,
    limit: int = 10,
    min_rating: float | None = None,
) -> list[dict]:
    """Найти места рядом с рейтингами. Возвращает список бизнесов, отсортированный по близости."""
    async with _lock:
        ctx = await _context()
        page = await ctx.new_page()
        resps: list[Any] = []
        page.on("response", lambda r: resps.append(r))
        best: dict | None = None
        try:
            await page.goto(
                f"https://yandex.ru/maps/?ll={lon},{lat}&z=15",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            await page.wait_for_timeout(2500)
            inp = page.locator("input[type=text]").first
            await inp.click()
            await inp.fill(text)
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

    items = ((best or {}).get("data") or {}).get("items") or []
    out = [_parse(it, lat, lon) for it in items if it.get("type") == "business"]
    if min_rating is not None:
        out = [x for x in out if (x.get("rating") or 0) >= min_rating]
    out.sort(key=lambda x: x.get("distance_m") if x.get("distance_m") is not None else 1e12)
    return out[:limit]


async def aclose() -> None:
    for key in ("browser", "pw"):
        obj = _holder.get(key)
        if obj is not None:
            try:
                await (obj.close() if key == "browser" else obj.stop())
            except Exception:
                pass
    _holder.clear()
