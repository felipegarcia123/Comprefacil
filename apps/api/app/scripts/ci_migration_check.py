"""Validate Alembic on an empty, disposable PostgreSQL database in CI.

The Alembic INI has Docker paths; CI overrides only those paths in memory.
"""

import hashlib
import json
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, UniqueConstraint, create_engine, inspect
from sqlalchemy.engine import make_url


ROOT = Path(__file__).resolve().parents[4]
MIGRATION_ONLY_CHECKS = {
    "cash_movements": {"ck_cash_movements_type", "ck_cash_movements_withdrawal_reason"},
    "electronic_invoices": {"ck_electronic_invoices_status"},
    "expenses": {"ck_expenses_payment_source"},
    "inventory_movements": {"ck_inventory_movements_type"},
    "payment_channels": {"ck_payment_channels_payment_group"},
    "purchases": {"purchasestatus"},
    "sales": {"ck_sales_source"},
}
MIGRATION_ONLY_INDEXES = {"sales": {"ix_sales_status_completed_at"}}


def require_disposable_database():
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        raise RuntimeError("DATABASE_URL must point to the disposable CI database")
    url = make_url(raw)
    if (url.drivername != "postgresql+psycopg" or url.host not in {"127.0.0.1", "localhost"}
            or url.database != "comprefacil_ci" or url.username != "ci"):
        raise RuntimeError("Refusing to migrate a database outside the disposable CI target")
    return url


def schema_fingerprint(connection):
    inspector = inspect(connection)
    snapshot = {}
    for name in sorted(inspector.get_table_names()):
        snapshot[name] = {
            "columns": sorted((column["name"], str(column["type"]), column["nullable"])
                              for column in inspector.get_columns(name)),
            "indexes": sorted((index["name"], tuple(index["column_names"]), index["unique"])
                              for index in inspector.get_indexes(name)),
            "primary_key": inspector.get_pk_constraint(name)["constrained_columns"],
            "foreign_keys": sorted((tuple(key["constrained_columns"]), key["referred_table"],
                                    tuple(key["referred_columns"]))
                                   for key in inspector.get_foreign_keys(name)),
            "unique": sorted(tuple(item["column_names"])
                             for item in inspector.get_unique_constraints(name)),
            "checks": sorted((item["name"], item["sqltext"])
                             for item in inspector.get_check_constraints(name)),
        }
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


def validate_schema(connection, metadata, head):
    inspector = inspect(connection)
    actual_tables = set(inspector.get_table_names()) - {"alembic_version"}
    expected_tables = set(metadata.tables)
    assert actual_tables == expected_tables, ("tables", sorted(expected_tables - actual_tables),
                                               sorted(actual_tables - expected_tables))
    assert MigrationContext.configure(connection).get_current_revision() == head

    for name, table in metadata.tables.items():
        columns = {column["name"]: column for column in inspector.get_columns(name)}
        assert set(columns) == set(table.columns.keys()), (name, "columns")
        for column in table.columns:
            assert columns[column.name]["nullable"] == column.nullable, (name, column.name, "nullable")

        indexes = {index["name"]: index for index in inspector.get_indexes(name)}
        required_indexes = {index.name for index in table.indexes}
        required_indexes |= MIGRATION_ONLY_INDEXES.get(name, set())
        assert required_indexes <= set(indexes), (name, "indexes", required_indexes - set(indexes))
        for index in table.indexes:
            assert indexes[index.name]["unique"] == index.unique, (name, index.name, "unique")

        assert tuple(inspector.get_pk_constraint(name)["constrained_columns"]) == tuple(
            column.name for column in table.primary_key.columns), (name, "primary key")

        actual_foreign_keys = {
            (tuple(key["constrained_columns"]), key["referred_table"], tuple(key["referred_columns"]))
            for key in inspector.get_foreign_keys(name)
        }
        expected_foreign_keys = {
            (tuple(column.name for column in key.columns), key.referred_table.name,
             tuple(element.column.name for element in key.elements))
            for key in table.foreign_key_constraints
        }
        assert expected_foreign_keys <= actual_foreign_keys, (name, "foreign keys",
                                                         expected_foreign_keys - actual_foreign_keys)

        actual_unique = {tuple(item["column_names"]) for item in inspector.get_unique_constraints(name)}
        actual_unique |= {tuple(item["column_names"]) for item in indexes.values() if item["unique"]}
        expected_unique = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints if isinstance(constraint, UniqueConstraint)
        }
        assert expected_unique <= actual_unique, (name, "unique constraints",
                                                  expected_unique - actual_unique)

        actual_checks = {item["name"] for item in inspector.get_check_constraints(name)}
        expected_checks = {
            constraint.name for constraint in table.constraints if isinstance(constraint, CheckConstraint)
        }
        expected_checks |= MIGRATION_ONLY_CHECKS.get(name, set())
        assert expected_checks <= actual_checks, (name, "check constraints",
                                                  expected_checks - actual_checks)

    return len(actual_tables)


def main():
    url = require_disposable_database()
    from app.core.db import Base  # imported after the CI database guard
    from app import models  # noqa: F401 - register all ORM tables

    config = Config(str(ROOT / "database" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "database" / "migrations"))
    config.set_main_option("prepend_sys_path", str(ROOT / "apps" / "api"))
    head = ScriptDirectory.from_config(config).get_current_head()
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert not inspect(connection).get_table_names(), "CI PostgreSQL must start empty"
        command.upgrade(config, "head")
        with engine.connect() as connection:
            count = validate_schema(connection, Base.metadata, head)
            before = schema_fingerprint(connection)
        command.upgrade(config, "head")
        with engine.connect() as connection:
            validate_schema(connection, Base.metadata, head)
            assert schema_fingerprint(connection) == before, "second upgrade changed the schema"
        print(f"Alembic {head}: {count} domain tables; schema validated; second upgrade unchanged")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
