"""Alembic env — usa DATABASE_URL_SYNC del .env (psycopg2 sync).

Importa todos los modelos vía `app.models` para que Base.metadata esté completo
y Alembic pueda comparar contra la DB.
"""
from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Importar Base y modelos para que metadata esté poblada.
# get_settings() llamada acá inicializa pydantic-settings y lee .env.
from app.core.config import get_settings  # noqa: E402
from app.core.db import Base  # noqa: E402
import app.models  # noqa: F401, E402

config = context.config

# Inyectar DATABASE_URL_SYNC del settings
_settings = get_settings()
config.set_main_option("sqlalchemy.url", _settings.database_url_sync)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
