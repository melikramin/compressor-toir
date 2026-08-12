# compressor-toir

Служба-зеркало: раз в 5 минут забирает таблицу 1С:ТОиР у платформы
`com.geotek.app` по HTTPS и кладёт её в локальный Postgres. Ставится **у
клиента**, к которому подключается его 1С:ТОиР.

Смысл: клиенту нужна база в своей локальной сети, а не подключение к нашей.
Направление инвертировано — наружу ходим мы, у клиента не открыт ни один
входящий порт.

```
com.geotek.app ──HTTPS──> [sync] ──> [postgres] <──локально── 1С:ТОиР
   (наш API)              контур клиента
```

- Установка и передача данных интегратору — [`INSTALL.md`](INSTALL.md).
- Правила разработки и контракт — [`CLAUDE.md`](CLAUDE.md).

## Быстрый старт

На чистом сервере — одной командой (поставит Docker, если его нет):

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/melikramin/compressor-toir/main/install.sh)"
```

Если проект уже скачан:

```bash
./setup.sh
```

Спросит API-ключ, сгенерирует пароли, поднимет контейнеры и покажет реквизиты
для интегратора 1С. Второй запуск открывает меню: состояние, реквизиты, смена
ключа и адреса платформы.

`API_KEY` выдаёт admin в UI платформы: «Настройки → 1С:ТОиР → API-ключи».

Вручную, если нужно:

```bash
cp .env.example .env      # заполнить API_KEY и оба пароля
docker compose up -d --build
docker compose logs -f sync        # ждём «такт ок: N тегов»
```

## Проверка

```bash
./status.sh          # состояние службы одной командой
```

Код возврата: `0` — работает, `1` — сбой последнего такта, `2` — данные
устарели или база недоступна. Годится для мониторинга и cron.

Подробнее, если нужно:

```bash
# то же число строк, что отдаёт платформа
curl -s -H "X-API-Key: $API_KEY" https://com.geotek.app/api/integration/toir-1c | jq length
docker compose exec postgres psql -U toir -d toir -tAc "SELECT count(*) FROM toir_1c;"

# ровно тот запрос, что пойдёт в 1С, из-под read-only роли
docker compose exec -e PGPASSWORD=$TOIR_1C_PASSWORD postgres \
  psql -U toir_1c -d toir -c "SELECT tag, value, value_text, ts FROM v_toir_1c ORDER BY tag;"

# состояние синхронизации, если что-то не так
docker compose exec postgres psql -U toir -d toir -c "SELECT * FROM sync_state;"
```

## Тесты

```bash
docker compose run --rm --no-deps sync sh -c "pip install -q pytest && python -m pytest tests -q"
```

Покрыты только мэппинг строки и защита от стирания таблицы — см. `CLAUDE.md`.

## Устройство

| Файл | Что делает |
|---|---|
| `sync/config.py` | env → `Settings`, падает на старте, если чего-то нет |
| `sync/api.py` | GET снимка по `X-API-Key`, таймауты, 3 попытки, валидация |
| `sync/db.py` | схема, read-only роль для 1С, UPSERT + удаление лишнего |
| `sync/main.py` | цикл, логи, чистый выход по SIGTERM |
| `sync/status.py` | состояние службы для человека, за ним `./status.sh` |
| `setup.sh` | установка и меню обслуживания: ключ, адрес платформы, реквизиты |
| `install.sh` | установка с нуля: Docker, git clone, запуск `setup.sh` |

Схема БД повторяет `toir.v_toir_1c` платформы колонка в колонку, поэтому запрос
чтения в 1С один и тот же — что к облаку, что к локальной копии.
