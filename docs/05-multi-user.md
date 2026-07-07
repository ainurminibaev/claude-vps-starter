# 05. Multi-user — отдельный аккаунт на том же VPS

Настраиваем ещё одного человека (сотрудник, ребёнок, друг) — со своим Claude-инстансом и своим Telegram-ботом. Всё через отдельного Linux-юзера (безопасно, независимо, легко откатить).

## Быстрый старт (одна команда)

**Перед запуском:**

1. Создай бота в `@BotFather` → сохрани токен.
2. Узнай TG-ID владельца через `@userinfobot` — числовой ID, не username.
3. **Владелец должен открыть бота и нажать `/start`** — иначе TG не заведёт chat_id для него, и бот его не увидит (тест: `getChat` вернёт `chat not found`).

**Одна команда развернёт всё:**

```bash
sudo /root/projects/claude-vps-starter/scripts/add-second-user.sh <username> \
  --tg-token <bot_token> \
  --tg-user-id <owner_tg_id> \
  [--extra-allow <id>,<id>]
```

Пример (10-й бот, Ильшат):
```bash
sudo /root/projects/claude-vps-starter/scripts/add-second-user.sh ilshat \
  --tg-token 8839430682:AAG... \
  --tg-user-id 976870658 \
  --extra-allow 105839411
```

Что скрипт делает автоматом:
- Заводит Linux-юзера (пароль заблокирован, всё через `sudo -u`).
- Скелет `.claude/`: `settings.json`, `hooks/stop-autoreply.py`, `scripts/send_tg.sh`.
- `channels/telegram/.env` (chmod 600) + `access.json` с allowlist.
- `.claude.json` с `hasCompletedOnboarding=true, bypassPermissionsModeAccepted=true`.
- Регистрирует в `SESSIONS` watchdog (repo + deployed).
- Регистрирует в `BOTS` auth-bot (repo + deployed), перезапускает `auth-bot.service`.
- Прогоняет verification-чек.

**После скрипта (OAuth):**

1. В Telegram напиши auth-bot команду: `/login <username>` → пришлёт OAuth URL.
2. Открой URL, войди в свой Claude Max, скопируй код.
3. Пришли код обратно auth-bot'у — он вставит в pane, дождётся `Login successful`.
4. Через 30–60с watchdog запустит tmux сессию `claude-tg-<username>` с флагом `--channels plugin:telegram@claude-plugins-official` и пошлёт **first-boot nudge** (иначе клод сидит в welcome screen без активного канала).
5. Владелец пишет боту → бот отвечает.

## Проверки (когда что-то не так)

Ходи по этому списку сверху вниз, останавливайся на первом FAIL:

```bash
# 1. Bot API живой (getMe)
curl -s "https://api.telegram.org/bot<TOKEN>/getMe" | jq '.ok'      # ожидаем: true

# 2. Bun MCP реально ловит long-polling
curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" | jq '.error_code'
# ожидаем: 409 (Conflict: terminated by other getUpdates) — это значит bun-процесс уже долгопуллит

# 3. Chat с владельцем существует
curl -s "https://api.telegram.org/bot<TOKEN>/getChat?chat_id=<owner_id>" | jq '.ok'
# true = ок, false с 'chat not found' = владелец ещё не нажал /start у своего бота

# 4. OAuth credentials свежие
python3 -c "import json,time; d=json.load(open('/home/<u>/.claude/.credentials.json'))['claudeAiOauth']; print(len(d['refreshToken']),'chars,',round((d['expiresAt']/1000-time.time())/3600,1),'h left')"
# ожидаем: refreshToken ≥100 chars, expires >0h

# 5. Bun MCP heartbeat не старше 30 сек
stat -c '%y' /home/<u>/.claude/channels/telegram/bot.heartbeat

# 6. Plugin telegram установлен
jq -e '.plugins["telegram@claude-plugins-official"]' /home/<u>/.claude/plugins/installed_plugins.json

# 7. .claude.json > 20 KB (значит клод хоть раз стартанул полноценно)
ls -la /home/<u>/.claude.json

# 8. Watchdog log — session boot прошёл
sudo tail -20 /var/log/claude-tg-watchdog.log | grep <username>
# ищи: `bootstrap new session ...` → `confirmed workspace trust` → `first-boot nudge sent`

# 9. tmux session жива
sudo -u <u> tmux ls | grep claude-tg-<u>
```

## Симптомы → решения

| Симптом | Причина | Решение |
|---------|---------|---------|
| `getChat` → `chat not found` | Владелец не нажимал `/start` у своего бота | Пусть откроет бота в TG и нажмёт `/start` |
| В pane заглушка `Try "how does <filepath> work?"` и никаких TG-событий | Клод стартовал **без** `--channels plugin:telegram@claude-plugins-official` — канал не активен | Kill session (`sudo -u <u> tmux kill-session -t claude-tg-<u>`) — watchdog поднимет правильно с first-boot nudge |
| Бот показывает `typing…` и молчит | MCP получает, но клод не подписан на канал | То же — kill session |
| Клод ответил один раз и потом молчит | Обычно stop-hook не может писать `send_tg.sh` (отсутствует под юзером) | Проверь `/home/<u>/.claude/scripts/send_tg.sh` — `add-second-user.sh` v2 копирует автоматом |
| `refreshToken` короткий или отсутствует | Credentials протухли/побились | В auth-bot: `/login <username>`, повтори OAuth flow |
| Watchdog log спамит `session missing → bootstrap` каждую минуту | Клод крашится сразу после запуска | Смотри `docs/07-troubleshooting.md`; временно: `sudo -u <u> settings.json` без `hooks` |

## Что делать НЕ надо

- Не давай юзеру пароль — все действия через `sudo -u`.
- Не давай полный sudo (root). Только узкие sudoers-правила (см. Шаг 2 ниже).
- Не редактируй `/root/claude-tg-watchdog.sh` вручную. Меняй `scripts/claude-tg-watchdog.sh` в репе, потом `add-second-user.sh` (или `install.sh`) сам скопирует.
- Не коммить `.env` (боевой токен), `.credentials.json`, `access.json` в git.

---

# Детальный режим (fallback, если что-то делается вручную)

Если хочется пройтись по шагам без автоскрипта — вот они.

## Глоссарий

В примерах ниже используется placeholder `<USER>`. Замени на реальное имя (например, `ilshat`, `colleague`).

## Что общее на VPS

- Бинари: Docker, Bun, Claude CLI (`/usr/bin/claude`) ставятся глобально один раз.
- Whisper-контейнер один (`127.0.0.1:9000`).
- Watchdog один (`/root/claude-tg-watchdog.sh`), запускается из root-cron, умеет управлять tmux любого юзера через `sudo -u`.

## Шаг 1. Создать Linux-юзера

```bash
sudo useradd -m -s /bin/bash <USER>
sudo passwd -l <USER>        # заблокировать пароль — все действия через sudo -u
```

## Шаг 2. Узкие sudo-разрешения

Только то, что реально нужно. **Не давай полный sudo.**

### Nginx (полный контроль над сервисом)
```
<USER> ALL=(root) NOPASSWD: /bin/systemctl reload nginx, /bin/systemctl restart nginx, /usr/sbin/nginx -t
```

### apt-get (юзер сам ставит пакеты)
```
<USER> ALL=(root) NOPASSWD: /usr/bin/apt-get install *, /usr/bin/apt-get update
```

### Web-deploy (если юзер публикует сайты под `/var/www` через nginx)
```
Cmnd_Alias WEBDEPLOY_<USER> = \
    /bin/mkdir -p /var/www/*, \
    /bin/cp -r * /var/www/*, \
    /bin/rm -rf /var/www/<USER>/*, \
    /bin/chown -R www-data\:www-data /var/www/*

<USER> ALL=(root) NOPASSWD: WEBDEPLOY_<USER>
```
+ добавь соответствующие `Bash(sudo cp ...)` в `settings.json > permissions.allow` юзера.

### Если нужны другие команды
Добавляй по одной, всегда с полным путём (`/usr/bin/...`) и с ограниченным аргументами через `*`.

## Шаг 3. Скелет `.claude/`

Ровно тот же скелет, что делает `add-second-user.sh` (см. секцию Быстрый старт для полного файла). Ключевые файлы:

- `~/.claude/settings.json` — auto mode, telegram plugin, Stop hook.
- `~/.claude/hooks/stop-autoreply.py` — из репо `hooks/stop-autoreply.py`.
- `~/.claude/scripts/send_tg.sh` — из репо `scripts/send_tg.sh` (**обязательно**! иначе stop-hook не отправит fallback-сообщения).
  - После копирования нужно поправить hardcoded путь `.env` в `send_tg.sh`:
    ```bash
    sudo sed -i "s|/root/.claude/channels/telegram/.env|/home/<USER>/.claude/channels/telegram/.env|" /home/<USER>/.claude/scripts/send_tg.sh
    ```
- `~/.claude/channels/telegram/.env` (chmod 600) — `TELEGRAM_BOT_TOKEN=...`.
- `~/.claude/channels/telegram/access.json` — `dmPolicy: allowlist, allowFrom: [<owner_id>, <extras>...]`.
- `~/.claude.json` — `{"hasCompletedOnboarding": true, "bypassPermissionsModeAccepted": true}`.

## Шаг 4. Telegram-бот

Уже описано в «Быстром старте». Токен в `.env`, allowlist в `access.json`.

## Шаг 5. Установить telegram-плагин

Плагин ставится **автоматически** при первом запуске Claude, когда `settings.json > enabledPlugins > telegram@claude-plugins-official = true` и есть `extraKnownMarketplaces > claude-plugins-official > source > github`. Это уже в шаблоне.

## Шаг 6. Авторизовать Claude (OAuth headless)

Работает **через auth-bot** — не запускай `claude /login` руками:

1. В Telegram напиши auth-bot: `/login <USER>` → пришлёт OAuth URL.
2. Открой URL, войди в свой Claude Max/Pro, скопируй код.
3. Пришли код обратно auth-bot — он вставит в pane и дождётся `Login successful`.

Если auth-bot почему-то недоступен (например его сервис в даун):
```bash
# запускаем клод под юзером в tmux, отправляем /login, вытаскиваем URL:
sudo -u <USER> tmux new-session -d -s claude-tg-<USER> "claude --dangerously-skip-permissions"
sleep 45
sudo -u <USER> tmux send-keys -t claude-tg-<USER> "/login" Enter
sleep 8
sudo -u <USER> tmux send-keys -t claude-tg-<USER> Enter   # выбор варианта 1
sleep 6
sudo -u <USER> tmux capture-pane -t claude-tg-<USER> -p -S -200 | grep -A2 "Browser didn't open"
# скопируй URL (склеив разбитые строки), пройди OAuth в браузере, получи код
sudo -u <USER> tmux send-keys -t claude-tg-<USER> "<CODE>" Enter
```

## Шаг 7. Отметить onboarding выполненным

Уже в `.claude.json` — `add-second-user.sh` делает.

## Шаг 8. Добавить в watchdog

`add-second-user.sh` делает автоматом. Вручную — открой `/root/projects/claude-vps-starter/scripts/claude-tg-watchdog.sh`, найди массив `SESSIONS`, добавь строку:
```
SESSIONS=(
  "claude-tg|root|/root|<uuid>"
  ...
  "claude-tg-<USER>|<USER>|/home/<USER>|<new-uuid>"   # ← новая
)
```
и скопируй в `/root/claude-tg-watchdog.sh`.

## Шаг 9. Проверить

Список проверок — см. «Проверки» в «Быстром старте» выше.

### ВАЖНО: bun запущен с heartbeat-preload

Pattern 7 watchdog требует, чтобы bun запускался с `--preload /usr/local/share/telegram-mcp-heartbeat.ts`. Иначе heartbeat-файл не создаётся и watchdog слеп к зависшему bun.

```bash
ps -eo user,cmd | grep <USER> | grep bun.real                  # должен быть --preload
stat -c '%y' /home/<USER>/.claude/channels/telegram/bot.heartbeat   # < 30 сек
```

Если `--preload` **нет**: `sudo pkill -9 -u <USER> -f bun.real` — при рестарте bun подхватит новую команду.

## Шаг 10. Bash allow для web-deploy (опционально)

### a) Расширить sudoers (см. Шаг 2 → блок «Web-deploy»)

### b) Расширить bash-allow в `settings.json` юзера
```json
{
  "permissions": {
    "allow": [
      "Bash(sudo cp -r /home/<USER>/build/* /var/www/*)",
      "Bash(sudo mkdir -p /var/www/*)",
      "Bash(sudo chown -R www-data:www-data /var/www/*)",
      "Bash(sudo /usr/sbin/nginx -t)",
      "Bash(sudo /bin/systemctl reload nginx)"
    ]
  }
}
```

### Что важно понимать

Claude Code sandbox блокирует cross-user действия, self-modification, и запуск команд под другими юзерами через `sudo` — это фича безопасности. Разрешения даются только через явный `permissions.allow` **и** соответствующие sudoers-правила.

## Шаг 11. Добавить других людей в allowlist

Открой `/home/<USER>/.claude/channels/telegram/access.json`, добавь ID в `allowFrom`. Клод подхватит при следующем tool_call MCP (не нужно перезапускать).

## Безопасность: почему sandbox блокирует cross-user / self-modification

Смотри `docs/07-troubleshooting.md`.

## Чек-лист готовности

- [ ] `id <USER>` показывает пользователя
- [ ] `sudo -u <USER> claude --version` работает
- [ ] `/home/<USER>/.credentials.json` есть, `refreshToken` длинный
- [ ] `/home/<USER>/.claude/plugins/installed_plugins.json` есть
- [ ] `/home/<USER>/.claude.json` `hasCompletedOnboarding: true`
- [ ] watchdog SESSIONS содержит новую запись
- [ ] auth-bot `BOTS` содержит юзера + `systemctl restart auth-bot` прошёл
- [ ] tmux session `claude-tg-<USER>` живёт
- [ ] `ps -eo cmd | grep bun.real` для юзера содержит `--preload telegram-mcp-heartbeat.ts`
- [ ] `~/.claude/channels/telegram/bot.heartbeat` существует и mtime < 30s
- [ ] Первый nudge отработал (в watchdog log: `first-boot nudge sent`)
- [ ] Telegram-бот отвечает на сообщение от owner

## Удалить юзера обратно

```bash
sudo systemctl stop auth-bot
# 1. Kill tmux + processes
sudo -u <USER> tmux kill-server 2>/dev/null
sudo pkill -9 -u <USER>
# 2. Убрать из watchdog SESSIONS (и deployed) и auth-bot BOTS вручную
sudo vim /root/projects/claude-vps-starter/scripts/claude-tg-watchdog.sh
sudo vim /root/projects/claude-vps-starter/auth-bot/auth_bot.py
sudo cp /root/projects/claude-vps-starter/scripts/claude-tg-watchdog.sh /root/claude-tg-watchdog.sh
sudo cp /root/projects/claude-vps-starter/auth-bot/auth_bot.py /usr/local/bin/auth-bot.py
# 3. Удалить юзера (и всю его домашку)
sudo userdel -r <USER>
sudo systemctl restart auth-bot
```
