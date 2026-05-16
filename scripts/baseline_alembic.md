# Alembic baseline — tomar ownership de la DB existente sin tocar datos

La DB ya tiene 12 tablas creadas por Prisma + datos seeded.
El objetivo es que Alembic crea que "ya aplicó" el schema actual y a partir de ahora gestione los cambios.

## Procedimiento (correr UNA VEZ desde la VM)

```powershell
# 1. SSH a la VM
ssh -i F:\Proys\cognipilot-um.pem ubuntu@10.201.0.67

# 2. Entrar al directorio del back (cuando lo subamos):
cd ~/cognipilot-back

# 3. Levantar solo postgres (back-api aún no, para no ejecutar migraciones)
docker compose up -d postgres

# 4. Generar la migración inicial autogenerada por Alembic (compara modelos vs DB)
docker compose run --rm back-api alembic revision --autogenerate -m "baseline desde prisma"

# Esto crea alembic/versions/YYYYMMDD_HHMM_baseline_desde_prisma.py
# CON los CREATE TABLEs de las 12 tablas. Las tablas YA EXISTEN en la DB,
# así que tenemos que decirle a Alembic que esa migración ya está aplicada,
# SIN correrla (sino petaría con "table already exists").

# 5. Stamp: marcar la DB como si ya hubiera corrido esa migración
docker compose run --rm back-api alembic stamp head

# 6. Verificar: la tabla alembic_version queda con el ID de la baseline
docker compose exec postgres psql -U cognipilot -d cognipilot -c "SELECT * FROM alembic_version;"
```

A partir de ese momento:
- Cualquier cambio futuro al schema se hace **editando los modelos SQLAlchemy en `app/models/`**
- Generás migración con: `alembic revision --autogenerate -m "qué hace"`
- Aplicás con: `alembic upgrade head`
- Prisma queda fuera del flujo de schema (no toques `cognipilot-remote/prisma/schema.prisma` más).

## Diferencias esperables vs Prisma (cosméticas)

Alembic autogen puede detectar "diferencias" con la DB que en realidad son equivalentes semánticamente:

- **Nombres de constraints/índices**: Prisma usa nombres como `Empresa_nombre_key`, Alembic preferiría `uq_empresa_nombre`. Si autogen los renombra, los podemos dejar como están en la DB editando la migración a mano (o convivir con los nombres de Prisma).
- **Order de columnas**: irrelevante en Postgres.
- **Defaults server-side**: Prisma a veces usa app-side defaults; Alembic puede querer agregarlos como server_default. Revisar la migración antes del `stamp`.

Recomendación práctica: **revisar el archivo de la migración antes del stamp**. Si tiene cosas raras, la editamos a mano o usamos un `--autogenerate` más restrictivo.
