# CogniPilot Back

Backend API de CogniPilot. **FastAPI + SQLAlchemy 2.0 async + Postgres 16 + Redis 7 + arq workers.**

Reemplaza los route handlers de `cognipilot-remote` (Next.js). El front Next.js sigue ocupándose de la UI admin; este proyecto se queda con todo lo que es API/DB/colas/push.

## Stack

| Capa | Tecnología | Por qué |
|---|---|---|
| HTTP | FastAPI 0.115 + Uvicorn | Async-first, OpenAPI auto, Pydantic v2 |
| ORM | SQLAlchemy 2.0 async + asyncpg | Equivalente a Prisma del back viejo, mismo schema |
| Migraciones | Alembic | Ownership del schema |
| DB | Postgres 16 | Misma DB del back viejo (preservación de datos) |
| Pool conexiones | PgBouncer (transaction pooling) | Para escalar a N réplicas del API |
| Cache + queue | Redis 7 | Reglas activas + cola arq |
| Workers | arq (asyncio) | Equivalente a BullMQ para Python async |
| Auth | JWT HS256 (python-jose) | Tokens compatibles con el back Next.js |
| Hashing | bcrypt (rounds 10) | Compatible con hashes existentes en DB |
| FCM | firebase-admin | Push notifications a la app Android |
| Reverse proxy | nginx | Ruteo /api → FastAPI, /admin → Next.js |
| Package mgr | uv | Rust-based, mucho más rápido que pip |

## Estructura

```
cognipilot-back/
├── app/
│   ├── main.py              ← FastAPI app + middlewares
│   ├── core/                ← Config, DB, security, deps
│   ├── models/              ← SQLAlchemy 2.0 (12 entidades)
│   ├── schemas/             ← Pydantic v2 (request/response)
│   ├── routers/             ← Endpoints (auth, empresas, usuarios, ...)
│   ├── services/            ← Lógica de negocio (FCM, reglas)
│   ├── utils/               ← Helpers (CUIT, password gen)
│   └── workers/             ← arq tasks
├── alembic/                 ← Migraciones DB
├── scripts/                 ← Comandos auxiliares (baseline, seed, etc.)
├── nginx/                   ← Config del reverse proxy
├── Dockerfile               ← Multi-stage, una imagen sirve para API y workers
├── docker-compose.yml       ← Stack completo (back + workers + postgres + redis + nginx)
├── pyproject.toml           ← Deps (uv-friendly)
└── alembic.ini
```

## Desarrollo local (Windows con uv)

```powershell
# 1. Instalar uv si no está: https://docs.astral.sh/uv/getting-started/installation/
# 2. Sync deps (crea .venv automáticamente)
uv sync

# 3. Copiar .env.example a .env y completar (mismo JWT_SECRET que cognipilot-remote)
copy .env.example .env

# 4. Levantar dependencias (postgres + redis) — desde la VM o local
# Si la DB ya existe en la VM con datos, apuntar DATABASE_URL a 10.201.0.67:5432

# 5. Correr API en modo dev
uv run uvicorn app.main:app --reload --port 8000

# 6. Correr worker arq
uv run arq app.workers.tasks.WorkerSettings
```

OpenAPI UI: http://localhost:8000/docs

## Deploy en la VM UM-Cloud

```powershell
# Desde el repo en GitHub:
ssh -i F:\Proys\cognipilot-um.pem ubuntu@10.201.0.67 'cd ~/cognipilot-back; git pull; docker compose up -d --build'

# Logs en vivo (todos los servicios):
ssh -i F:\Proys\cognipilot-um.pem ubuntu@10.201.0.67 'cd ~/cognipilot-back; docker compose logs -f'

# Escalar el API a 4 réplicas:
ssh -i F:\Proys\cognipilot-um.pem ubuntu@10.201.0.67 'cd ~/cognipilot-back; docker compose up -d --scale back-api=4'
```

## Migración desde el back viejo (Next.js)

Plan de cutover sin downtime:

1. **Skeleton + auth + CRUDs** ← este commit
2. Alembic baseline → stamp DB existente
3. Levantar back-api en paralelo en la VM (otro puerto), validar con curl
4. Migrar endpoints restantes (schedule, events, devices)
5. Setup arq + FCM async + endpoints calientes
6. nginx adelante
7. Modificar Next.js: drop `app/api/*`, las páginas hacen fetch a FastAPI
8. Cutover Cloudflare Tunnel apunta a nginx

Tokens JWT viejos siguen funcionando (mismo HS256, mismo secret, mismas claims).

## Compatibilidad de auth

Los JWTs emitidos por el back viejo (Next.js) son **bit-compatible** con este back:

- Misma firma HS256
- Mismo `JWT_SECRET` y `JWT_REFRESH_SECRET` en el `.env`
- Mismas claims: `sub`, `email`, `rol`, `empresaId`, `iat`, `exp`
- Mismas cookies httpOnly: `cp_at` (access) y `cp_rt` (refresh)
- Bcrypt rounds 10 — los hashes de password existentes se validan tal cual

Cuando se haga el cutover, los usuarios logueados **no necesitan re-loguearse**.
