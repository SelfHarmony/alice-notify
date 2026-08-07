"""Стадия 1 — получение x-token Яндекса по QR-коду (без ввода пароля).

Запуск (в вашем терминале, чтобы отрисовался QR):
    pip install -e .
    python scripts/get_token.py

Как работает (флоу AlexxIT/YandexStation):
    1. passport.yandex.ru/pwl-yandex → CSRF-токен
    2. auth/password/submit → track_id
    3. auth/magic/code → ссылка для QR
    4. поллинг auth/magic/code/status до подтверждения в приложении Яндекса
    5. sessions/get_session → cookie сессии
    6. token_by_sessionid → x-token
Пароль нигде не вводится — только подтверждение сканированием QR в приложении Яндекс.

Полученный x-token печатается и (по подтверждению) записывается в .env → YANDEX_X_TOKEN.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from alice_notify.config import PROJECT_ROOT  # noqa: E402

# Публичные клиентские константы приложения Яндекса (из AlexxIT/YandexStation).
CLIENT_ID = "c0ebe342af7d48fbbbfcf2d2eedb8f9e"
CLIENT_SECRET = "ad0a908f0aa341a182a37ecd75bc319e"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
POLL_SECONDS = 3
POLL_TIMEOUT = 180  # ~3 минуты на скан


def _print_qr(link: str) -> None:
    print("\nОтсканируйте QR приложением Яндекс (или Яндекс с Алисой):\n")
    try:
        import qrcode

        qr = qrcode.QRCode(border=1)
        qr.add_data(link)
        qr.make()
        qr.print_ascii(invert=True)
    except Exception as exc:  # pragma: no cover - qrcode всегда в deps
        print(f"(не удалось нарисовать QR: {exc})")
    print(f"\nИли откройте ссылку на телефоне с установленным приложением Яндекса:\n{link}\n")


def main() -> int:
    headers_common = {"User-Agent": UA}
    with httpx.Client(follow_redirects=True, timeout=15.0, headers=headers_common) as c:
        # 1. CSRF
        r = c.get("https://passport.yandex.ru/pwl-yandex")
        r.raise_for_status()
        m = re.search(r'__CSRF__ = "([^"]+)', r.text)
        if not m:
            print("Не нашёл CSRF-токен на странице passport. Первые 500 символов ответа:")
            print(r.text[:500])
            return 1
        auth_headers = {"X-CSRF-Token": m[1]}

        # 2. track_id
        r = c.post(
            "https://passport.yandex.ru/pwl-yandex/api/passport/auth/password/submit",
            json={"retpath": "https://passport.yandex.ru/"},
            headers=auth_headers,
        )
        r.raise_for_status()
        auth_json = r.json()
        if "track_id" not in auth_json:
            print(f"Неожиданный ответ password/submit: {auth_json}")
            return 1

        # 3. QR-ссылка
        r = c.post(
            "https://passport.yandex.ru/pwl-yandex/api/passport/auth/magic/code",
            data={"location_id": "0", "magic_track_id": auth_json["track_id"], "track_id": ""},
            headers=auth_headers,
        )
        r.raise_for_status()
        link = r.json().get("link")
        if not link:
            print(f"Не получил ссылку для QR: {r.json()}")
            return 1
        _print_qr(link)

        # 4. Поллинг подтверждения
        print("Жду подтверждения в приложении Яндекса...")
        deadline = time.time() + POLL_TIMEOUT
        status = {}
        while time.time() < deadline:
            r = c.post(
                "https://passport.yandex.ru/pwl-yandex/api/passport/auth/magic/code/status",
                json=auth_json,
                headers=auth_headers,
            )
            r.raise_for_status()
            status = r.json()
            if status.get("state") == "otp_auth_finished":
                break
            time.sleep(POLL_SECONDS)
        else:
            print("Тайм-аут: подтверждение не получено. Запустите скрипт заново.")
            return 1

        # 5. Сессия
        r = c.post(
            "https://passport.yandex.ru/pwl-yandex/api/passport/sessions/get_session",
            data={"track_id": status["trackId"]},
            headers=auth_headers,
        )
        r.raise_for_status()

        # 6. cookie → x-token
        cookies = "; ".join(
            f"{ck.name}={ck.value}"
            for ck in c.cookies.jar
            if (ck.domain or "").endswith("yandex.ru")
        )
        r = c.post(
            "https://mobileproxy.passport.yandex.net/1/bundle/oauth/token_by_sessionid",
            data={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
            headers={"Ya-Client-Host": "passport.yandex.ru", "Ya-Client-Cookie": cookies},
        )
        r.raise_for_status()
        resp = r.json()
        x_token = resp.get("access_token")
        if not x_token:
            print(f"Не удалось получить x-token: {resp}")
            return 1

        # Валидация + имя аккаунта
        info = c.get(
            "https://mobileproxy.passport.yandex.net/1/bundle/account/short_info/?avatar_size=islands-300",
            headers={"Authorization": f"OAuth {x_token}"},
        ).json()
        login = info.get("display_login") or info.get("display_name") or "?"

    print("\n" + "=" * 70)
    print(f"Успех! Аккаунт: {login}")
    print("x-token:")
    print(x_token)
    print("=" * 70)

    _maybe_save_env(x_token)
    return 0


def _maybe_save_env(x_token: str) -> None:
    env_path = PROJECT_ROOT / ".env"
    ans = input("\nЗаписать x-token в .env (YANDEX_X_TOKEN)? [y/N]: ").strip().lower()
    if ans not in ("y", "yes", "д", "да"):
        print("Ок, впишите вручную: YANDEX_X_TOKEN=<токен> в .env")
        return
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    replaced = False
    for i, line in enumerate(lines):
        if line.strip().startswith("YANDEX_X_TOKEN="):
            lines[i] = f"YANDEX_X_TOKEN={x_token}"
            replaced = True
            break
    if not replaced:
        lines.append(f"YANDEX_X_TOKEN={x_token}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Записано в {env_path}")


if __name__ == "__main__":
    raise SystemExit(main())
