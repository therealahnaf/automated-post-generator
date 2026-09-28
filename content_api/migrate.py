"""Explicit, additive schema setup: python -m content_api.migrate."""

from pathlib import Path

import psycopg

from .config import database_url


def migrate() -> None:
    with psycopg.connect(database_url(), connect_timeout=5) as connection:
        # Serialize simultaneous deployments; never drop or reset application data.
        connection.execute("SELECT pg_advisory_xact_lock(7429381)")
        connection.execute(Path(__file__).with_name("schema.sql").read_text())


if __name__ == "__main__":
    migrate()
    print("Content schema is ready.")
