import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))
load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def database_url() -> str:
    value = os.getenv("CONTENT_DATABASE_URL", "")
    if not value:
        raise RuntimeError("CONTENT_DATABASE_URL is required.")
    return value


def api_key() -> str:
    value = os.getenv("CONTENT_API_KEY", "")
    if len(value) < 32:
        raise RuntimeError("CONTENT_API_KEY must contain at least 32 characters.")
    return value
