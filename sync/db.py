"""Локальный Postgres: схема, read-only роль для 1С, зеркалирование снимка.

Схема повторяет toir.v_toir_1c платформы колонка в колонку — см. CLAUDE.md,
«Зеркало — значит зеркало».
"""
import logging

import psycopg
from psycopg import sql

log = logging.getLogger("db")

COLUMNS = (
    "tag", "object_id", "serial", "param_code", "param_name", "signal_type",
    "value", "value_text", "unit", "ts", "updated_at",
)

DDL = """
CREATE TABLE IF NOT EXISTS public.toir_1c (
    tag         VARCHAR(64) PRIMARY KEY,
    object_id   INT NOT NULL,
    serial      VARCHAR(32) NOT NULL,
    param_code  VARCHAR(32) NOT NULL,
    param_name  VARCHAR(128) NOT NULL,
    signal_type VARCHAR(32) NOT NULL,
    value       DOUBLE PRECISION,
    value_text  VARCHAR(128),
    unit        VARCHAR(16),
    ts          TIMESTAMPTZ,
    updated_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS toir_1c_object_idx ON public.toir_1c (object_id);

CREATE TABLE IF NOT EXISTS public.sync_state (
    id              SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    last_attempt_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    rows_written    INT,
    last_error      TEXT
);
INSERT INTO public.sync_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

CREATE SCHEMA IF NOT EXISTS toir;

-- Колонки перечислены явно: контракт с 1С не должен поехать от того, что в
-- таблицу добавили поле.
CREATE OR REPLACE VIEW toir.v_toir_1c AS
SELECT tag, object_id, serial, param_code, param_name, signal_type,
       value, value_text, unit, ts, updated_at
  FROM public.toir_1c;

CREATE OR REPLACE VIEW toir.v_sync_state AS
SELECT last_attempt_at, last_success_at, rows_written, last_error
  FROM public.sync_state;
"""

UPSERT = """
INSERT INTO public.toir_1c ({cols}) VALUES ({phs})
ON CONFLICT (tag) DO UPDATE SET {sets}
""".format(
    cols=", ".join(COLUMNS),
    phs=", ".join("%s" for _ in COLUMNS),
    sets=", ".join(f"{c} = EXCLUDED.{c}" for c in COLUMNS if c != "tag"),
)


def to_record(row: dict) -> tuple:
    """Строка API → кортеж под UPSERT. Psycopg сам разберёт ISO-8601 в timestamptz."""
    return tuple(row.get(c) for c in COLUMNS)


class Database:
    name = "postgres"

    def __init__(self, dsn: str, toir_user: str, toir_password: str):
        self.dsn = dsn
        self.toir_user = toir_user
        self.toir_password = toir_password

    def _connect(self):
        return psycopg.connect(self.dsn, connect_timeout=10)

    def ping(self) -> None:
        with self._connect() as conn:
            conn.execute("SELECT 1")

    def ensure_schema(self) -> None:
        """Идемпотентно: таблицы, view, read-only роль для 1С."""
        with self._connect() as conn:
            conn.execute(DDL)
            self._ensure_role(conn)
            conn.commit()
        log.info("схема готова, роль '%s' синхронизирована", self.toir_user)

    def _ensure_role(self, conn) -> None:
        """Роль видит только схему toir и только на чтение.

        Пароль перезаписываем на каждом старте: единственный источник правды —
        .env, чтобы смена пароля не требовала лезть в psql.
        """
        exists = conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (self.toir_user,)
        ).fetchone()
        ident = sql.Identifier(self.toir_user)
        verb = "ALTER" if exists else "CREATE"
        conn.execute(
            sql.SQL("{} ROLE {} LOGIN PASSWORD {}").format(
                sql.SQL(verb), ident, sql.Literal(self.toir_password)
            )
        )
        for stmt in (
            "GRANT CONNECT ON DATABASE {db} TO {role}",
            "GRANT USAGE ON SCHEMA toir TO {role}",
            "GRANT SELECT ON toir.v_toir_1c TO {role}",
            "GRANT SELECT ON toir.v_sync_state TO {role}",
            # Чтобы `SELECT ... FROM v_toir_1c` работал без указания схемы —
            # тот же запрос, что и при подключении к платформе.
            "ALTER ROLE {role} SET search_path = toir",
            # Public клиенту не нужен: там лежат сами таблицы.
            "REVOKE ALL ON SCHEMA public FROM {role}",
        ):
            conn.execute(
                sql.SQL(stmt).format(
                    db=sql.Identifier(conn.info.dbname), role=ident
                )
            )

    def mirror(self, rows: list[dict]) -> int:
        """UPSERT снимка + удаление всего, чего в нём нет. Одна транзакция.

        Пустой снимок не принимаем: это стёрло бы таблицу целиком.
        """
        if not rows:
            raise ValueError("пустой снимок — зеркалировать нечего")
        tags = [r["tag"] for r in rows]
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.executemany(UPSERT, [to_record(r) for r in rows])
                cur.execute("DELETE FROM public.toir_1c WHERE tag <> ALL(%s)", (tags,))
                dropped = cur.rowcount
            conn.commit()
        if dropped:
            log.info("удалено строк, которых больше нет на платформе: %d", dropped)
        return len(rows)

    def read_diagnostics(self) -> dict:
        """Всё для команды status за одно подключение. Только чтение."""
        with self._connect() as conn:
            state = conn.execute(
                "SELECT last_success_at, last_error FROM public.sync_state WHERE id = 1"
            ).fetchone()
            rows, max_ts = conn.execute(
                "SELECT count(*), max(ts) FROM public.toir_1c"
            ).fetchone()
            machines = conn.execute(
                "SELECT serial, count(*), max(ts) FROM public.toir_1c"
                " GROUP BY serial ORDER BY serial"
            ).fetchall()
        success, error = state or (None, None)
        return {
            "last_success_at": success,
            "last_error": error,
            "rows": rows,
            "max_ts": max_ts,
            "machines": machines,
        }

    def record_state(self, attempt_at, success_at=None, rows=None, error=None) -> None:
        """Отметка о цикле — чтобы диагностировать, не читая логи."""
        try:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE public.sync_state SET last_attempt_at = %s, "
                    "last_success_at = COALESCE(%s, last_success_at), "
                    "rows_written = COALESCE(%s, rows_written), last_error = %s "
                    "WHERE id = 1",
                    (attempt_at, success_at, rows, error),
                )
                conn.commit()
        except psycopg.Error as e:
            log.warning("не записал sync_state: %s", e)
