FROM python:3.12-slim

WORKDIR /app

# Сначала метаданные для кеша слоёв
COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir ".[mcp,api,geo]"
# Chromium + системные зависимости для гео-поиска с рейтингами (Playwright)
RUN playwright install --with-deps chromium

ENV PYTHONUNBUFFERED=1 \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8848 \
    MCP_PATH=/mcp \
    SESSION_CACHE_PATH=/data/.session.json

EXPOSE 8848

# healthcheck без токена (эндпоинт /healthz открыт)
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"MCP_PORT\",\"8848\")}/healthz').read()" || exit 1

CMD ["python", "-m", "alice_notify.mcp_remote"]
