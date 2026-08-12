"""Критичная логика: мэппинг строки API → строка БД и защита mirror-delete.

Остальное тестами не покрываем — CLAUDE.md, «Тесты только для критичного».
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api import ApiError, _validate  # noqa: E402
from db import COLUMNS, Database, to_record  # noqa: E402

ROW = {
    "tag": "50317504_OUTLET_PRESSURE",
    "object_id": 13473,
    "serial": "50317504",
    "param_code": "OUTLET_PRESSURE",
    "param_name": "Давление на выходе",
    "signal_type": "Показатель",
    "value": 0.69,
    "value_text": "0.69 MPa",
    "unit": "MPa",
    "ts": "2026-08-12T10:41:39+00:00",
    "updated_at": "2026-08-12T10:41:44.123456+00:00",
}


def test_record_follows_column_order():
    """Порядок значений обязан совпадать с COLUMNS — иначе поля разъедутся молча."""
    rec = to_record(ROW)
    assert len(rec) == len(COLUMNS)
    assert dict(zip(COLUMNS, rec)) == ROW


def test_nullable_fields_survive():
    """value/unit/ts могут быть NULL: параметр без связи или без единицы."""
    row = {**ROW, "value": None, "unit": "", "ts": None}
    rec = dict(zip(COLUMNS, to_record(row)))
    assert rec["value"] is None and rec["ts"] is None and rec["unit"] == ""


def test_unknown_extra_field_ignored():
    """Платформа добавила колонку — мы не падаем, просто её не переносим."""
    rec = to_record({**ROW, "brand_new": 1})
    assert len(rec) == len(COLUMNS)


def test_empty_snapshot_never_wipes_table():
    """Пустой снимок обязан отвергаться до похода в БД."""
    db = Database("postgresql://nobody@127.0.0.1:1/none", "toir_1c", "x")
    with pytest.raises(ValueError):
        db.mirror([])


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        if isinstance(self._payload, str):
            raise ValueError("not json")
        return self._payload


def test_validate_rejects_row_without_tag():
    with pytest.raises(ApiError):
        _validate(_Resp([{**ROW, "tag": None}]))


def test_validate_rejects_non_list():
    with pytest.raises(ApiError):
        _validate(_Resp({"rows": []}))


def test_validate_accepts_good_payload():
    assert _validate(_Resp([ROW])) == [ROW]
