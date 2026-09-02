#!/usr/bin/env bash
# Удаляет из /tgparser/incoming файлы старше RETENTION_DAYS.
# Каталог наполняется скачанными из Telegram видео и своей ротации не имеет:
# за полтора месяца набегало ~3.4 ГБ и продолжало расти.
set -euo pipefail

DIR=/tgparser/incoming
RETENTION_DAYS=30
LOG=/var/log/tgparser-incoming-cleanup.log

[[ -d "$DIR" ]] || exit 0

count=$(find "$DIR" -type f -mtime +$RETENTION_DAYS | wc -l)
if [[ "$count" -eq 0 ]]; then
  echo "$(date -Iseconds) нечего чистить" >> "$LOG"
  exit 0
fi

size=$(find "$DIR" -type f -mtime +$RETENTION_DAYS -exec du -ch {} + 2>/dev/null | tail -1 | cut -f1)
find "$DIR" -type f -mtime +$RETENTION_DAYS -delete
find "$DIR" -mindepth 1 -type d -empty -delete
echo "$(date -Iseconds) удалено $count файлов ($size), осталось $(du -sh "$DIR" | cut -f1)" >> "$LOG"
