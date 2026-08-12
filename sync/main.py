"""Цикл синхронизации: снимок с платформы → локальный Postgres.

Сбой API — пропускаем такт, строки оставляем прошлыми (CLAUDE.md,
«Сбой не стирает данные»).
"""
import logging
import signal
import sys
import threading
from datetime import datetime, timezone

import config
from api import ApiError, Toir1CClient
from db import Database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("sync")

_stop = threading.Event()


def _now():
    return datetime.now(timezone.utc)


def wait_for_db(db: Database, attempts: int = 30, delay: int = 5) -> bool:
    for i in range(1, attempts + 1):
        try:
            db.ping()
            log.info("postgres доступен (попытка %d)", i)
            return True
        except Exception as e:
            log.info("жду postgres (%d/%d): %s", i, attempts, e)
            if _stop.wait(delay):
                return False
    log.error("postgres недоступен после %d попыток", attempts)
    return False


def run_cycle(client: Toir1CClient, db: Database) -> None:
    started = _now()
    try:
        rows = client.fetch()
    except ApiError as e:
        log.warning("такт пропущен (API): %s — строки не тронуты", e)
        db.record_state(started, error=f"API: {e}")
        return

    if not rows:
        # Платформа отдала пустой массив. Это либо парк пуст, либо что-то не так
        # на той стороне; в обоих случаях стирать локальную таблицу нельзя.
        log.warning("такт пропущен: платформа вернула 0 строк — строки не тронуты")
        db.record_state(started, error="платформа вернула 0 строк")
        return

    try:
        written = db.mirror(rows)
    except Exception as e:
        log.warning("такт пропущен (БД): %s — строки не тронуты", e)
        db.record_state(started, error=f"БД: {e}")
        return

    log.info("такт ок: %d тегов", written)
    db.record_state(started, success_at=_now(), rows=written, error=None)


def main() -> None:
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: _stop.set())

    cfg = config.load()
    db = Database(cfg.pg_dsn, cfg.toir_user, cfg.toir_password)
    if not wait_for_db(db):
        sys.exit(1)
    db.ensure_schema()

    client = Toir1CClient(cfg.api_base, cfg.api_key)
    log.info("служба запущена: %s, период %d c", client.url, cfg.interval)

    while not _stop.is_set():
        try:
            run_cycle(client, db)
        except Exception as e:  # такт не имеет права уронить службу
            log.exception("непредвиденная ошибка в такте: %s", e)
        _stop.wait(cfg.interval)
    log.info("остановлено по сигналу")


if __name__ == "__main__":
    main()
