#!/usr/bin/env bash
# Еженедельное обновление Claude Code CLI для всех ботов.
# Запущенные сессии не трогает: новая версия (и новый Opus по алиасу --model opus)
# подхватится при следующем рестарте сессии через watchdog.
# Откат: npm i -g /root/backups/anthropic-ai-claude-code-<версия>.tgz
set -uo pipefail
LOG=/var/log/claude-cli-update.log
BACKUP_DIR=/root/backups

log() { echo "$(date -Iseconds) $*" >> "$LOG"; }

current=$(claude --version 2>/dev/null | awk '{print $1}')
latest=$(npm view @anthropic-ai/claude-code version 2>/dev/null)

if [ -z "$latest" ]; then log "npm недоступен, пропуск"; exit 0; fi
if [ "$current" = "$latest" ]; then log "актуально: $current"; exit 0; fi

# Сохраняем текущую версию для отката
mkdir -p "$BACKUP_DIR"
( cd "$BACKUP_DIR" && npm pack "@anthropic-ai/claude-code@$current" >/dev/null 2>&1 )

if npm i -g "@anthropic-ai/claude-code@$latest" >/dev/null 2>&1; then
  log "обновлено: $current → $(claude --version 2>/dev/null | awk '{print $1}')"
else
  log "ОШИБКА обновления $current → $latest, откатываю"
  npm i -g "$BACKUP_DIR/anthropic-ai-claude-code-$current.tgz" >/dev/null 2>&1
fi

# Оставляем три последних бэкапа
ls -t "$BACKUP_DIR"/anthropic-ai-claude-code-*.tgz 2>/dev/null | tail -n +4 | xargs -r rm -f
