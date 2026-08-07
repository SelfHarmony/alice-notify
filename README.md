# alice-notify

Мост для управления умным домом Яндекса из кода и из LLM. Позволяет **программно**:
озвучивать произвольный текст на колонках с Алисой (TTS), ставить напоминания пачками,
управлять устройствами и полностью работать со сценариями (создавать / читать / изменять /
удалять / запускать). Доступно тремя способами: **MCP-сервер** (для нейросетей/Claude),
**REST API** (FastAPI) и **CLI-скрипты**.

> ⚠️ Проект использует **неофициальный** внутренний API Яндекса (`iot.quasar.yandex.ru`) — тот же,
> что и приложение «Дом с Алисой». Это серая зона относительно ToS Яндекса, интерфейс может
> измениться без предупреждения. Проект не аффилирован с Яндексом. Используйте на свой риск,
> только со своим аккаунтом.

## Возможности

- **TTS** — Алиса произносит любой переданный текст на выбранной колонке. Длинный текст
  автоматически режется на части (лимит Яндекса — 100 символов на фразу) и читается подряд.
- **Напоминания пачками (гибрид):**
  - на конкретные даты → нативные напоминания Алисы (голосовая команда, привязка к дате);
  - повторяющиеся по дням недели → беззвучный сценарий с TTS по расписанию.
- **Устройства** — список и управление умениями (свет, розетки, климат и т.д.).
- **Сценарии** — полный CRUD: список, чтение, создание, изменение, удаление, запуск.
- **Три интерфейса** поверх одного ядра: MCP (stdio и удалённый HTTP с авторизацией),
  FastAPI, CLI.

## Как это устроено

- **Бэкенд** — неофициальный API `https://iot.quasar.yandex.ru/m`. Авторизация по **x-token**
  (долгоживущий токен Яндекса), из него выводятся cookie-сессии и `x-csrf-token`; сессия
  кэшируется и автоматически обновляется.
- **TTS реализован через сценарий**: на колонку заводится сценарий со скрытым голосовым
  триггером, в него подставляется текст, затем сценарий запускается (прямого «скажи текст»
  в API нет).
- Ядро — `QuasarClient` (`src/alice_notify/quasar/`), над ним тонкие обёртки: `mcp_server.py`
  (stdio), `mcp_remote.py` (HTTP + bearer-auth), `api/app.py` (FastAPI).

## Требования

- Python 3.11+
- Аккаунт Яндекса с привязанными к «Дому с Алисой» устройствами (колонка с Алисой).
- Для удалённого MCP: Docker + (желательно) реверс-прокси с HTTPS.

## Быстрый старт (локально)

```bash
python -m venv .venv
# Windows: .venv\Scripts\Activate.ps1   |   Linux/macOS: source .venv/bin/activate
pip install -e ".[all]"

cp .env.example .env     # заполнить по ходу (см. ниже)
```

### 1. Авторизация (x-token по QR, один раз)

```bash
python scripts/get_token.py
```

Скрипт покажет QR-код — отсканируйте приложением **Яндекс** и подтвердите вход. Пароль нигде
не вводится. Полученный x-token скрипт запишет в `.env` (`YANDEX_X_TOKEN`). Сессия кэшируется в
`.session.json` и обновляется автоматически.

### 2. Найти устройства

```bash
python scripts/discover.py
```

Выведет устройства, сценарии и подскажет `STATION_DEVICE_ID` (колонка по умолчанию) — впишите
его в `.env`.

### 3. Проверить озвучку

```bash
python scripts/say_probe.py "Привет из alice-notify"
```

## MCP-сервер

Локально (stdio, для Claude Code):

```bash
claude mcp add alice-notify -- python -m alice_notify.mcp_server
```

Удалённо (HTTP + пароль), см. раздел «Docker-деплой».

**Инструменты:** `alice_say`, `alice_command`, `alice_set_date_reminders`,
`alice_set_recurring_reminder`, `alice_list_devices`, `alice_device_action`,
`alice_list_scenarios`, `alice_get_scenario`, `alice_create_scenario`,
`alice_update_scenario`, `alice_delete_scenario`, `alice_run_scenario`.

## REST API (FastAPI)

```bash
uvicorn alice_notify.api.app:app --port 8000
curl -X POST localhost:8000/say \
  -H "Authorization: Bearer $API_AUTH_TOKEN" -H "Content-Type: application/json" \
  -d '{"text":"проверка"}'
```

Эндпоинты: `POST /say`, `POST /command`, `POST /reminders/dates`, `POST /reminders/recurring`,
`GET /devices`, `POST /devices/{id}/action`, `GET|POST /scenarios`,
`GET|PUT|DELETE /scenarios/{id}`, `POST /scenarios/{id}/run`, `GET /health`.

## Docker-деплой (удалённый MCP с авторизацией)

`mcp_remote.py` поднимает MCP по HTTP (streamable-http) и требует общий пароль: каждый клиент
шлёт заголовок `Authorization: Bearer <MCP_AUTH_TOKEN>`, без него — `401`.

```bash
# 1. Заполнить .env: YANDEX_X_TOKEN, STATION_DEVICE_ID, MCP_AUTH_TOKEN (длинная случайная строка)
# 2. Собрать и запустить
docker compose up -d --build
```

Сервис слушает порт `8848` (том `./data` хранит кэш сессии). Подключение клиента:

```bash
claude mcp add --transport http alice --header "Authorization: Bearer <MCP_AUTH_TOKEN>" \
  https://<your-host>/mcp
```

**HTTPS обязателен при выставлении наружу** — иначе пароль идёт открытым текстом. Поставьте
перед контейнером реверс-прокси с TLS (Caddy / nginx / Nginx Proxy Manager). Для потоковых
ответов (SSE) на прокси отключите буферизацию, например для nginx:

```nginx
proxy_buffering off;
proxy_read_timeout 3600s;
proxy_http_version 1.1;
```

## Переменные окружения (`.env`)

| Переменная | Назначение |
|---|---|
| `YANDEX_X_TOKEN` | x-token Яндекса (главный секрет). Получить `scripts/get_token.py`. |
| `STATION_DEVICE_ID` | id колонки по умолчанию (из `scripts/discover.py`). |
| `QUASAR_BASE_URL` | База API, обычно менять не нужно. |
| `API_AUTH_TOKEN` | Bearer для FastAPI. |
| `MCP_AUTH_TOKEN` | Общий пароль для удалённого MCP (`mcp_remote`). |
| `MCP_HOST` / `MCP_PORT` / `MCP_PATH` | Параметры HTTP-сервера MCP. |
| `TTS_CHUNK_SIZE` | Макс. длина TTS-чанка (≤100). |

## Ограничения и безопасность

- **Лимит 100 символов** на команду/фразу Алисе. `alice_say` сам разбивает длинный текст;
  `alice_command` и напоминания валидируют длину и возвращают понятную ошибку.
- Пакетные напоминания на даты Алиса **вслух** подтверждает на каждое.
- Секреты (`.env`, `.session.json`) — в `.gitignore`, **не коммитить**. При утечке x-token
  выйдите из устройств в настройках Яндекс ID (это отзовёт токен).
- Неофициальный API может измениться; форматы ответов не гарантированы.

## Структура

```
src/alice_notify/
  config.py            # настройки (.env, pydantic-settings)
  quasar/auth.py       # x-token → cookie → csrf, кэш сессии
  quasar/client.py     # QuasarClient: устройства, TTS/чанкинг, сценарии, будильники
  reminders.py         # напоминания пачками (гибрид)
  service.py           # общий клиент + представление устройств
  mcp_server.py        # MCP через stdio
  mcp_remote.py        # MCP через HTTP + bearer-auth (для Docker)
  api/app.py           # FastAPI
scripts/               # get_token, discover, say_probe, alarms_probe, scenario_dump
Dockerfile, docker-compose.yml
```

## Благодарности

Механику неофициального API (авторизация по x-token, TTS через сценарий) удалось разобрать
благодаря открытым проектам сообщества, в частности
[AlexxIT/YandexStation](https://github.com/AlexxIT/YandexStation).

## Лицензия

Добавьте по своему усмотрению (например, MIT). Использование неофициального API — на ваш риск.
