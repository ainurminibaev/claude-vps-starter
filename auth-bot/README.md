# auth-bot

Отдельный TG-бот для автоматического OAuth `/login` root-сессии `claude-tg` на VPS.

## Зачем

Бага в claude-code: refreshToken иногда обнуляется при auto-refresh → claude залипает с 401 → бот молчит часами/сутками пока не сделаешь `/login` руками. Раньше делали через guard-cron + waiter-через-pane, но MCP-плагин забирает входящие сообщения и не передаёт в pane когда claude в модалке `/login` → попытки автоматизации проваливались.

Решение — **отдельный TG-бот** со своим токеном (нет конфликта polling с основным MCP).

## Что делает

- В фоне (каждые 60 сек) проверяет `/root/.claude/.credentials.json`
- Если `refreshToken=0` → запускает `/login` в tmux pane `claude-tg`, достаёт URL, шлёт владельцу в TG
- Принимает от владельца:
  - `/start` — приветствие
  - `/login` — ручной запуск
  - `/status` — текущее состояние credentials
  - OAuth-код формата `XXX#YYY` — вставляет в pane, подтверждает `Login successful`, шлёт `✅`
- При expired code → автоматически перегенерирует URL «🔁 держи новую»
- Cooldown 1ч между алертами «снова broken»

## Установка

1. В `@BotFather`: `/newbot` → имя/username → скопировать токен
2. Послать новому боту `/start` (чтобы он узнал твой chat_id)
3. На VPS:
   ```bash
   cd /root/projects/claude-vps-starter/auth-bot
   sudo ./install.sh
   sudo vim /etc/auth-bot/env     # вписать AUTH_BOT_TOKEN и OWNER_CHAT_ID
   sudo systemctl restart auth-bot
   sudo journalctl -u auth-bot -f
   ```

## Логи

- `/var/log/auth-bot.log` — все события (URL отправлен, код принят, ошибки)
- `journalctl -u auth-bot` — systemd-level

## Файлы

- `auth_bot.py` — основной скрипт (Python 3, stdlib only)
- `auth-bot.service` — systemd unit (Restart=always)
- `install.sh` — установщик
- `env.example` — шаблон env-файла (секреты лежат в `/etc/auth-bot/env`, **не в git**)

## Что снести после установки

Старая попытка автоматизации (через polling pane) больше не нужна — auth-bot её полностью заменяет:

```bash
sudo rm /etc/cron.d/credentials-guard
sudo rm /root/.claude/scripts/credentials_guard.sh
sudo rm /root/.claude/scripts/auto_login_waiter.sh
```
