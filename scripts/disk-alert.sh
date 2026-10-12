#!/usr/bin/env bash
# Предупреждение в Telegram, если на / меньше LIMIT_GB свободного места. Раз в сутки, не чаще.
LIMIT_GB=5
STATE=/var/lib/disk-alert.last
free_gb=$(( $(df --output=avail -B1M / | tail -1) / 1024 ))
[ "$free_gb" -ge "$LIMIT_GB" ] && exit 0
[ -f "$STATE" ] && [ $(( $(date +%s) - $(stat -c %Y "$STATE") )) -lt 86400 ] && exit 0
chat=$(python3 -c "import json;print(json.load(open(\"/root/.claude/channels/telegram/access.json\"))[\"allowFrom\"][0])")
/root/.claude/scripts/send_tg.sh "$chat" "⚠️ На сервере ботов осталось ${free_gb} ГБ свободного места (из 96). При нуле боты упадут. Что занимает: du -x -d2 / | sort -h | tail" >/dev/null 2>&1
touch "$STATE"
