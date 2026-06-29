#!/bin/bash
# Usage: send_tg.sh <chat_id> <text>
# URL-encode-ит text чтобы спецсимволы (& и др) не ломали запрос.
set -e
chat_id="$1"
shift
text="$*"
. /root/.claude/channels/telegram/.env
curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${chat_id}" \
  --data-urlencode "text=${text}" \
  --data-urlencode "disable_web_page_preview=true" \
  > /tmp/send_tg.last.log
