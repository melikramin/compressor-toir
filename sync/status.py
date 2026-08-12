"""Состояние службы одной командой.

Читает то же, что видит 1С, и печатает человеку. Ничего не меняет.
Код возврата: 0 — работает, 1 — сбой последнего такта, 2 — данные устарели
или нет связи с базой (годится для мониторинга).
"""
import sys
from datetime import datetime, timezone

import config
from db import Database

# Служба считается отставшей, если успешного такта не было три периода подряд.
STALE_CYCLES = 3
# Приборы молчат больше часа — не авария службы, но клиенту это надо видеть.
DEVICE_STALE = 3600

EXIT = {"ok": 0, "warn": 1, "bad": 2}


def _ago(then, now):
    """«2 мин назад» — понятнее, чем UTC-штамп в чужом часовом поясе."""
    if then is None:
        return "никогда"
    sec = int((now - then).total_seconds())
    if sec < 60:
        return "только что"
    if sec < 3600:
        return f"{sec // 60} мин назад"
    if sec < 86400:
        return f"{sec // 3600} ч {sec % 3600 // 60} мин назад"
    return f"{sec // 86400} сут назад"


def _stamp(then):
    return then.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M:%S UTC") if then else "—"


def status(diag, interval, now):
    """Светофор: связь с платформой и свежесть локальной таблицы."""
    ok_at, err = diag["last_success_at"], diag["last_error"]
    if ok_at is None:
        return "bad", "НЕ РАБОТАЕТ", err or "успешной синхронизации ещё не было"
    lag = (now - ok_at).total_seconds()
    if lag > interval * STALE_CYCLES:
        # Данные в базе есть, но уже не обновляются — снаружи это выглядит как
        # рабочая таблица, поэтому говорим прямо.
        return "bad", "ДАННЫЕ УСТАРЕЛИ", err or "синхронизации давно не было"
    if err:
        # Один такт не прошёл, предыдущие свежие — не повод пугать.
        return "warn", "СБОЙ ПОСЛЕДНЕГО ТАКТА", err
    return "ok", "РАБОТАЕТ", ""


def _paint(level, text):
    if not sys.stdout.isatty():
        return text
    return {"ok": "\033[32m", "warn": "\033[33m", "bad": "\033[31m"}[level] + text + "\033[0m"


def main() -> int:
    cfg = config.load()
    now = datetime.now(timezone.utc)
    try:
        diag = Database(cfg.pg_dsn, cfg.toir_user, cfg.toir_password).read_diagnostics()
    except Exception as e:
        print("compressor-toir\n")
        print(" ", _paint("bad", "НЕТ СВЯЗИ С ЛОКАЛЬНОЙ БАЗОЙ"))
        print("  ", e, sep="")
        print("\n  Проверить: docker compose ps")
        return 2

    level, title, detail = status(diag, cfg.interval, now)
    print("compressor-toir\n")
    print(" ", _paint(level, title))
    if detail:
        print(f"  {detail}")
    print()
    print(f"  Последняя синхронизация  {_ago(diag['last_success_at'], now)}"
          f"  ({_stamp(diag['last_success_at'])})")
    print(f"  Строк в таблице          {diag['rows']}")
    dev = diag["max_ts"]
    stale = " — приборы давно не присылали данные" if (
        dev is None or (now - dev).total_seconds() > DEVICE_STALE) else ""
    print(f"  Данные приборов          {_ago(dev, now)}{stale}")
    print(f"  Опрос платформы          раз в {cfg.interval // 60} мин")

    if diag["machines"]:
        print("\n  Компрессоры:")
        width = max(len(m[0]) for m in diag["machines"])
        for serial, cnt, ts in diag["machines"]:
            print(f"    {serial:<{width}}  {cnt:>3} парам.  {_ago(ts, now)}")
    else:
        print("\n  Таблица пуста.")
    print()
    return EXIT[level]


if __name__ == "__main__":
    sys.exit(main())
