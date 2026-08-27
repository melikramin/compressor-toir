#!/usr/bin/env bash
# Установка compressor-toir одной командой на чистый сервер:
#
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/melikramin/compressor-toir/main/install.sh)"
#
# Ставит Docker, если его нет, забирает проект с GitHub и запускает setup.sh.
set -euo pipefail

REPO=${TOIR_REPO:-https://github.com/melikramin/compressor-toir.git}
DIR=${TOIR_DIR:-/opt/compressor-toir}

if [ -t 1 ]; then
    R=$'\033[31m'; G=$'\033[32m'; B=$'\033[1m'; N=$'\033[0m'
else
    R=""; G=""; B=""; N=""
fi
ok()  { printf '%s✔%s %s\n' "$G" "$N" "$*"; }
err() { printf '%sОшибка:%s %s\n' "$R" "$N" "$*" >&2; exit 1; }

# Скрипт могли запустить через «curl | bash» — тогда stdin занят пайпом и
# setup.sh не сможет ничего спросить. Возвращаем себе терминал, но только
# если он вправду открывается: под cron и в CI /dev/tty есть, а толку нет.
if [ ! -t 0 ] && [ -c /dev/tty ] && (: </dev/tty) 2>/dev/null; then
    exec </dev/tty
fi

printf '\n%scompressor-toir — установка%s\n\n' "$B" "$N"

[ "$(id -u)" = "0" ] || err "нужны права root. Запустите через sudo -i или от root."

if command -v apt-get >/dev/null 2>&1; then
    PKG=apt
elif command -v dnf >/dev/null 2>&1; then
    PKG=dnf
else
    err "поддерживаются Debian/Ubuntu и RHEL/Fedora. Поставьте git и Docker вручную, затем запустите ./setup.sh"
fi

if ! command -v git >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1; then
    echo "Ставлю git и curl..."
    if [ "$PKG" = apt ]; then
        apt-get update -qq && apt-get install -y -qq git curl ca-certificates
    else
        dnf install -y -q git curl ca-certificates
    fi
fi
ok "git и curl на месте"

if ! command -v docker >/dev/null 2>&1; then
    echo "Docker не найден, ставлю с get.docker.com (займёт пару минут)..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable --now docker
    ok "Docker установлен"
else
    ok "Docker уже стоит"
fi
docker compose version >/dev/null 2>&1 || err "нет плагина docker compose (нужен Docker Compose v2)"
docker info >/dev/null 2>&1 || err "демон Docker не отвечает: systemctl status docker"

if [ -d "$DIR/.git" ]; then
    echo "Проект уже есть в $DIR, обновляю..."
    git -C "$DIR" pull --ff-only
    ok "обновлено"
elif [ -e "$DIR" ] && [ -n "$(ls -A "$DIR" 2>/dev/null)" ]; then
    err "каталог $DIR не пуст и это не git-репозиторий. Уберите его или задайте другой: TOIR_DIR=/opt/другой"
else
    echo "Забираю проект в $DIR..."
    git clone -q "$REPO" "$DIR"
    ok "проект скачан"
fi

cd "$DIR"
chmod +x setup.sh status.sh certgen.sh
exec ./setup.sh
