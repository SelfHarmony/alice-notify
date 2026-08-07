"""Авторизация в неофициальном API: x-token → cookie сессии → x-csrf-token.

Флоу из AlexxIT/YandexStation:
    - login_token: x-token → Session_id cookie (mobileproxy.passport + /auth/session/)
    - csrf: со страницы https://yandex.ru/quasar (поле "csrfToken2")
    - проверка cookie: GET https://yandex.ru/quasar?storage=1 → storage.user.uid
Сессия (cookies + csrf) кэшируется в .session.json и авто-обновляется из долгоживущего x-token.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class YandexAuth:
    def __init__(self, x_token: str, session_cache_path: str) -> None:
        self.x_token = x_token
        self.cache = Path(session_cache_path)
        self.client = httpx.AsyncClient(
            follow_redirects=True, timeout=15.0, headers={"User-Agent": UA}
        )
        self.csrf: str | None = None
        self._load_cache()

    # --- кэш сессии ---
    def _load_cache(self) -> None:
        if not self.cache.exists():
            return
        try:
            data = json.loads(self.cache.read_text(encoding="utf-8"))
        except Exception:
            return
        for name, value in (data.get("cookies") or {}).items():
            self.client.cookies.set(name, value, domain=".yandex.ru")
        self.csrf = data.get("csrf")

    def _save_cache(self) -> None:
        cookies = {c.name: c.value for c in self.client.cookies.jar}
        self.cache.write_text(
            json.dumps({"cookies": cookies, "csrf": self.csrf}, ensure_ascii=False),
            encoding="utf-8",
        )

    # --- публичное ---
    async def ensure(self) -> None:
        """Гарантирует валидные cookie (обновляет из x-token при необходимости)."""
        if await self._cookies_ok():
            return
        await self.login_token()
        self._save_cache()

    async def csrf_token(self) -> str:
        if not self.csrf:
            r = await self.client.get("https://yandex.ru/quasar")
            m = re.search(r'"csrfToken2":"(.+?)"', r.text)
            if not m:
                raise RuntimeError("Не удалось получить csrfToken2 со страницы quasar")
            self.csrf = m.group(1)
            self._save_cache()
        return self.csrf

    def invalidate_csrf(self) -> None:
        self.csrf = None

    async def aclose(self) -> None:
        await self.client.aclose()

    # --- внутреннее ---
    async def _cookies_ok(self) -> bool:
        try:
            r = await self.client.get("https://yandex.ru/quasar?storage=1")
            data = r.json()
            return bool(data.get("storage", {}).get("user", {}).get("uid"))
        except Exception:
            return False

    async def login_token(self) -> None:
        """Обновляет cookie сессии из x-token."""
        if not self.x_token:
            raise RuntimeError("YANDEX_X_TOKEN не задан — получите его: python scripts/get_token.py")
        r = await self.client.post(
            "https://mobileproxy.passport.yandex.net/1/bundle/auth/x_token/",
            data={"type": "x-token", "retpath": "https://www.yandex.ru"},
            headers={"Ya-Consumer-Authorization": f"OAuth {self.x_token}"},
        )
        resp = r.json()
        if resp.get("status") != "ok":
            raise RuntimeError(f"Ошибка входа по x-token: {resp}")
        host = resp["passport_host"]
        r = await self.client.get(
            f"{host}/auth/session/", params={"track_id": resp["track_id"]},
            follow_redirects=False,
        )
        location = r.headers.get("Location", "")
        if "/auth/finish" not in location and r.status_code not in (302, 303, 200):
            raise RuntimeError(f"Неожиданный ответ auth/session: {r.status_code} {location}")
        # csrf устаревает вместе с сессией
        self.csrf = None
