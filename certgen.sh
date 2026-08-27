#!/usr/bin/env bash
# Самоподписанный сертификат для TLS между 1С и локальным Postgres.
#
# Домена у сервера нет, только IP, поэтому сертификат выписан сам на себя:
# 1С проверяет его по файлу (sslmode=verify-ca), а не по имени. Срок — 10 лет:
# служба стоит в закрытой сети, продлевать его будет некому.
#
# Идемпотентно: если файлы на месте, ничего не делает. Вызывается из setup.sh
# перед стартом контейнеров — без сертификата Postgres с ssl=on не поднимется.
set -euo pipefail
cd "$(dirname "$0")"

CRT=certs/server.crt
KEY=certs/server.key

[ -f "$CRT" ] && [ -f "$KEY" ] && exit 0

command -v openssl >/dev/null 2>&1 || {
    printf 'Ошибка: нет openssl, сертификат не создать. Поставить: apt install -y openssl\n' >&2
    exit 1
}
[ "$(id -u)" -eq 0 ] || {
    printf 'Ошибка: сертификат создаётся от root — иначе Postgres не сможет прочитать ключ.\n' >&2
    exit 1
}

mkdir -p certs
umask 077
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -keyout "$KEY" -out "$CRT" -subj "/CN=compressor-toir" 2>/dev/null

# Postgres читает ключ под своим uid (70 в postgres:16-alpine) и отказывается
# стартовать, если файл доступен кому-то ещё: владелец root, чтение — группе.
chown 0:70 "$KEY" "$CRT"
chmod 640 "$KEY"
chmod 644 "$CRT"
printf 'Сертификат создан: %s (действует 10 лет)\n' "$CRT"
