#!/usr/bin/env bash
# Автообновление общего пространства marusya-os для бота claude-tg-marusya.
# Запускается кроном раз в сутки. Логика:
#   нет изменений                       → выход
#   менялись CLAUDE.md / skills / .claude → рестарт сессии (инструкции читаются на старте)
#   прочие .md                          → nudge в pane со списком файлов
set -uo pipefail

REPO=/home/marusya/marusya/marusya-os
SESSION=claude-tg-marusya
BOTUSER=marusya
LOG=/var/log/marusya-sync.log
SPINNER_RE='✻|✽|✶|✢|⏳|Brewing|Brewed|Sketching|Forging|Twisting|Musing|Generating|Spelunking|Cogitating|Compacting|Thinking|Working|Choreographing|Imagining|Flowing|Germinating|Leavening|Noodling|Jitterbugging|Boondoggling'

log() { echo "$(date -Iseconds) $*" >> "$LOG"; }
asbot() { sudo -H -u "$BOTUSER" "$@"; }

before=$(asbot git -C "$REPO" rev-parse HEAD 2>>"$LOG") || { log "FAIL rev-parse"; exit 1; }
asbot git -C "$REPO" pull --ff-only --quiet >>"$LOG" 2>&1 || { log "FAIL pull"; exit 1; }
after=$(asbot git -C "$REPO" rev-parse HEAD)

if [[ "$before" == "$after" ]]; then
  log "no changes ($after)"
  exit 0
fi

changed=$(asbot git -C "$REPO" diff --name-only "$before" "$after")
count=$(wc -l <<< "$changed")
log "updated ${before:0:7}..${after:0:7} ($count files)"

# Критичные файлы — только рестарт заставит перечитать их с нуля
if grep -qE '(^|/)CLAUDE\.md$|^skills/|^\.claude/' <<< "$changed"; then
  log "core files changed -> killing session, watchdog will restart"
  asbot tmux kill-session -t "$SESSION" 2>>"$LOG"
  exit 0
fi

# Иначе — мягкий nudge, но не влезая в идущий ход
pane=$(asbot tmux capture-pane -t "$SESSION" -p 2>/dev/null)
if [[ -z "$pane" ]]; then
  log "no pane, skip nudge"
  exit 0
fi
if grep -qE "$SPINNER_RE" <<< "$pane"; then
  log "session busy, skip nudge"
  exit 0
fi

files=$(head -12 <<< "$changed" | tr '\n' '; ')
[[ $count -gt 12 ]] && files="$files и ещё $((count-12))"
msg="Общее пространство marusya-os обновилось. Изменились файлы: ${files} Перечитай те, что относятся к твоим задачам, прежде чем работать по ним. Отвечать в Telegram на это не нужно."

asbot tmux send-keys -t "$SESSION" -l "$msg"
sleep 1
asbot tmux send-keys -t "$SESSION" Enter
log "nudge sent"
