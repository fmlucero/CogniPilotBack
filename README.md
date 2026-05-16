# CogniPilot Back

Backend API de CogniPilot. **FastAPI + SQLAlchemy 2.0 async + Postgres 16 + Redis 7 + arq workers.**

Reemplaza los route handlers de `cognipilot-remote` (Next.js). El front Next.js sigue ocupándose de la UI admin; este proyecto se queda con todo lo que es API/DB/colas/push.

## Stack

| Capa | Tecnología | Por qué |
|---|---|---|
| HTTP | FastAPI 0.115 + Uvicorn | Async-first, OpenAPI auto, Pydantic v2 |
| Validación | Pydantic v2 | Schemas request/response, field validators |
| ORM | SQLAlchemy 2.0 async + asyncpg | Equivalente a Prisma del back viejo, mismo schema |
| Migraciones | Alembic | Ownership del schema (+ baseline contra DB existente) |
| DB | Postgres 16 | Misma DB del back viejo (preservación de datos) |
| Pool conexiones | PgBouncer (transaction pooling) | Para escalar a N réplicas del API |
| Cache + queue | Redis 7 | Caché de reglas + backend de arq |
| Workers async | arq (asyncio) | Equivalente a BullMQ para Python async |
| Auth | JWT HS256 (python-jose) | Tokens compatibles con el back Next.js |
| Hashing | bcrypt (rounds 10) | Compatible con hashes existentes en DB |
| FCM | firebase-admin | Push notifications a la app Android (notification+data) |
| Instrumentación | prometheus-fastapi-instrumentator + prometheus-client | Métricas HTTP auto + counters/gauges/histograms de negocio |
| Métricas store | Prometheus 3 | Scrapea `/metrics` cada 15s, retención 15d |
| Dashboards OPS | Grafana 11 | Dashboard auto-provisioned con 8 paneles (drill-down técnico) |
| Dashboards admin | Endpoints JSON `/api/metrics/*` role-protected | Embebidos en el panel web (Next.js), no requieren abrir Grafana |
| Reverse proxy | nginx | Ruteo `/api` → FastAPI, `/admin` → Next.js (pendiente Fase B) |
| Package mgr | uv | Rust-based, mucho más rápido que pip |

## Estructura

```
cognipilot-back/
├── app/
│   ├── main.py                     ← FastAPI app + middlewares + instrumentator
│   ├── core/
│   │   ├── config.py               ← Settings (pydantic-settings, lee .env)
│   │   ├── db.py                   ← Engine async + sessionmaker + Base
│   │   ├── deps.py                 ← FastAPI Depends (DB, CurrentUser, require_roles)
│   │   ├── security.py             ← JWT sign/verify + bcrypt
│   │   └── observability.py        ← Prometheus instrumentator + métricas custom
│   ├── models/                     ← SQLAlchemy 2.0 (12 entidades + 4 enums)
│   │   ├── empresa.py, usuario.py, operacion.py, regla.py, eventos.py, enums.py
│   ├── schemas/                    ← Pydantic v2 (request/response)
│   │   ├── auth.py, empresa.py, usuario.py, dispositivo.py,
│   │   ├── evento.py, schedule.py, posicion.py, metrics.py
│   ├── routers/                    ← Endpoints HTTP
│   │   ├── health.py               ← /health, /health/db
│   │   ├── auth.py                 ← /api/auth/{login,logout,me,refresh}
│   │   ├── empresas.py             ← /api/empresas[/{id}]
│   │   ├── usuarios.py             ← /api/usuarios[/{id}]
│   │   ├── schedule.py             ← /api/schedule (con FCM push)
│   │   ├── events.py               ← /api/events (GET + POST)
│   │   ├── devices.py              ← /api/devices/register
│   │   └── metrics.py              ← /api/metrics/{overview,timeseries} (admin)
│   ├── services/
│   │   ├── fcm.py                  ← Firebase Admin SDK (notification+data)
│   │   └── prometheus_client.py    ← Cliente HTTP de Prometheus para timeseries
│   ├── utils/
│   │   ├── cuit.py                 ← Validación liviana CUIT
│   │   └── password.py             ← Generador temp 12 chars
│   └── workers/
│       └── tasks.py                ← arq WorkerSettings + tasks (FCM, bulk, position)
├── alembic/                        ← Migraciones DB
│   ├── env.py, script.py.mako, versions/
├── monitoring/                     ← Stack OPS (profile "monitoring")
│   ├── prometheus.yml              ← Config de scraping
│   └── grafana/
│       ├── provisioning/           ← Datasource + dashboards auto-load
│       └── dashboards/cognipilot-overview.json
├── scripts/
│   ├── baseline_alembic.md         ← Cómo tomar ownership de la DB sin downtime
│   └── seed.py                     ← Port de prisma/seed.ts (mismo dataset)
├── nginx/                          ← Config del reverse proxy (pendiente Fase B)
├── Dockerfile                      ← Multi-stage, una imagen sirve para API y workers
├── docker-compose.yml              ← Stack completo + profiles (monitoring, dev)
├── pyproject.toml                  ← Deps (uv-friendly)
└── alembic.ini
```

## Desarrollo local (Windows con uv)

```powershell
# 1. Instalar uv si no está: https://docs.astral.sh/uv/getting-started/installation/

# 2. Sync deps (crea .venv automáticamente y bloquea con uv.lock)
uv sync

# 3. Copiar .env.example a .env y completar
#    Importante: usar el MISMO JWT_SECRET / JWT_REFRESH_SECRET que cognipilot-remote
#    para que los tokens sigan validándose durante el cutover.
copy .env.example .env

# 4. Para dev local, podés apuntar DATABASE_URL a la VM por ZeroTier:
#    DATABASE_URL=postgresql+asyncpg://cognipilot:<pw>@10.201.0.67:5432/cognipilot
#    DATABASE_URL_SYNC=postgresql://cognipilot:<pw>@10.201.0.67:5432/cognipilot

# 5. Correr API en modo dev (auto-reload)
uv run uvicorn app.main:app --reload --port 8000

# 6. Correr worker arq (en otra terminal)
uv run arq app.workers.tasks.WorkerSettings

# 7. Correr el seed (solo primera vez en una DB fresca)
uv run python -m scripts.seed
```

| URL | Para qué |
|---|---|
| `http://localhost:8000/docs` | OpenAPI / Swagger UI |
| `http://localhost:8000/redoc` | ReDoc |
| `http://localhost:8000/health` | Liveness check |
| `http://localhost:8000/health/db` | Readiness check (SELECT 1) |
| `http://localhost:8000/metrics` | Prometheus exposition format |
| `http://localhost:8000/api/metrics/overview` | JSON métricas (requiere auth admin) |

## Endpoints calientes (alto throughput)

| Endpoint | Modo | Beneficio |
|---|---|---|
| `POST /api/schedule` | Encola FCM en arq, responde inmediato | Latencia <20ms (vs ~200ms del sync) |
| `POST /api/events/bulk` | Bulk insert en una transacción, max 500 eventos | Una sola conexión DB, en vez de 500 INSERTs |
| `POST /api/positions` | Inline con haversine: si difiere <10m de la última, NO inserta fila (solo actualiza `lastLat/lastLng/lastSeen`) | Controla crecimiento de la tabla con repartidor parado |

Si Redis se cae, `POST /api/schedule` hace fallback a sync FCM (no se pierde el push).

## Background jobs (in-process)

El proceso del API corre un loop async que cada 30s actualiza los gauges:

- `cognipilot_active_devices{window="5m"}` y `{window="24h"}` — count desde Postgres
- `cognipilot_arq_queue_depth{queue="arq:queue"}` — `ZCARD` sobre Redis

Esto hace que Grafana y el dashboard del admin tengan series temporales actualizadas sin necesidad de un cron externo.

## Deploy en la VM UM-Cloud

```powershell
# Desde el repo en GitHub:
ssh -i F:\Proys\cognipilot-um.pem ubuntu@10.201.0.67 'cd ~/cognipilot-back; git pull; docker compose up -d --build'

# Logs en vivo (todos los servicios):
ssh -i F:\Proys\cognipilot-um.pem ubuntu@10.201.0.67 'cd ~/cognipilot-back; docker compose logs -f'

# Escalar el API a 4 réplicas:
ssh -i F:\Proys\cognipilot-um.pem ubuntu@10.201.0.67 'cd ~/cognipilot-back; docker compose up -d --scale back-api=4'
```

## Migración desde el back viejo (Next.js) — plan de cutover sin downtime

| # | Paso | Estado |
|---|---|---|
| 1 | Skeleton + auth + CRUDs (empresas, usuarios) | ✅ |
| 2 | Endpoints schedule + events + devices/register | ✅ |
| 3 | Observabilidad (Prometheus + endpoints `/api/metrics`) | ✅ |
| 4 | Seed Python equivalente al de Prisma | ✅ |
| 5 | arq async para FCM push (`POST /api/schedule` ahora encola) | ✅ |
| 6 | Endpoints calientes: `POST /api/events/bulk` (bulk insert), `POST /api/positions` (haversine + diff) | ✅ |
| 7 | Loop periódico que refresca gauges (active_devices, queue_depth) | ✅ |
| 8 | nginx config para reverse proxy (`/api` → FastAPI, resto → Next.js) | ✅ |
| 9 | Alembic baseline → stamp DB existente | ⏳ |
| 10 | Levantar `back-api` en paralelo en la VM, validar contra DB real | ⏳ |
| 11 | Modificar Next.js: drop `app/api/*` + `lib/{prisma,jwt,auth,firebase-admin,password,cuit}.ts`, las páginas hacen fetch a FastAPI | ⏳ |
| 12 | Cloudflare Tunnel pasa a apuntar a nginx | ⏳ |
| 13 | Dashboard React en el admin Next.js consumiendo `/api/metrics/*` | ⏳ |

Tokens JWT viejos siguen funcionando (mismo HS256, mismo secret, mismas claims) → cuando hagamos el cutover **nadie se desloguea**.

## Observabilidad

Dos capas, integradas:

### A) Para el equipo de plataforma (Grafana — drill-down técnico)

Profile `monitoring` en el compose. Levantar con:

```powershell
docker compose --profile monitoring up -d prometheus grafana
```

- **Prometheus** (`http://10.201.0.67:9090` interno): scrapea `back-api:8000/metrics` cada 15s. Retención 15 días.
- **Grafana** (`http://10.201.0.67:3001`): auto-provisiona el datasource Prometheus + dashboard "CogniPilot — Overview" con 8 paneles (req/s, p50/p95/p99, errores, eventos por tipo, FCM success/error).
- Login: `admin / ${GRAFANA_ADMIN_PASSWORD}`

### B) Para el admin del producto (dentro del panel web)

El back expone endpoints JSON role-protegidos que el admin UI (Next.js) consume:

```
GET /api/metrics/overview                  # snapshot — cards del dashboard
GET /api/metrics/timeseries?metric=...     # puntos {ts, value} para line charts
```

Solo `admin_sistema` por ahora. Métricas disponibles para timeseries:

| `metric` | Qué muestra |
|---|---|
| `requests_rate` | Req/s del back (1m rate) |
| `error_rate` | % de 5xx sobre total (5m) |
| `latency_p95_ms` | Latencia p95 en ms |
| `events_rate` | Eventos ingresados/s |
| `fcm_success_rate` | % de pushes exitosos |
| `queue_depth` | Jobs encolados en arq |

Parametros: `window=15m|1h|6h|24h|7d`, `step=15..3600` (segundos).

Si Prometheus no está corriendo, los endpoints devuelven `prometheus_available: false` y arrays vacíos (degradación graceful). El `overview` igual devuelve los contadores in-process + counts directos de DB.

### C) Métricas custom de negocio

Definidas en `app/core/observability.py`:

- `cognipilot_events_ingested_total{tipo=...}` — counter por tipo de evento
- `cognipilot_fcm_push_total{result=success|error}` — push enviados
- `cognipilot_fcm_push_duration_seconds` — histogram latencia FCM
- `cognipilot_active_devices{window=5m|24h}` — gauge (actualizado por un job periódico — pendiente)
- `cognipilot_arq_queue_depth{queue=...}` — gauge profundidad de cola
- `cognipilot_arq_jobs_total{status=ok|retry|fail, task=...}` — counter de jobs

## Compatibilidad de auth

Los JWTs emitidos por el back viejo (Next.js) son **bit-compatible** con este back:

- Misma firma HS256
- Mismo `JWT_SECRET` y `JWT_REFRESH_SECRET` en el `.env`
- Mismas claims: `sub`, `email`, `rol`, `empresaId`, `iat`, `exp`
- Mismas cookies httpOnly: `cp_at` (access) y `cp_rt` (refresh)
- Bcrypt rounds 10 — los hashes de password existentes se validan tal cual

Cuando se haga el cutover, los usuarios logueados **no necesitan re-loguearse**.
