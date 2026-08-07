"""Конфигурация проекта. Читает .env через pydantic-settings.

Поля опциональны на уровне типов, чтобы каркас и отдельные пробы можно было запускать,
имея заполненными только нужные секреты. Проверку наличия конкретных полей делают сами
скрипты/сервисы через require().
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Корень проекта (…/alice-notify) — для путей к .env и кэшу сессии.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Неофициальный quasar API ---
    # Главный секрет: долгоживущий x-token (получается scripts/get_token.py по QR).
    yandex_x_token: str | None = None
    quasar_base_url: str = "https://iot.quasar.yandex.ru/m"
    # Кэш id станции после дискавери (scripts/discover.py подскажет значение).
    station_device_id: str | None = None

    # --- Общее ---
    reply_timeout_seconds: int = 20
    # Максимальная длина одного TTS-чанка. Прямая озвучка (phrase_action) допускает ~550;
    # берём с запасом. Длинный текст режется на несколько последовательных фраз.
    tts_chunk_size: int = 500
    # Файл-кэш сессии (Session_id cookie + x-csrf-token), выводимой из x-token.
    session_cache_path: str = str(PROJECT_ROOT / ".session.json")

    # --- FastAPI ---
    api_auth_token: str | None = None


def get_settings() -> Settings:
    return Settings()


def require(settings: Settings, *fields: str) -> None:
    """Падает с понятным сообщением, если нужные поля не заполнены в .env."""
    missing = [f for f in fields if getattr(settings, f, None) in (None, "")]
    if missing:
        raise SystemExit(
            "Не заполнены обязательные поля .env для этого шага: "
            + ", ".join(f.upper() for f in missing)
        )
