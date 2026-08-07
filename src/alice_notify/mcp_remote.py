"""Удалённый MCP-сервер поверх HTTP (streamable-http) с общим паролем (bearer-токен).

Все клиенты должны присылать заголовок:
    Authorization: Bearer <MCP_AUTH_TOKEN>
(или X-MCP-Token: <MCP_AUTH_TOKEN>). Без него — 401.

Переменные окружения:
    MCP_AUTH_TOKEN  — общий пароль (обязателен; иначе сервер откажется стартовать)
    MCP_HOST        — по умолчанию 0.0.0.0
    MCP_PORT        — по умолчанию 8848
    MCP_PATH        — путь эндпоинта, по умолчанию /mcp

Запуск: python -m alice_notify.mcp_remote
"""

from __future__ import annotations

import os

import uvicorn
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .mcp_server import mcp

TOKEN = os.environ.get("MCP_AUTH_TOKEN", "")
HOST = os.environ.get("MCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("MCP_PORT", "8848"))
PATH = os.environ.get("MCP_PATH", "/mcp")


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/healthz":
            return JSONResponse({"status": "ok"})
        auth = request.headers.get("authorization", "")
        provided = (
            auth[7:].strip()
            if auth.lower().startswith("bearer ")
            else request.headers.get("x-mcp-token", "")
        )
        if not TOKEN or provided != TOKEN:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def build_app():
    from mcp.server.transport_security import TransportSecuritySettings

    # Host/Origin-проверка (защита от DNS-rebinding для браузеров) не нужна:
    # клиенты — CLI, а реальный гейт — bearer-токен ниже. Host меняется (IP/роутер).
    mcp.settings.streamable_http_path = PATH
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    )
    app = mcp.streamable_http_app()
    app.add_middleware(AuthMiddleware)
    return app


app = build_app()


def main() -> None:
    if not TOKEN:
        raise SystemExit("MCP_AUTH_TOKEN не задан — задайте общий пароль в окружении.")
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
