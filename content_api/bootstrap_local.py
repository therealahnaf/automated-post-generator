"""Explicitly provision an isolated database on local Docker pg, port 15432.

No container restarts or changes to existing databases. Credentials stay in
memory and ignored .env files, never process arguments or printed output.
"""

import json
import secrets
import subprocess
from pathlib import Path
from urllib.parse import quote

import psycopg
from dotenv import dotenv_values, set_key
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]


def main():
    config = ROOT / "content_api" / ".env"
    if dotenv_values(config).get("CONTENT_DATABASE_URL"):
        print("Local content configuration already exists; left unchanged.")
        return
    inspected = json.loads(
        subprocess.check_output(["docker", "inspect", "pg"], text=True)
    )[0]
    values = dict(
        value.split("=", 1) for value in inspected["Config"]["Env"] if "=" in value
    )
    password, key = secrets.token_urlsafe(36), secrets.token_urlsafe(48)
    with psycopg.connect(
        host="127.0.0.1",
        port=15432,
        dbname="postgres",
        user=values.get("POSTGRES_USER", "postgres"),
        password=values.get("POSTGRES_PASSWORD"),
        autocommit=True,
        connect_timeout=5,
    ) as conn:
        if conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = 'bits_today'"
        ).fetchone():
            raise RuntimeError(
                "bits_today exists; configure it manually rather than changing it."
            )
        if conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = 'bits_today_web'"
        ).fetchone():
            raise RuntimeError(
                "bits_today_web exists; refusing to change its password."
            )
        conn.execute(
            sql.SQL("CREATE ROLE bits_today_web LOGIN PASSWORD {}").format(
                sql.Literal(password)
            )
        )
        conn.execute("CREATE DATABASE bits_today OWNER bits_today_web")
    set_key(
        config,
        "CONTENT_DATABASE_URL",
        f"postgresql://bits_today_web:{quote(password)}@127.0.0.1:15432/bits_today",
    )
    set_key(config, "CONTENT_API_KEY", key)
    set_key(ROOT / ".env", "CONTENT_API_URL", "http://127.0.0.1:8001")
    set_key(ROOT / ".env", "CONTENT_API_KEY", key)
    print(
        "Created isolated bits_today database and login; saved ignored API/publisher configuration."
    )


if __name__ == "__main__":
    main()
