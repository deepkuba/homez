import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from homefinder.catalog.orm import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.environ.get(
    "HOMEFINDER_DATABASE_URL",
    os.environ.get("DATABASE_URL", "sqlite:///homefinder-preview.db"),
)
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
target_metadata = Base.metadata


def include_object(
    _object: object,
    name: str | None,
    type_: str,
    reflected: bool,
    _compare_to: object | None,
) -> bool:
    """Ignore tables owned by extensions rather than application metadata."""
    schema = getattr(_object, "schema", None)
    return not (
        type_ == "table"
        and reflected
        and (name == "spatial_ref_sys" or schema in {"tiger", "topology"})
    )


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
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
        if connection.dialect.name == "postgresql":
            # PostGIS images add extension schemas to the database search path.
            # Alembic owns application tables in public only.
            connection.exec_driver_sql("SET search_path TO public")
            connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
