#!/usr/bin/env bash
# Безопасный перезапуск одного бота: bot-restart.sh <user> [макс. ожидание тишины, сек]
#
# 1. Ждёт тишины: бот не в ходе (нет «esc to interrupt») и transcript не менялся QUIET сек —
#    иначе плагин может забрать сообщение у Telegram и умереть, не успев отдать его боту.
# 2. Мягко гасит: закрывает tmux-сессию → claude завершается → у плагина закрывается stdin,
#    он сам останавливает поллинг и подтверждает offset. kill -9 — только остаткам.
# 3. Поднимает через watchdog (--resume, патчи плагина накладываются там же).
#
# pkill — строго с -u. Паттерны не содержат «claude --», чтобы при перезапуске root
# не убить собственную ssh-сессию, в командной строке которой это слово есть.
set -uo pipefail
u="$1"; MAX_WAIT="${2:-600}"; QUIET=30
[ "$u" = root ] && { s=claude-tg; run=""; } || { s=claude-tg-$u; run="sudo -u $u"; }
h=$(getent passwd "$u" | cut -d: -f6)

waited=0
while :; do
  busy=$($run tmux capture-pane -p -t "$s" 2>/dev/null | tail -3 | grep -c "esc to interrupt")
  f=$(ls -t "$h"/.claude/projects/*/*.jsonl 2>/dev/null | head -1)
  idle=$(( $(date +%s) - $(stat -c %Y "$f" 2>/dev/null || echo 0) ))
  [ "$busy" = 0 ] && [ "$idle" -ge "$QUIET" ] && break
  [ "$waited" -ge "$MAX_WAIT" ] && { echo "$u: не дождался тишины за ${MAX_WAIT}с — пропускаю"; exit 1; }
  sleep 5; waited=$((waited + 5))
done

$run tmux kill-session -t "$s" 2>/dev/null
for _ in $(seq 1 10); do
  pgrep -u "$u" -x claude >/dev/null || pgrep -u "$u" -f "bun.real.*server.ts" >/dev/null || break
  sleep 1
done
pkill -9 -u "$u" -x claude 2>/dev/null
pkill -9 -u "$u" -f "bun.real.*server.ts" 2>/dev/null
sleep 2

/root/claude-tg-watchdog.sh >/dev/null 2>&1
for _ in $(seq 1 30); do
  p=$(pgrep -u "$u" -x claude | head -1)
  b=$(pgrep -u "$u" -f "bun.real.*server.ts" | head -1)
  [ -n "$p" ] && [ -n "$b" ] && break
  sleep 3
done
flags=$(tr '\0' '\n' < /proc/"$p"/cmdline 2>/dev/null | grep -A1 -E '^--(resume|model)$' | tr '\n' ' ')
patch=$(grep -o 'group-autojoin v[0-9]*' "$(readlink -f /proc/"$b"/cwd 2>/dev/null)/server.ts" 2>/dev/null)
echo "$u: $([ -n "$p" ] && echo ПОДНЯТ || echo НЕ_ПОДНЯЛСЯ) (ждал тишины ${waited}с) $flags патч:${patch:-нет}"
