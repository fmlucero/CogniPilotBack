# syntax=docker/dockerfile:1.7

# ──────────────────────────────────────────────────────────────────────────────
# Imagen base: Python 3.12 slim + uv (gestor de deps Rust, mucho más rápido que pip)
# La MISMA imagen sirve para la API (uvicorn) y para los workers (arq).
# El comando de entrada se cambia en docker-compose por servicio.
# ──────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

# Instalar uv
COPY --from=ghcr.io/astral-sh/uv:0.5.4 /uv /usr/local/bin/uv

WORKDIR /app

# ──────────────────────────────────────────────────────────────────────────────
# Stage 1: instalar dependencias en una capa cacheable
# ──────────────────────────────────────────────────────────────────────────────
FROM base AS deps

COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-install-project --no-dev || uv sync --no-install-project --no-dev

# ──────────────────────────────────────────────────────────────────────────────
# Stage 2: copiar la app
# ──────────────────────────────────────────────────────────────────────────────
FROM base AS runtime

# Copiar venv pre-armado
COPY --from=deps /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}"

# Copiar código
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY scripts ./scripts

# Healthcheck básico
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=3).status == 200 else 1)"

EXPOSE 8000

# Comando por defecto: API. El worker arq override en compose.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--no-access-log"]
