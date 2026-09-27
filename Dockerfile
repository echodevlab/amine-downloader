# syntax=docker/dockerfile:1

# ---- 前端构建 ----
FROM oven/bun:1 AS web
WORKDIR /web
COPY web/package.json web/bun.lock ./
RUN bun install --frozen-lockfile
COPY web/ ./
RUN bun run build

# ---- Python 运行时 ----
FROM python:3.14-slim AS runtime
COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    # 配置 / 数据库 / 规则 / CloakBrowser 缓存都放这里（挂载卷）
    AMINE_DOWNLOADER_CONFIG_DIR=/config \
    AMINE_DOWNLOADER_WEB_DIR=/app/web/dist \
    CLOAKBROWSER_CACHE_DIR=/config/.cloakbrowser

WORKDIR /app

# 先装依赖（利用层缓存），再装项目本身
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev
COPY src/ ./src/
COPY README.md ./
RUN uv sync --frozen --no-dev

# 前端产物由内置服务托管
COPY --from=web /web/dist ./web/dist

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8420
VOLUME ["/config"]

# 需要时用 -e AMINE_DOWNLOADER_CONFIG=/config/xxx.toml 指定配置
ENTRYPOINT ["amine-downloader"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8420"]
