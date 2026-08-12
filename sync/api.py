"""Клиент платформы: снимок таблицы 1С:ТОиР по API-ключу."""
import logging
import time

import requests

log = logging.getLogger("api")

# Колонки, без которых строка бессмысленна. Остальное может быть NULL.
REQUIRED = ("tag", "object_id", "serial", "param_code", "updated_at")


class ApiError(Exception):
    pass


class Toir1CClient:
    def __init__(self, base: str, key: str, attempts: int = 3):
        self.url = f"{base}/integration/toir-1c"
        self.key = key
        self.attempts = attempts

    def fetch(self) -> list[dict]:
        """Снимок целиком. Бросает ApiError — цикл её ловит и пропускает такт."""
        last = None
        for i in range(1, self.attempts + 1):
            try:
                resp = requests.get(
                    self.url,
                    headers={"X-API-Key": self.key},
                    timeout=(10, 60),
                )
            except requests.RequestException as e:
                last = f"сеть: {e}"
            else:
                # Неверный ключ ретраить бессмысленно — выходим сразу.
                if resp.status_code in (401, 403):
                    raise ApiError(f"ключ отвергнут (HTTP {resp.status_code})")
                if resp.status_code != 200:
                    last = f"HTTP {resp.status_code}"
                else:
                    return _validate(resp)
            if i < self.attempts:
                time.sleep(2 ** i)
                log.info("попытка %d/%d не удалась (%s), повтор", i, self.attempts, last)
        raise ApiError(last or "неизвестная ошибка")


def _validate(resp) -> list[dict]:
    try:
        rows = resp.json()
    except ValueError as e:
        raise ApiError(f"ответ не JSON: {e}") from e
    if not isinstance(rows, list):
        raise ApiError(f"ожидался массив, пришёл {type(rows).__name__}")
    for row in rows:
        missing = [k for k in REQUIRED if not isinstance(row, dict) or row.get(k) in (None, "")]
        if missing:
            raise ApiError(f"строка без обязательных полей {missing}: {row}")
    return rows
