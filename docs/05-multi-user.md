# 05. Multi-user — отдельный аккаунт на том же VPS

Эта инструкция — как добавить новый изолированный Claude-агент (отдельный Linux-юзер + отдельный Telegram-бот + отдельная память) на ту же машину, где уже работает основной (root) агент.

Проверено на сетапе rafka (2026-04-29). Обходит все грабли, на которые наступили в первый раз.

## Зачем отдельный Linux-юзер

- **Изоляция памяти.** `~/.claude/projects/.../memory/` у каждого своя. Контексты не смешиваются.
- **Изоляция кода.** Разные `$HOME`, разные права, файлы не пересекаются.
- **Изоляция MCP-стейта.** У `plugin:telegram` своя `access.json`, свой `inbox/`.
- **Отдельный биллинг.** Свой Claude-аккаунт (subscription или API key) — расход видно отдельно.
- **Безопасность.** Поломка одного агента не компрометирует другой.

## Что общее на VPS

- Бинари: Docker, Bun, Claude CLI (`/usr/bin/claude`) ставятся глобально один раз.
- Whisper-контейнер один (`127.0.0.1:9000`).
- Watchdog один (`/root/claude-tg-watchdog.sh`) запускается из root-cron, умеет управлять tmux любого юзера через `sudo -u`.

## Глоссарий

В примерах ниже используется placeholder `<USER>`. Замени на реальное имя нового юзера (например, `rafka`, `colleague`).

---

## Шаг 1. Создать Linux-юзера

```bash
sudo useradd -m -s /bin/bash <USER>
sudo passwd -l <USER>           # пароль заблокирован
sudo usermod -aG docker <USER>  # доступ к Docker без sudo
id <USER>                        # проверка
```

`passwd -l` блокирует логин по паролю. Если новый юзер не должен заходить по SSH — этого достаточно. Если нужен SSH — добавь его публичный ключ в `/home/<USER>/.ssh/authorized_keys`.

## Шаг 2. Узкие sudo-разрешения

Только то, что реально нужно. **Не давай полный sudo.**

### Nginx (полный контроль над сервисом)

```bash
sudo tee /etc/sudoers.d/<USER>-nginx > /dev/null <<'EOF'
<USER> ALL=(root) NOPASSWD: /usr/sbin/nginx, /bin/systemctl reload nginx, /bin/systemctl restart nginx, /bin/systemctl start nginx, /bin/systemctl stop nginx, /bin/systemctl status nginx, /usr/bin/tee /etc/nginx/*
EOF
sudo chmod 440 /etc/sudoers.d/<USER>-nginx
sudo visudo -c -f /etc/sudoers.d/<USER>-nginx   # должно быть "parsed OK"
```

### apt-get (юзер сам ставит пакеты)

```bash
sudo tee /etc/sudoers.d/<USER>-apt > /dev/null <<'EOF'
<USER> ALL=(root) NOPASSWD: /usr/bin/apt, /usr/bin/apt-get, /usr/bin/dpkg
EOF
sudo chmod 440 /etc/sudoers.d/<USER>-apt
sudo visudo -c -f /etc/sudoers.d/<USER>-apt
```

### Web-deploy (если юзер будет публиковать сайты под /var/www через nginx)

Опциональный набор. Даёт сужённое NOPASSWD-право на стандартные операции деплоя:
cp/mkdir/chown/chmod/rm в /var/www/, ln в /etc/nginx/sites-enabled/, certbot.

```bash
sudo tee /etc/sudoers.d/<USER>-webdeploy > /dev/null <<'EOF'
<USER> ALL=(root) NOPASSWD: /bin/cp /tmp/* /var/www/*, /bin/cp -r /tmp/* /var/www/*, /bin/cp /tmp/* /var/www/*/*, /bin/mkdir -p /var/www/*, /bin/chown -R <USER>\:<USER> /var/www/*, /bin/chmod -R 755 /var/www/*, /bin/chmod 755 /var/www/*, /bin/rm -rf /var/www/*, /bin/ln -sf /etc/nginx/sites-available/* /etc/nginx/sites-enabled/*, /bin/rm /etc/nginx/sites-enabled/*, /usr/bin/certbot *, /usr/bin/tee /var/www/*
EOF
sudo chmod 440 /etc/sudoers.d/<USER>-webdeploy
sudo visudo -c -f /etc/sudoers.d/<USER>-webdeploy
```

⚠️ **Не давать NOPASSWD на голые `/bin/cp`, `/bin/rm`, `/bin/chown` без path-restriction.** Это эквивалент полного root: можно подменить `/etc/sudoers`, `/etc/passwd`, удалить системные файлы. Узкие пути выше (`/tmp/*` → `/var/www/*`) безопасны.

### Если нужны другие команды

Заведи отдельный файл `/etc/sudoers.d/<USER>-<имя>` с минимальным набором.

## Шаг 3. Скелет .claude/

```bash
sudo -u <USER> mkdir -p \
  /home/<USER>/.claude/channels/telegram/inbox \
  /home/<USER>/.claude/scripts \
  /home/<USER>/.claude/projects \
  /home/<USER>/.claude/plugins/{cache,marketplaces,data}

# settings.json (модельные настройки, hooks, разрешения)
sudo cp /root/.claude/settings.json /home/<USER>/.claude/settings.json
# Подсказка: если хочешь расширенный allow для web-deploy (sudo cp/mv/rm/chown,
# docker, certbot, etc) — см. блок «Bash allow для web-deploy» ниже.

# Whisper скрипт (для голосовых сообщений)
sudo cp /root/.claude/scripts/whisper_via_api.sh /home/<USER>/.claude/scripts/whisper_via_api.sh

# CLAUDE.md (правила взаимодействия — потом юзер сам подправит под себя)
sudo cp /root/CLAUDE.md /home/<USER>/CLAUDE.md

# Сделать всё owned by <USER>
sudo chown -R <USER>:<USER> /home/<USER>/.claude /home/<USER>/CLAUDE.md
sudo chmod 755 /home/<USER>/.claude/scripts/whisper_via_api.sh
```

## Шаг 4. Telegram-бот

1. В Telegram открой `@BotFather`, команда `/newbot`, придумай имя и username (например, `<user>_claude_bot`).
2. Сохрани выданный токен.
3. Запиши токен в `.env` нового юзера:

```bash
sudo -u <USER> tee /home/<USER>/.claude/channels/telegram/.env > /dev/null <<EOF
TELEGRAM_BOT_TOKEN=<вставь-токен>
EOF
sudo chmod 600 /home/<USER>/.claude/channels/telegram/.env
sudo chown <USER>:<USER> /home/<USER>/.claude/channels/telegram/.env
```

4. Allowlist (кто может писать боту). На старте — только владелец, потом добавишь остальных:

```bash
sudo -u <USER> tee /home/<USER>/.claude/channels/telegram/access.json > /dev/null <<'EOF'
{
  "dmPolicy": "allowlist",
  "allowFrom": ["<твой-telegram-user-id>"],
  "groups": {}
}
EOF
```

`<твой-telegram-user-id>` узнаёшь у `@userinfobot` в Telegram. Это числовой ID, не username.

## Шаг 5. Установить telegram-плагин

Сначала пробуем штатно:

```bash
sudo -u <USER> claude plugin marketplace add anthropics/claude-plugins-official
sudo -u <USER> claude plugin install telegram@claude-plugins-official
```

Если падает с `Failed to clone marketplace repository` (например, нет SSH-ключа на github для нового юзера, или сетевая просадка) — копируем из существующего юзера:

```bash
# Скопировать кеш и marketplace из root
sudo cp -r /root/.claude/plugins/marketplaces/claude-plugins-official \
        /home/<USER>/.claude/plugins/marketplaces/
sudo cp -r /root/.claude/plugins/cache/claude-plugins-official \
        /home/<USER>/.claude/plugins/cache/
sudo chown -R <USER>:<USER> /home/<USER>/.claude/plugins
```

Затем создать метаданные плагина (поправить путь под нового юзера и текущую версию плагина):

```bash
sudo -u <USER> tee /home/<USER>/.claude/plugins/installed_plugins.json > /dev/null <<'EOF'
{
  "version": 2,
  "plugins": {
    "telegram@claude-plugins-official": [
      {
        "scope": "user",
        "installPath": "/home/<USER>/.claude/plugins/cache/claude-plugins-official/telegram/0.0.6",
        "version": "0.0.6",
        "installedAt": "2026-04-29T00:00:00.000Z",
        "lastUpdated": "2026-04-29T00:00:00.000Z"
      }
    ]
  }
}
EOF

sudo -u <USER> tee /home/<USER>/.claude/plugins/known_marketplaces.json > /dev/null <<'EOF'
{
  "claude-plugins-official": {
    "source": {
      "source": "github",
      "repo": "anthropics/claude-plugins-official"
    },
    "installLocation": "/home/<USER>/.claude/plugins/marketplaces/claude-plugins-official",
    "lastUpdated": "2026-04-29T00:00:00.000Z"
  }
}
EOF
```

Версию плагина (`0.0.6` в примере) сверь с тем, что лежит в `/root/.claude/plugins/cache/claude-plugins-official/telegram/`.

## Шаг 6. Авторизовать Claude (OAuth headless)

⚠️ `claude auth login --claudeai` **не работает** на безголовом сервере — он использует локальный HTTP-callback, который браузер пользователя не достанет.

**Правильный способ — bare `claude` wizard:**

```bash
sudo -u <USER> tmux new-session -d -s <USER>-auth -x 220 -y 60 'claude'
sleep 3
```

Wizard покажет:
1. Theme selection (Dark mode по умолчанию) — нажми Enter.
2. Login method — выбери `1. Claude account with subscription` (Enter).
3. Покажет URL `https://claude.com/cai/oauth/authorize?...` — скопируй его.

```bash
# Извлечь URL из tmux pane
sudo -u <USER> tmux capture-pane -t <USER>-auth -p -S - | tr -d '\n' | grep -oE 'https://claude.com/cai/oauth[^ ]+' | head -1
```

4. Открой URL в браузере (там, где залогинен в Claude под нужным аккаунтом).
5. Подтверди доступ → страница покажет код вида `xxx#yyy`.
6. Вставь код в tmux:

```bash
sudo -u <USER> tmux send-keys -t <USER>-auth "вставь-полный-код" Enter
sleep 2
sudo -u <USER> tmux send-keys -t <USER>-auth Enter   # подтверждение
sleep 5
```

7. Проверь:

```bash
sudo -u <USER> claude auth status
# должно вернуть:
# { "loggedIn": true, "authMethod": "claude.ai", ... }
```

8. Закрой auth-сессию:

```bash
sudo -u <USER> tmux kill-session -t <USER>-auth
```

⚠️ **Важно**: PKCE привязан к процессу. Если ты убьёшь tmux до того, как пользователь вставит код, нужно будет начать заново — старая ссылка станет невалидной.

## Шаг 7. Отметить onboarding выполненным

После auth, в `~/<USER>/.claude.json` поле `hasCompletedOnboarding` остаётся `null`. Если этого не сделать — wizard будет показываться при каждом запуске Claude и watchdog будет крутить рестарты в цикле.

```bash
sudo -u <USER> jq '.hasCompletedOnboarding = true | .lastOnboardingVersion = "2.1.123"' \
  /home/<USER>/.claude.json > /tmp/<USER>-claude.json && \
sudo -u <USER> cp /tmp/<USER>-claude.json /home/<USER>/.claude.json && \
sudo -u <USER> chmod 600 /home/<USER>/.claude.json && \
rm /tmp/<USER>-claude.json
```

Версию (`2.1.123`) подставь актуальную:

```bash
claude --version
```

## Шаг 8. Добавить в watchdog

Открой `/root/claude-tg-watchdog.sh`, найди массив `SESSIONS`, добавь строку:

```bash
SESSIONS=(
  "claude-tg|root|/root"
  "claude-tg-wife|wife|/home/wife"
  "claude-tg-<USER>|<USER>|/home/<USER>"   # ← новая запись
)
```

Watchdog работает по cron каждую минуту — подхватит новый сессию автоматически.

## Шаг 9. Проверить

```bash
sudo tail -20 /var/log/claude-tg-watchdog.log
sudo -u <USER> tmux list-sessions     # должна быть "claude-tg-<USER>"
ps -u <USER> -o pid,cmd | grep -E 'claude|bun'
```

Через 1–2 минуты:
- В tmux pane должен быть TUI Claude в auto-mode.
- bun MCP server.ts должен крутиться.
- Бот в Telegram отвечает на `/start` от тебя (ты в allowlist).

⚠️ Первые 1–3 минуты watchdog может писать `bun MCP gone 30s → restart` — это норма, bun стартует ~30–40с. Если через 3 минуты не стабилизировалось — подними порог `bun MCP gone Xs` в watchdog.

## Шаг 10. Bash allow для web-deploy (опционально)

Если новый юзер должен публиковать сайты под /var/www через nginx + ставить SSL — нужно добавить два набора:

### a) Расширить sudoers (см. Шаг 2 → блок «Web-deploy»)

### b) Расширить bash-allow в settings.json юзера

⚠️ **КРИТИЧНО:** Эти патерны должен добавить САМ ЮЗЕР в свой собственный `/home/<USER>/.claude/settings.json` — либо вручную через `sudo -u <USER>`, либо с помощью своей собственной Claude-сессии. Из ROOT-сессии редактировать чужой `settings.json` Claude-сэндбокс блокирует как «cross-user agent config edit / memory poisoning». Это правильное поведение, не баг.

Рабочий способ — выполни в SSH под root:

```bash
sudo -u <USER> jq '.permissions.allow += [
  "Bash(sudo cp:*)",
  "Bash(sudo mv:*)",
  "Bash(sudo mkdir:*)",
  "Bash(sudo chown:*)",
  "Bash(sudo chmod:*)",
  "Bash(sudo rm:*)",
  "Bash(sudo tee:*)",
  "Bash(sudo nginx:*)",
  "Bash(sudo systemctl reload nginx)",
  "Bash(sudo systemctl restart nginx)",
  "Bash(sudo systemctl status nginx)",
  "Bash(sudo nginx -t)",
  "Bash(sudo nginx -s reload)",
  "Bash(sudo ln -s:*)",
  "Bash(sudo ln -sf:*)",
  "Bash(sudo certbot:*)",
  "Bash(docker run:*)",
  "Bash(docker stop:*)",
  "Bash(docker rm:*)",
  "Bash(docker start:*)",
  "Bash(docker restart:*)",
  "Bash(docker logs:*)",
  "Bash(docker ps:*)",
  "Bash(docker exec:*)",
  "Bash(docker compose:*)",
  "Bash(docker-compose:*)",
  "Bash(docker pull:*)",
  "Bash(docker build:*)",
  "Bash(docker network:*)",
  "Bash(docker volume:*)",
  "Bash(docker inspect:*)",
  "Bash(curl -F:*)",
  "Bash(curl --form:*)"
]' /home/<USER>/.claude/settings.json > /tmp/<USER>-settings.json && \
sudo -u <USER> cp /tmp/<USER>-settings.json /home/<USER>/.claude/settings.json && \
rm /tmp/<USER>-settings.json
```

После этого юзер сможет без sandbox-блока:
- `sudo cp/mkdir/...` — в /var/www через узкие sudoers-правила (Шаг 2)
- `docker run/exec/...` — через группу docker
- `curl -F` — для аплоадов через мульти-парт форм
- Перезагружать nginx, делать SSL через certbot

### Что важно понимать

Список выше — это де-факто полный root для агента. Применяй ТОЛЬКО когда юзер:
- доверенный (сам владелец, не сторонний человек)
- работает с веб-деплоем
- не запускает untrusted-код (т.к. через docker run возможен escape)

Для bot-юзера который только в Telegram отвечает (как wife) — НЕ применять.

## Шаг 11. Добавить других людей в allowlist

Для каждого нового пользователя бота:

1. Узнай его Telegram user ID (через `@userinfobot`).
2. Добавь в `/home/<USER>/.claude/channels/telegram/access.json`:

```json
{
  "dmPolicy": "allowlist",
  "allowFrom": ["<owner-id>", "<его-id>"],
  "groups": {}
}
```

Перезагружать ничего не нужно — telegram MCP перечитывает access.json на каждом сообщении.

---

## Что НЕ делать

- ❌ `claude auth login --claudeai` на headless сервере — не работает.
- ❌ `claude auth login --console` — это для API-ключа, не для subscription.
- ❌ Не давай новому юзеру `usermod -aG sudo` без необходимости.
- ❌ Не клади токен бота в settings.json или в неенв-файлы — только в `.env` с `chmod 600`.
- ❌ Не копируй `.credentials.json` или `.claude.json` другого юзера — каждый авторизуется отдельно.

## Чек-лист готовности

- [ ] `id <USER>` показывает группу docker
- [ ] `sudo -u <USER> sudo nginx -t` проходит
- [ ] `sudo -u <USER> claude auth status` → `loggedIn: true`
- [ ] `/home/<USER>/.claude/plugins/installed_plugins.json` есть
- [ ] `/home/<USER>/.claude.json` имеет `hasCompletedOnboarding: true`
- [ ] watchdog SESSIONS содержит новую запись
- [ ] tmux session `claude-tg-<USER>` живёт
- [ ] Telegram-бот отвечает на сообщение от owner
- [ ] (опционально) sudoers `<USER>-webdeploy` создан, если нужен web-деплой
- [ ] (опционально) `permissions.allow` в settings.json расширен под web-деплой

## Удалить юзера обратно

Если решил откатить:

```bash
# Стоп watchdog для этого юзера: убрать строку из SESSIONS в /root/claude-tg-watchdog.sh
sudo -u <USER> tmux kill-server 2>/dev/null
sudo rm /etc/sudoers.d/<USER>-nginx /etc/sudoers.d/<USER>-apt /etc/sudoers.d/<USER>-webdeploy 2>/dev/null
sudo userdel -r <USER>
```

`-r` удалит /home. Если хочешь сохранить файлы — без `-r`.
