#!/usr/bin/env bash
# Еженедельная чистка восстанавливаемого мусора на сервере ботов.
# Трогает только кэши и временные файлы — ни проектов, ни transcript-ов, ни памяти ботов.
#   - кэши uv / pip / npm у всех пользователей (скачаются заново при установке);
#   - /tmp/claude-<uid>/… — рабочие папки прошлых сессий Claude Code (кроме текущих);
#   - docker: build cache и неиспользуемые образы (запущенные контейнеры не трогаем).
set -uo pipefail
LOG=/var/log/disk-cleanup.log
log() { echo "$(date -Iseconds) $*" >> "$LOG"; }

before=$(df --output=avail -B1M / | tail -1)

# 1. Кэши пакетных менеджеров.
#    uv: MCP-серверы, запущенные через uvx, работают ПРЯМО из ~/.cache/uv/archive-v0/<hash> —
#    удалить такую папку = уронить MCP посреди сессии. Поэтому archive-v0 чистим только от
#    окружений, которые не использует ни один живой процесс и не трогали больше суток.
#    npm: ~/.npm/_npx не трогаем (там живут npx-MCP), только _cacache.
in_use=$(for p in /proc/[0-9]*; do
  { readlink "$p/exe"; tr '\0' '\n' < "$p/cmdline"; } 2>/dev/null
done | grep -oE '/[^ ]*/\.cache/uv/archive-v0/[^/ ]+' | sort -u)
for h in /root /home/*; do
  uv="$h/.cache/uv"
  if [ -d "$uv" ]; then
    for sub in "$uv"/wheels-* "$uv"/sdists-* "$uv"/simple-* "$uv"/builds-* "$uv"/git-*; do
      [ -e "$sub" ] && rm -rf "$sub"
    done
    for a in "$uv"/archive-v0/*/; do
      [ -d "$a" ] || continue
      a=${a%/}
      grep -qxF "$a" <<< "$in_use" && continue
      [ -n "$(find "$a" -maxdepth 0 -mtime -7 2>/dev/null)" ] && continue
      rm -rf "$a"
    done
  fi
  [ -d "$h/.cache/pip" ] && rm -rf "${h:?}/.cache/pip"
  [ -d "$h/.npm/_cacache" ] && rm -rf "${h:?}/.npm/_cacache"
done

# 2. Рабочие папки Claude Code прошлых сессий. Текущие определяем по --resume/--session-id
#    живых процессов и не трогаем; остальное — только если не менялось больше суток.
active=$(for p in $(pgrep -x claude); do tr '\0' '\n' < /proc/$p/cmdline 2>/dev/null \
  | grep -A1 -E '^--(resume|session-id)$' | grep -E '^[0-9a-f-]{36}$'; done | sort -u)
for d in /tmp/claude-*/*/*/; do
  [ -d "$d" ] || continue
  sid=$(basename "$d")
  grep -qx "$sid" <<< "$active" && continue
  [ -n "$(find "$d" -mmin -1440 -print -quit 2>/dev/null)" ] && continue
  rm -rf "$d"
done

# 3. Docker.
docker builder prune -af >/dev/null 2>&1
docker image prune -af >/dev/null 2>&1

after=$(df --output=avail -B1M / | tail -1)
log "освобождено $(( (after - before) / 1024 )) ГБ, свободно $(( after / 1024 )) ГБ"
