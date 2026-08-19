#!/usr/bin/env bash
# Установка и управление службой compressor-toir.
#
# Первый запуск проводит установку: спрашивает ключ, генерирует пароли,
# поднимает контейнеры и показывает реквизиты для интегратора 1С.
# Дальнейшие запуски открывают меню: состояние, реквизиты, смена ключа и
# адреса платформы.
set -euo pipefail
cd "$(dirname "$0")"

if [ -t 1 ]; then
    R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'; B=$'\033[1m'; N=$'\033[0m'
else
    R=""; G=""; Y=""; B=""; N=""
fi

say()  { printf '%s\n' "$*"; }
# Кириллица в UTF-8 занимает два байта, а printf %-28s считает байты — колонки
# от этого разъезжаются. Считаем длину в символах и добиваем пробелами сами.
row() {
    local label=$1 value=$2 pad
    pad=$(LC_ALL=C.UTF-8 bash -c 'printf %s "$((28 - ${#1}))"' _ "$label")
    [ "$pad" -lt 1 ] && pad=1
    printf '  %s%*s%s\n' "$label" "$pad" "" "$value"
}
head_() { printf '\n%s%s%s\n\n' "$B" "$*" "$N"; }
err()  { printf '%sОшибка:%s %s\n' "$R" "$N" "$*" >&2; }
ok()   { printf '%s✔%s %s\n' "$G" "$N" "$*"; }
warn() { printf '%s!%s %s\n' "$Y" "$N" "$*"; }

ask() {  # ask ПЕРЕМЕННАЯ "вопрос" "значение по умолчанию"
    local __var=$1 prompt=$2 def=${3:-} answer
    # Оборвался ввод (Ctrl-D, запуск не из терминала) — выходим, а не крутим
    # меню на значении по умолчанию.
    if [ -n "$def" ]; then
        read -r -p "$prompt [$def]: " answer || { printf '\n'; exit 0; }
        answer=${answer:-$def}
    else
        while :; do
            read -r -p "$prompt: " answer || { printf '\n'; exit 0; }
            [ -n "$answer" ] && break
            err "нужно что-то ввести"
        done
    fi
    printf -v "$__var" '%s' "$answer"
}

# Пароль только из букв и цифр: он попадает в строку подключения Postgres и в
# настройки 1С, где спецсимволы приходится экранировать.
genpass() { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 24; }

get_env() { [ -f .env ] && sed -n "s/^$1=//p" .env | head -1 || true; }

set_env() {  # set_env КЛЮЧ ЗНАЧЕНИЕ
    local tmp; tmp=$(mktemp)
    if grep -q "^$1=" .env 2>/dev/null; then
        K=$1 V=$2 awk 'BEGIN{k=ENVIRON["K"];v=ENVIRON["V"]}
                       index($0,k"=")==1 {print k"="v; next} {print}' .env >"$tmp"
    else
        cp .env "$tmp"; printf '%s=%s\n' "$1" "$2" >>"$tmp"
    fi
    mv "$tmp" .env; chmod 600 .env
}

require_docker() {
    command -v docker >/dev/null 2>&1 || { err "Docker не установлен. Поставить: curl -fsSL https://get.docker.com | sh"; exit 1; }
    docker compose version >/dev/null 2>&1 || { err "Нет плагина docker compose (нужен Docker Compose v2)."; exit 1; }
    docker info >/dev/null 2>&1 || { err "Нет доступа к Docker. Запускайте от root или добавьте пользователя в группу docker."; exit 1; }
}

# Адрес платформы нужен до /api, но из документации обычно копируют весь URL
# эндпоинта — без этого запрос уходит в /integration/toir-1c дважды и ловит 404.
norm_base() {  # norm_base АДРЕС
    local b
    b=$(printf '%s' "$1" | tr -d '[:space:]')
    b=${b%/}; b=${b%/integration/toir-1c}; b=${b%/}
    case "$b" in http://*|https://*) ;; *) b="https://$b" ;; esac
    case "${b#*://}" in */*) ;; *) b="$b/api" ;; esac
    printf '%s' "$b"
}

# Проверяем ключ до записи в .env: опечатка не должна ронять рабочую службу.
check_key() {  # check_key БАЗА КЛЮЧ
    local code
    # curl сам печатает 000, когда соединения не было.
    code=$(curl -s -o /dev/null -m 30 -w '%{http_code}' -H "X-API-Key: $2" "$1/integration/toir-1c" 2>/dev/null) || true
    [ -n "$code" ] || code=000
    case "$code" in
        200) ok "платформа ответила, ключ принят"; return 0 ;;
        401|403) err "платформа отвергла ключ (HTTP $code)"; return 1 ;;
        000) err "нет связи с $1 — проверьте интернет и адрес"; return 1 ;;
        404) err "на $1 нет эндпоинта интеграции — проверьте адрес платформы"; return 1 ;;
        *) err "платформа ответила HTTP $code"; return 1 ;;
    esac
}

wait_first_cycle() {
    printf 'Жду первую синхронизацию'
    local i
    for i in $(seq 1 30); do
        if docker compose exec -T sync python status.py >/dev/null 2>&1; then
            printf '\n'; ok "данные получены"; return 0
        fi
        printf '.'; sleep 5
    done
    printf '\n'; warn "за 2.5 минуты данные не пришли — смотрите: docker compose logs sync"
    return 1
}

show_credentials() {
    local host port
    host=$(get_env PG_BIND); port=$(get_env PG_PORT)
    [ "$host" = "0.0.0.0" ] && host="IP этого сервера"
    head_ "Данные для интегратора 1С:ТОиР"
    row "СУБД" "PostgreSQL"
    row "Сервер" "$host"
    row "Порт" "$port"
    row "База" "$(get_env POSTGRES_DB)"
    row "Пользователь" "$(get_env TOIR_1C_USER)"
    row "Пароль" "$B$(get_env TOIR_1C_PASSWORD)$N"
    row "Период получения данных" "5 минут"
    say ""
    say "  Запрос чтения:"
    say "    SELECT tag, value, value_text, ts FROM v_toir_1c;"
    say ""
    warn "Пароль показан только здесь. Передайте его интегратору и не публикуйте."
}

install() {
    head_ "Установка compressor-toir"
    require_docker
    ok "Docker на месте"

    if [ -f .env ]; then
        warn "Файл .env уже есть — установка перезапишет пароли, и 1С придётся перенастраивать."
        local yn; ask yn "Перезаписать? (yes/no)" "no"
        [ "$yn" = "yes" ] || { say "Оставляю как есть."; return 1; }
        cp .env ".env.backup.$(date +%Y%m%d%H%M%S)"
        ok "старый .env сохранён рядом"
    fi

    local base key bind port
    say ""
    ask base "Адрес платформы" "https://com.geotek.app/api"
    base=$(norm_base "$base")
    say "Адрес: $base"
    while :; do
        ask key "API-ключ (выдаём мы)"
        check_key "$base" "$key" && break
        local yn; ask yn "Повторить ввод? (yes/no)" "yes"
        [ "$yn" = "yes" ] || return 1
    done

    say ""
    say "Откуда будет подключаться 1С:ТОиР?"
    say "  1) с других машин — из сети или через интернет (доступ с любого адреса)"
    say "  2) только с этого же сервера"
    local choice; ask choice "Выбор" "1"
    if [ "$choice" = "2" ]; then
        bind=127.0.0.1
    else
        bind=0.0.0.0
        warn "Порт Postgres будет открыт для всех адресов."
        say  "  Защита — только пароль, и он идёт по сети без шифрования."
        say  "  Если у 1С постоянный IP, сузьте доступ:"
        say  "    ufw allow OpenSSH && ufw allow from IP_1C to any port PORT proto tcp && ufw enable"
    fi

    while :; do
        ask port "Порт Postgres" "5432"
        if ss -tln 2>/dev/null | grep -q ":$port "; then
            warn "порт $port уже занят на этом сервере"
        else
            break
        fi
    done

    umask 077
    cat > .env <<ENV
# Создан setup.sh $(date '+%d.%m.%Y %H:%M')

# --- Источник: платформа Compressor ---
API_BASE=$base
API_KEY=$key
SYNC_INTERVAL=300

# --- Локальный Postgres ---
POSTGRES_USER=toir
POSTGRES_PASSWORD=$(genpass)
POSTGRES_DB=toir
PG_BIND=$bind
PG_PORT=$port

# --- Read-only роль, под которой подключается 1С ---
TOIR_1C_USER=toir_1c
TOIR_1C_PASSWORD=$(genpass)
ENV
    chmod 600 .env
    ok "пароли сгенерированы, .env создан"

    head_ "Запуск"
    docker compose up -d --build
    wait_first_cycle || true
    ./status.sh || true
    show_credentials
    say ""
    say "Состояние в любой момент: ${B}./status.sh${N}   Меню: ${B}./setup.sh${N}"
}

change_key() {
    head_ "Смена API-ключа"
    local base key; base=$(get_env API_BASE)
    say "Текущий адрес платформы: $base"
    ask key "Новый API-ключ"
    check_key "$base" "$key" || { warn "ключ не записан, оставил прежний"; return; }
    set_env API_KEY "$key"
    docker compose up -d sync >/dev/null
    ok "ключ записан, служба перезапущена"
    wait_first_cycle || true
}

change_base() {
    head_ "Смена адреса платформы"
    local base key; key=$(get_env API_KEY)
    say "Текущий: $(get_env API_BASE)"
    ask base "Новый адрес" "https://com.geotek.app/api"
    base=$(norm_base "$base")
    say "Адрес: $base"
    check_key "$base" "$key" || { warn "адрес не записан, оставил прежний"; return; }
    set_env API_BASE "$base"
    docker compose up -d sync >/dev/null
    ok "адрес записан, служба перезапущена"
    wait_first_cycle || true
}

uninstall() {
    head_ "Удаление службы"
    warn "Удалятся контейнеры, том с базой и собранный образ — данные не вернуть."
    say  "1С:ТОиР после этого читать будет нечего."
    local yn dir; ask yn "Удалить? (yes/no)" "no"
    [ "$yn" = "yes" ] || { say "Отмена."; return; }

    docker compose down -v --rmi local || true
    ok "контейнеры, том с данными и образ удалены"

    ask yn "Удалить .env с паролями? (yes/no)" "yes"
    if [ "$yn" = "yes" ]; then
        rm -f .env .env.backup.*
        ok ".env удалён"
    fi

    dir=$(pwd)
    if [ -f "$dir/setup.sh" ] && [ "$dir" != "/" ]; then
        ask yn "Удалить каталог проекта $dir? (yes/no)" "no"
        if [ "$yn" = "yes" ]; then
            # exec: дальше читать скрипт неоткуда — своего файла у нас не будет.
            exec bash -c 'cd / && rm -rf -- "$1" && printf "Готово, каталог %s удалён.\n" "$1"' _ "$dir"
        fi
    fi
    ok "Готово. Каталог оставлен: $dir"
    exit 0
}

menu() {
    while :; do
        head_ "compressor-toir"
        say "  1) Состояние службы"
        say "  2) Данные для подключения 1С (с паролем)"
        say "  3) Сменить API-ключ"
        say "  4) Сменить адрес платформы"
        say "  5) Перезапустить службу"
        say "  6) Последние строки журнала"
        say "  7) Переустановить с нуля"
        say "  8) Удалить службу с сервера"
        say "  0) Выход"
        say ""
        local choice; ask choice "Выбор" "1"
        case "$choice" in
            1) say ""; ./status.sh || true ;;
            2) show_credentials ;;
            3) change_key ;;
            4) change_base ;;
            5) docker compose up -d >/dev/null && ok "перезапущено"; wait_first_cycle || true ;;
            6) say ""; docker compose logs --tail 30 sync ;;
            7) install || true ;;
            8) uninstall ;;
            0) exit 0 ;;
            *) err "нет такого пункта" ;;
        esac
    done
}

require_docker
if [ -f .env ]; then menu; else install; fi
