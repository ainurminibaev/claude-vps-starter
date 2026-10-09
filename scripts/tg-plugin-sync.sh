#!/usr/bin/env bash
# Обновление telegram-плагина у ботов + наложение патчей vps-starter.
#
#   tg-plugin-sync.sh                 — полный прогон по всем ботам: claude plugin update,
#                                       патч, отчёт в Telegram если что-то изменилось/сломалось
#   tg-plugin-sync.sh --patch <user>  — только наложить патч одному боту (зовёт watchdog
#                                       перед стартом сессии; быстро, без сети)
#
# Новая версия плагина и новый патч включаются после рестарта сессии бота.
set -uo pipefail

PATCH_DIR=/root/projects/claude-vps-starter/plugin-patches/telegram
PLUGIN=telegram@claude-plugins-official
LOG=/var/log/tg-plugin-sync.log
# Кому слать отчёт: TG_NOTIFY_CHAT или первый id из allowlist главного (root) бота.
NOTIFY_CHAT="${TG_NOTIFY_CHAT:-$(python3 -c "import json;print(json.load(open('/root/.claude/channels/telegram/access.json'))['allowFrom'][0])" 2>/dev/null)}"
SEND=/root/.claude/scripts/send_tg.sh

# Список ботов берём из watchdog — единственный источник правды.
users() {
  grep -oE '^\s*"claude-tg[^|]*\|[^|]+\|' /root/claude-tg-watchdog.sh | cut -d'|' -f2
}

log() { echo "$(date -Iseconds) $*" >> "$LOG"; }

home_of() { getent passwd "$1" | cut -d: -f6; }

plugin_version() {
  python3 - "$(home_of "$1")/.claude/plugins/installed_plugins.json" <<'PY' 2>/dev/null
import json, sys
d = json.load(open(sys.argv[1]))
e = d["plugins"]["telegram@claude-plugins-official"]
print((e[0] if isinstance(e, list) else e).get("version", "?"))
PY
}

# Накладывает патч на все версии плагина в кэше пользователя. Печатает FAIL-строки.
patch_user() {
  local u="$1" h rc=0
  h=$(home_of "$u")
  for f in "$h"/.claude/plugins/cache/claude-plugins-official/telegram/*/server.ts; do
    [ -f "$f" ] || continue
    out=$(python3 "$PATCH_DIR/apply_patch.py" "$f" 2>&1)
    case "$out" in
      PATCHED*) log "$u: $out" ;;
      OK*) ;;
      *) log "$u: $out"; echo "$u: $out"; rc=1 ;;
    esac
  done
  return $rc
}

if [ "${1:-}" = "--patch" ]; then
  patch_user "$2" >/dev/null
  exit 0
fi

updated=()
failed=()
for u in $(users); do
  before=$(plugin_version "$u")
  sudo -H -u "$u" bash -lc "cd ~ && timeout 180 claude plugin update $PLUGIN" >/dev/null 2>&1
  after=$(plugin_version "$u")
  if [ "$before" != "$after" ]; then
    updated+=("$u: $before → $after")
    log "$u: плагин $before → $after"
  fi
  if ! fails=$(patch_user "$u"); then
    failed+=("$fails")
  fi
done

[ ${#updated[@]} -eq 0 ] && [ ${#failed[@]} -eq 0 ] && { log "без изменений"; exit 0; }

msg="Telegram-плагин ботов (tg-plugin-sync)"
if [ ${#updated[@]} -gt 0 ]; then
  msg+=$'\n\nОбновлён (включится после рестарта бота):'
  for x in "${updated[@]}"; do msg+=$'\n'"- $x"; done
fi
if [ ${#failed[@]} -gt 0 ]; then
  msg+=$'\n\n⚠️ Патч НЕ встал — автоподключение групп/каналов у этих ботов выключено, остальное работает:'
  for x in "${failed[@]}"; do msg+=$'\n'"- $x"; done
  msg+=$'\nНужно адаптировать plugin-patches/telegram/apply_patch.py под новую версию.'
fi
[ -x "$SEND" ] && "$SEND" "$NOTIFY_CHAT" "$msg" >/dev/null 2>&1
log "отчёт отправлен"
