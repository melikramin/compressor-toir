"""Настройки из окружения. Падаем на старте, если чего-то не хватает."""
import os
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    api_base: str
    api_key: str
    interval: int
    pg_dsn: str
    toir_user: str
    toir_password: str


def _env(key: str, default: str | None = None, required: bool = False) -> str:
    val = os.environ.get(key, default)
    if required and not val:
        print(f"FATAL: не задана переменная {key}", file=sys.stderr)
        sys.exit(1)
    return val or ""


def load() -> Settings:
    user = _env("POSTGRES_USER", "toir")
    password = _env("POSTGRES_PASSWORD", required=True)
    dbname = _env("POSTGRES_DB", "toir")
    return Settings(
        api_base=_env("API_BASE", "https://com.geotek.app/api").rstrip("/"),
        api_key=_env("API_KEY", required=True),
        interval=int(_env("SYNC_INTERVAL", "300")),
        # Внутри compose ходим к сервису postgres по docker-сети, не через
        # опубликованный порт — PG_BIND/PG_PORT только для 1С.
        pg_dsn=f"postgresql://{user}:{password}@postgres:5432/{dbname}",
        toir_user=_env("TOIR_1C_USER", "toir_1c"),
        toir_password=_env("TOIR_1C_PASSWORD", required=True),
    )
