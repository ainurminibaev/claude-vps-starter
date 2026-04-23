# 05. Multi-user — второй инстанс на том же VPS

У меня на сервере сейчас живут два агента: `@ainur_claude_bot` (этот чат — для меня) и `@dilya_claude_bot` (для жены). Оба на одном VPS, но полностью изолированы: свой Linux-юзер, свой бот, свой API-ключ, свой контекст Claude.

## Зачем отдельный Linux-юзер (а не просто второй бот)

- **Изоляция памяти.** `~/.claude/projects/.../memory/` у каждого свой. Мои решения по проектам не мешаются с её решениями.
- **Отдельный API-ключ и бюджет.** Можно видеть расход в разрезе юзера в Anthropic console.
- **Отдельный MCP-стейт.** У plugin:telegram свой `access.json` с allowlist на её телеграм — моё туда не попадает.
- **Процессы и файлы** изолированы Linux-правами. Один агент не сможет случайно дотянуться до другого.
- **Разные настройки CLAUDE.md.** У каждого свой стиль общения.

## Что общее

- VPS, RAM, CPU, сеть.
- Whisper-контейнер — один на всех, слушает на 127.0.0.1:9000.
- Docker, Bun, Claude Code — устанавливаются глобально (один раз).
- Watchdog в cron под root, умеет разруливать tmux под любого юзера через `sudo -u`.

## Шаги

### 1. Создать Linux-юзера

```bash
useradd -m -s /bin/bash wife
passwd wife            # пароль — чтобы можно было su
# если надо — добавить в sudo:
# usermod -aG sudo wife
```

### 2. Подготовить окружение юзера

```bash
su - wife
```

Внутри её shell'а:

```bash
# Bun в её домашке
curl -fsSL https://bun.sh/install | bash
export PATH=/home/wife/.bun/bin:$PATH
echo 'export PATH=/home/wife/.bun/bin:$PATH' >> ~/.bashrc

# Claude Code — если ставился глобально через npm, он уже доступен.
# Если через curl installer — поставь в её домашку:
# curl -fsSL https://claude.ai/install.sh | bash

# Её ANTHROPIC_API_KEY
echo 'export ANTHROPIC_API_KEY=sk-ant-...её-ключ...' >> ~/.bashrc
source ~/.bashrc

claude    # первый запуск, логин, workspace trust
```

### 3. Создать её бота в @BotFather

Отдельный бот, отдельный токен. Логи и allowlist у него свои.

### 4. plugin:telegram для неё

Под её юзером:

```bash
claude plugin install plugin:telegram@claude-plugins-official   # уточнить актуальную команду
claude
# внутри Claude:
# /telegram:configure   — вбить её TG_BOT_TOKEN
# /telegram:access      — одобрить её Telegram user id (она пишет /start боту, ты из её shell одобряешь)
```

### 5. Свой CLAUDE.md

```bash
cp /root/projects/claude-vps-starter/templates/CLAUDE.md /home/wife/CLAUDE.md
# отредактируй под её стиль — другой пол, другой язык, другие проекты
```

### 6. Добавить в watchdog

В `/root/projects/claude-vps-starter/scripts/claude-tg-watchdog.sh` (или в симлинке `/root/claude-tg-watchdog.sh` — это тот же файл):

```bash
SESSIONS=(
  "claude-tg|root|/root"
  "claude-tg-wife|wife|/home/wife"
)
```

Скрипт разруливает: если юзер не root — оборачивает tmux в `sudo -u <user>`. Cron остаётся под root, этого достаточно.

Для автоматизации шагов 1–6 есть [`scripts/add-second-user.sh`](../scripts/add-second-user.sh):

```bash
./scripts/add-second-user.sh wife
```

Он создаст юзера, скопирует репо в его домашку, добавит сессию в watchdog. Установку Bun/Claude и конфигурацию бота всё равно нужно сделать руками под её учёткой.

### 7. Проверить

```bash
tmux ls   # ты должен видеть claude-tg и claude-tg-wife
```

Если только одна сессия — подожди минуту, watchdog поднимет вторую при следующей итерации.

Пусть она напишет своему боту — должен ответить.

## Живой пример

На моём VPS:
- `root` + `claude-tg` + `@ainur_claude_bot` — мой агент.
- `wife` + `claude-tg-wife` + `@dilya_claude_bot` — жене.

Обе сессии крутятся 24/7, один cron, один watchdog, один Whisper. Контексты не пересекаются.

## Ограничения

- **Whisper контейнер один.** Если обе сессии одновременно отправят голосовуху, вторая подождёт в очереди. На `base`-модели это ~10 секунд — незаметно.
- **RAM.** Два Claude Code + два Bun MCP + Whisper warm — закладывай 6 ГБ RAM, комфортно 8.
- **API billing.** У каждого юзера свой ключ — биллинг тоже свой, это и нужно.
