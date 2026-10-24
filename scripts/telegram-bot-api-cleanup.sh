#!/usr/bin/env bash
# Чистит файлы, которые локальный Bot API сервер (/opt/telegram-bot-api) скачал или принял
# для ботов: плагин сразу копирует их в inbox бота, на сервере они больше не нужны.
# Трогаем только папки с медиа; td.binlog и прочее состояние ботов не трогаем.
set -euo pipefail

DIR=/var/lib/telegram-bot-api
RETENTION_DAYS=2
LOG=/var/log/telegram-bot-api-cleanup.log

[[ -d "$DIR" ]] || exit 0

files=$(find "$DIR" -mindepth 3 -maxdepth 3 -type f -mtime +$RETENTION_DAYS \
  -path "$DIR/*/*/*" -regextype posix-extended \
  -regex "$DIR/[^/]+/(documents|music|videos|voice|photos|video_notes|animations|stickers|thumbnails|temp)/[^/]+" \
  -print)
count=$(printf '%s' "$files" | grep -c . || true)
if [[ "$count" -eq 0 ]]; then
  echo "$(date -Iseconds) нечего чистить, всего $(du -sh "$DIR" | cut -f1)" >> "$LOG"
  exit 0
fi
size=$(printf '%s\n' "$files" | xargs -d '\n' du -ch 2>/dev/null | tail -1 | cut -f1)
printf '%s\n' "$files" | xargs -d '\n' rm -f
echo "$(date -Iseconds) удалено $count файлов ($size), осталось $(du -sh "$DIR" | cut -f1)" >> "$LOG"
