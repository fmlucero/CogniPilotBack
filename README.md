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
| Notificaciones | Polling controlado + SSE (HU-18) | Sistema propio que reemplaza a Firebase Cloud Messaging. La app consulta `/api/schedule` por polling (30s foreground / 15min background) y recibe push realtime por `GET /api/realtime/stream` (latencia ~100ms cuando está en foreground). Backend: `app/services/realtime.py` (Redis pub/sub) + `app/routers/realtime.py` (sse-starlette). |
| Instrumentación | prometheus-fastapi-instrumentator + prometheus-client | Métricas HTTP auto + counters/gauges/histograms de negocio |
| Métricas store | Prometheus 3 | Scrapea `/metrics` cada 15s, retención 15d |
| Dashboards OPS | Grafana 11 | Dashboard auto-provisioned con 8 paneles (drill-down técnico) |
| Dashboards admin | Endpoints JSON `/api/metrics/*` role-protected | Embebidos en el panel web (Next.js), no requieren abrir Grafana |
| Reverse proxy | nginx | Ruteo `/api` → FastAPI, `/` → Next.js. Activo en :80 (profile `with-nginx`). |
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
│   │   ├── evento.py, schedule.py, posicion.py, metrics.py, me.py
│   ├── routers/                    ← Endpoints HTTP
│   │   ├── health.py               ← /health, /health/db
│   │   ├── auth.py                 ← /api/auth/{login,logout,me,refresh}
│   │   ├── empresas.py             ← /api/empresas[/{id}]
│   │   ├── usuarios.py             ← /api/usuarios[/{id}]
│   │   ├── schedule.py             ← /api/schedule (sin FCM tras HU-18)
│   │   ├── events.py               ← /api/events (GET + POST, auth obligatoria desde HU-03)
│   │   ├── devices.py              ← /api/devices/register
│   │   ├── positions.py            ← /api/positions (haversine + diff)
│   │   ├── me.py                   ← /api/me/{ruta,reglas} (HU-03, solo rol=repartidor)
│   │   ├── realtime.py             ← /api/realtime/stream (SSE, HU-18 fase 4)
│   │   └── metrics.py              ← /api/metrics/{overview,timeseries} (admin)
│   ├── services/
│   │   ├── prometheus_client.py    ← Cliente HTTP de Prometheus para timeseries
│   │   └── realtime.py             ← Redis pub/sub para SSE (HU-18 fase 4)
│   ├── utils/
│   │   ├── cuit.py                 ← Validación liviana CUIT
│   │   └── password.py             ← Generador temp 12 chars
│   └── workers/
│       └── tasks.py                ← arq WorkerSettings (sin tasks activas tras HU-18 —
│                                     back-worker apagado por default, ver docker-compose.yml)
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
├── nginx/nginx.conf                ← Config del reverse proxy (activo)
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

# 6. Correr worker arq (en otra terminal — opcional, sin tareas registradas
#    tras HU-18 va a fallar con 'at least one function must be registered')
uv run arq app.workers.tasks.WorkerSettings

# 7. Correr el seed (idempotente — agrega lo nuevo, no duplica). El seed
#    requiere SEED_*_PASSWORD env vars (en .env o inline al ejecutar).
$env:SEED_ADMIN_PASSWORD = "admin123"
$env:SEED_SUPERVISOR_PASSWORD = "super123"
$env:SEED_GERENTE_PASSWORD = "gerente123"
$env:SEED_REPARTIDOR_PASSWORD = "repartidor123"
uv run python -m scripts.seed
```

> Para correr el seed desde Docker en la VM:
> ```
> ssh ubuntu@10.201.0.67 'cd ~/cognipilot-back && docker compose exec -T \
>   -e SEED_ADMIN_PASSWORD=admin123 \
>   -e SEED_SUPERVISOR_PASSWORD=super123 \
>   -e SEED_GERENTE_PASSWORD=gerente123 \
>   -e SEED_REPARTIDOR_PASSWORD=repartidor123 \
>   back-api python -m scripts.seed'
> ```

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
| `POST /api/schedule` | Inline. App detecta el cambio por polling (HU-18) + publica al channel `realtime:schedule` (Redis pub/sub) para los SSE conectados | Sin dependencia externa. Latencia <20ms en respuesta, <100ms a celus con SSE conectado. |
| `POST /api/events/bulk` | Bulk insert en una transacción, max 500 eventos. Requiere Bearer (HU-03). | Una sola conexión DB, en vez de 500 INSERTs |
| `POST /api/positions` | Inline con haversine: si difiere <10m de la última, NO inserta fila (solo actualiza `lastLat/lastLng/lastSeen`). Requiere Bearer. | Controla crecimiento de la tabla con repartidor parado |
| `GET /api/realtime/stream` | SSE long-lived con `sse-starlette`. Listener al channel Redis `realtime:schedule`. nginx con `proxy_buffering off` para no romper el stream. | Push real-time a la app sin Google FCM ni broker externo. |

## Endpoints orientados al repartidor (HU-03)

Devuelven la vista personalizada del usuario auth. Solo rol `repartidor`.

| Endpoint | Para qué |
|---|---|
| `GET /api/me/ruta?fecha=YYYY-MM-DD` | Ruta asignada al repartidor para `fecha` (default: hoy en TZ AR). Devuelve `{ruta: {...}, paradas: [{..., paquetes: [...]}, ...]}`. 404 si no hay asignación. |
| `GET /api/me/reglas` | Reglas activas de la empresa del repartidor (filtra por `empresaId`, ordena por `createdAt DESC`). |

## Background jobs (in-process)

El proceso del API corre un loop async que cada 30s actualiza los gauges:

- `cognipilot_active_devices{window="5m"}` y `{window="24h"}` — count desde Postgres
- `cognipilot_arq_queue_depth{queue="arq:queue"}` — `ZCARD` sobre Redis

Esto hace que Grafana y el dashboard del admin tengan series temporales actualizadas sin necesidad de un cron externo.

## Deploy en la VM UM-Cloud

El compose soporta **dos modos**:

### Modo PARALELO (default — para validar antes del cutover)

El back-api corre junto al back viejo (Next.js sigue en :3000). back-api conecta al **postgres existente** del back viejo via `host.docker.internal:5432`. Postgres y pgbouncer del compose nuevo **no se levantan**.

```powershell
# 1. Clonar en la VM
ssh -i F:\Proys\cognipilot-um.pem ubuntu@10.201.0.67
git clone https://github.com/fmlucero/CogniPilotBack.git cognipilot-back
cd cognipilot-back

# 2. Crear .env con los MISMOS JWT_SECRET y POSTGRES_PASSWORD que el back viejo:
cp .env.example .env
nano .env   # completar con valores del back viejo

# 3. Levantar solo redis + back-api (el back-worker está desactivado por default
#    tras HU-18 porque no hay tareas async registradas — ver §back-worker)
docker compose up -d --build redis back-api

# 4. Ver logs
docker compose logs -f back-api
```

### El back-worker (arq) está apagado por default

Tras HU-18 se removió la única función async registrada (`send_schedule_push_task`
de FCM). `WorkerSettings.functions` quedó vacío y arq se niega a arrancar.

Para evitar el restart-loop, `back-worker` está en el profile `with-worker` del
compose. Cuando agregues la primera task async real (batch processing, cron,
etc.) y la registres en `app/workers/tasks.py`, levantar con:

```powershell
docker compose --profile with-worker up -d back-worker
```

Necesario: agregar regla TCP **8001** en el security group `cognipilot-um` desde `192.168.3.0/24`.

Validación rápida (desde PC con ZeroTier):
```powershell
curl http://10.201.0.67:8001/health
curl http://10.201.0.67:8001/api/schedule   # GET público, devuelve la regla seeded
```

### Modo BUNDLED (post-cutover, todo en este compose)

```powershell
# Editar .env: comentar las DATABASE_URL de modo paralelo, descomentar las de @pgbouncer/@postgres
ssh -i F:\Proys\cognipilot-um.pem ubuntu@10.201.0.67
cd cognipilot-back

# Stack completo
docker compose --profile bundled-db --profile with-nginx up -d --build

# Con monitoreo:
docker compose --profile bundled-db --profile with-nginx --profile monitoring up -d --build

# Escalar el API a 4 réplicas:
docker compose up -d --scale back-api=4
```

## Migración desde el back viejo (Next.js) — plan de cutover sin downtime

| # | Paso | Estado |
|---|---|---|
| 1 | Skeleton + auth + CRUDs (empresas, usuarios) | ✅ |
| 2 | Endpoints schedule + events + devices/register | ✅ |
| 3 | Observabilidad (Prometheus + endpoints `/api/metrics`) | ✅ |
| 4 | Seed Python equivalente al de Prisma | ✅ |
| 5 | arq async para FCM push — **removido en HU-18** (`POST /api/schedule` ya no encola push, la app hace polling) | ✅ (revertido) |
| 6 | Endpoints calientes: `POST /api/events/bulk` (bulk insert), `POST /api/positions` (haversine + diff) | ✅ |
| 7 | Loop periódico que refresca gauges (active_devices, queue_depth) — corre dentro de FastAPI | ✅ |
| 8 | nginx config para reverse proxy (`/api` → FastAPI, resto → Next.js) | ✅ |
| 9 | Alembic baseline `5a1e1b850521` → stamp DB existente | ✅ |
| 10 | `back-api` corriendo en :8001 en la VM, parity validado contra DB real | ✅ |
| 11A | nginx adelante en :80 (parallel safe). JWT cross-backend validado (cookie de FastAPI → Server Components de Next.js) | ✅ |
| 11B | Cloudflare Tunnel re-apuntado a nginx (`http://localhost:80`). cloudflared corre como systemd service (sobrevive reboots, ver I-16). URL del tunnel rota con cada arranque — leer la actual con `~/cfurl.sh` en la VM. | ✅ |
| 12 | Cleanup Next.js: borrado `app/api/*` y `lib/{prisma,firebase-admin,password}.ts`. Server Components hacen `serverFetch()` a FastAPI con cookie forwarding. Front quedó como **solo UI**. Commit `7091e18` en repo `CogniPilotRemote`. | ✅ |
| 13 | HU-18 — remover Firebase Cloud Messaging del back: borrado `app/services/fcm.py`, refs en schedule/observability/metrics, removida dep `firebase-admin` del `pyproject.toml`. App Android pasa a polling propio + SSE. | ✅ |
| 14 | HU-03 — endpoints `/api/me/{ruta,reglas}` + auth obligatoria en `/api/events`. App Android con login JWT + Room para cache offline. | ✅ |
| 15 | Dashboard React en `/admin/metricas` consumiendo `/api/metrics/*` (HU-21) | ⏳ |

Tokens JWT viejos siguen funcionando (mismo HS256, mismo secret, mismas claims) → durante el cutover **nadie se desloguea**. Validado end-to-end: cookies emitidas por FastAPI funcionan en Server Components de Next.js sin reconfiguración.

## Observabilidad

Dos capas, integradas:

### A) Para el equipo de plataforma (Grafana — drill-down técnico)

Profile `monitoring` en el compose. Levantar con:

```powershell
docker compose --profile monitoring up -d prometheus grafana
```

- **Prometheus** (`http://10.201.0.67:9090` interno): scrapea `back-api:8000/metrics` cada 15s. Retención 15 días.
- **Grafana** (`http://10.201.0.67:3001`): auto-provisiona el datasource Prometheus + dashboard "CogniPilot — Overview" con paneles de req/s, p50/p95/p99, error rate, eventos ingresados por tipo.
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
| `queue_depth` | Jobs encolados en arq |

Parametros: `window=15m|1h|6h|24h|7d`, `step=15..3600` (segundos).

Si Prometheus no está corriendo, los endpoints devuelven `prometheus_available: false` y arrays vacíos (degradación graceful). El `overview` igual devuelve los contadores in-process + counts directos de DB.

### C) Métricas custom de negocio

Definidas en `app/core/observability.py`:

- `cognipilot_events_ingested_total{tipo=...}` — counter por tipo de evento
- `cognipilot_active_devices{window=5m|24h}` — gauge (actualizado cada 30s por el loop async del lifespan de FastAPI)
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
