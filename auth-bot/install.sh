#!/bin/bash
# Установка auth-bot из этого каталога. Запускать из corrента (cd auth-bot && sudo ./install.sh).
set -e
cd "$(dirname "$0")"

if [ "$EUID" -ne 0 ]; then
  echo "запусти под sudo/root" >&2
  exit 1
fi

# 1. /etc/auth-bot/env (секрет, не в git)
if [ ! -f /etc/auth-bot/env ]; then
  mkdir -p /etc/auth-bot
  cp env.example /etc/auth-bot/env
  chmod 600 /etc/auth-bot/env
  echo ""
  echo "⚠️  ОТРЕДАКТИРУЙ /etc/auth-bot/env — впиши AUTH_BOT_TOKEN и OWNER_CHAT_ID"
  echo "    После этого: sudo systemctl restart auth-bot"
  echo ""
fi

# 2. Скрипт в /usr/local/bin
install -m 755 auth_bot.py /usr/local/bin/auth-bot.py

# 3. Systemd unit
install -m 644 auth-bot.service /etc/systemd/system/auth-bot.service

# 4. Reload + enable + start (только если env уже заполнен)
systemctl daemon-reload
systemctl enable auth-bot.service

if grep -q PUT_YOUR /etc/auth-bot/env; then
  echo "❗ Не запускаю auth-bot — env не заполнен."
  exit 0
fi

systemctl restart auth-bot.service
sleep 2
systemctl status auth-bot.service --no-pager | head -15
