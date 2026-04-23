# Personal Claude Agent

Свой ИИ-агент на базе Claude Code, который живёт 24/7 на VPS и общается с тобой через Telegram.
Голос, текст, фото — всё в один бот. Этот репо — рабочий сетап, который можно склонировать себе и поднять за полчаса.

## Что ты получишь

- **Telegram-бот** — пишешь ему в личку, он отвечает. Голосовые распознаются локально через Whisper. Фото читаются как multimodal input.
- **24/7 аптайм** — watchdog в cron раз в минуту проверяет tmux-сессию, автоматически дисмиссит застрявшие диалоги (rate-limit, workspace trust, auto-mode), перезапускает если упало.
- **Whisper локально** — warm Docker-контейнер на 127.0.0.1:9000, модель постоянно в памяти. Минутная голосовуха расшифровывается за ~10 секунд, приватно, бесплатно.
- **Multi-user** — на одном VPS можно поднять несколько инстансов: себе, жене, коллеге. У каждого свой Linux-юзер, свой бот, свой API-ключ и свой контекст.
- **Memory system** — агент помнит твои предпочтения между сессиями (язык ответов, часовой пояс, стиль отчётов, решения по проектам). Индекс в `MEMORY.md`, отдельные .md-файлы на каждый факт.
- **Конвенция проектов** — всё в `/root/projects/<name>/`, системное в `/root/`. Агент сам ориентируется по этой структуре.
- **CLAUDE.md** — правила взаимодействия: ответы на русском, MarkdownV2, flow из трёх сообщений (план → прогресс → финал), 15-секундный grace period перед необратимыми действиями, транскрипт голосовухи первым сообщением.

## Как это работает

```
Telegram → @твой_бот → plugin:telegram MCP → Claude Code (tmux: claude-tg)
                                                    ↓
                                          Whisper API (Docker :9000)
                                          Memory (/root/.claude/...)
                                          Projects (/root/projects/...)
                                                    ↓
                                             Ответ → Telegram
```

Claude Code запущен в tmux-сессии `claude-tg` с флагами `--permission-mode auto --effort high --channels plugin:telegram@claude-plugins-official`.
Watchdog в cron каждую минуту проверяет, жива ли сессия, и нет ли застрявших диалогов.

## Quick-start (3 команды)

На свежем Ubuntu 22.04 или 24.04:

```bash
git clone <url-этого-репо> /root/projects/claude-vps-starter
cd /root/projects/claude-vps-starter
./scripts/install.sh
```

Скрипт подскажет, что ещё нужно сделать вручную: логин в Claude, токен бота, первый запуск сессии.

## Документация

- [docs/01-install.md](docs/01-install.md) — bootstrap на свежем VPS
- [docs/02-telegram.md](docs/02-telegram.md) — бот, plugin:telegram, allowlist
- [docs/03-whisper.md](docs/03-whisper.md) — локальная расшифровка голоса
- [docs/04-watchdog.md](docs/04-watchdog.md) — авто-восстановление сессии
- [docs/05-multi-user.md](docs/05-multi-user.md) — второй инстанс для жены/коллеги
- [docs/06-rules-claudemd.md](docs/06-rules-claudemd.md) — как устроен CLAUDE.md и memory
- [docs/07-troubleshooting.md](docs/07-troubleshooting.md) — частые проблемы

Шаблоны:
- [templates/CLAUDE.md](templates/CLAUDE.md) — канон правил, копируй в `/root/CLAUDE.md` и адаптируй
- [templates/crontab.example](templates/crontab.example) — пример cron
- [.env.example](.env.example) — список секретов

## Требования

- VPS: 2 vCPU / 4 GB RAM минимум (Whisper base-модель + Claude Code + MCP). Для модели `small` лучше 6+ GB.
- Ubuntu 22.04 / 24.04 (должно работать и на Debian 12).
- Anthropic API-ключ (или подписка Claude Pro/Max с совместимым доступом к Claude Code).
- Telegram-аккаунт и доступ к @BotFather.

## Что НЕ входит

- Нет web-UI. Общение только через Telegram, отладка — через `tmux attach -t claude-tg`.
- Нет multi-tenant на уровне одного юзера — если нужен отдельный контекст, поднимай отдельного Linux-юзера (см. [docs/05-multi-user.md](docs/05-multi-user.md)).
- Не пытается быть «платформой» — это личный сетап, фиксируй под себя.

## Лицензия

MIT. Код Claude Code, plugin:telegram, Whisper ASR — у их авторов.
