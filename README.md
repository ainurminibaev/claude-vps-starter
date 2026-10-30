# Personal Claude Agent

Свой ИИ-агент на базе Claude Code, который живёт 24/7 на VPS и общается с тобой через Telegram.
Голос, текст, фото — всё в один бот. Этот репо — рабочий сетап, который можно склонировать себе и поднять за полчаса.

## Что ты получишь

- **Telegram-бот** — пишешь ему в личку, он отвечает. Голосовые распознаются локально, на твоём сервере. Фото читаются как multimodal input.
- **24/7 аптайм** — watchdog в cron раз в минуту проверяет tmux-сессию, автоматически дисмиссит застрявшие диалоги (rate-limit, workspace trust, auto-mode), перезапускает если упало.
- **Распознавание голоса локально** — роутер `voice-to-text` определяет язык (VoxLingua107) и отдаёт русскую речь в GigaAM v3 (с пунктуацией, минута речи за ~5–10 с, без torch, ~400 МБ RAM только пока работает). Опционально — татарский, английский, арабский и другие языки: отдельный сервис Omnilingual 300M. Оба сервиса просыпаются по запросу и засыпают в простое. Модели качаются из первоисточников со сверкой sha256.
- **Multi-user** — на одном VPS можно поднять несколько инстансов: себе, жене, коллеге. У каждого свой Linux-юзер, свой бот, свои настройки авторизации и свой контекст.
- **Memory system** — агент помнит твои предпочтения между сессиями (язык ответов, часовой пояс, стиль отчётов, решения по проектам). Индекс в `MEMORY.md`, отдельные .md-файлы на каждый факт.
- **Переключаемая модель** — обычный Claude или Codex через отдельный локальный шлюз. Сам Claude Code, Telegram-плагин, история и память сохраняются; режим выбирается для каждого пользователя.
- **Конвенция проектов** — всё в `/root/projects/<name>/`, системное в `/root/`. Агент сам ориентируется по этой структуре.
- **CLAUDE.md** — правила взаимодействия: ответы на русском, MarkdownV2, flow из трёх сообщений (план → прогресс → финал), 15-секундный grace period перед необратимыми действиями, транскрипт голосовухи первым сообщением.

## Как это работает

```
Telegram → @твой_бот → plugin:telegram MCP → Claude Code (tmux: claude-tg)
                                                    ↓
                                          voice-to-text → gigaam-asr :9003 / tatar-asr :9002
                                          Memory (/root/.claude/...)
                                          Projects (/root/projects/...)
                                                    ↓
                                             Ответ → Telegram
```

Claude Code запущен в tmux-сессии `claude-tg` с флагами `--permission-mode auto --effort high --channels plugin:telegram@claude-plugins-official`.
Watchdog в cron каждую минуту проверяет, жива ли сессия, и нет ли застрявших диалогов.

Опциональный Codex backend меняет только транспорт запросов к модели:
`Claude Code → CLIProxyAPI → ChatGPT/Codex`. Код, служба и установщик — в
[`codex-backend/`](codex-backend/README.md); включение и обратное переключение —
в [инструкции](docs/08-codex-backend.md).

Для штатного Claude Code на Mac есть отдельный
[переключатель Claude/Codex](desktop-codex-backend/README.md), сохраняющий бинарник,
историю и настройки клиента.

## Quick-start (3 команды)

На свежем Ubuntu 22.04 или 24.04:

```bash
git clone <url-этого-репо> /root/projects/claude-vps-starter
cd /root/projects/claude-vps-starter
./scripts/install.sh
```

Скрипт подскажет, что ещё нужно сделать вручную: логин в Claude, токен бота, первый запуск сессии.

## Документация

- [docs/00-overview.md](docs/00-overview.md) — **начни здесь**: схема, ключевые файлы, грабли
- [docs/01-install.md](docs/01-install.md) — bootstrap на свежем VPS
- [docs/02-telegram.md](docs/02-telegram.md) — бот, plugin:telegram, allowlist
- [docs/03-voice.md](docs/03-voice.md) — локальная расшифровка голоса (+ опционально татарский), переезд с Whisper
- [docs/04-watchdog.md](docs/04-watchdog.md) — авто-восстановление сессии
- [docs/05-multi-user.md](docs/05-multi-user.md) — второй инстанс для жены/коллеги
- [docs/06-rules-claudemd.md](docs/06-rules-claudemd.md) — как устроен CLAUDE.md и memory
- [docs/07-troubleshooting.md](docs/07-troubleshooting.md) — частые проблемы
- [docs/08-codex-backend.md](docs/08-codex-backend.md) — Codex backend, сохранение истории и памяти, cron, возврат на Claude
- [docs/09-latency.md](docs/09-latency.md) — замеры задержек, Auto Mode и image tool pass-through

Шаблоны:
- [templates/CLAUDE.md](templates/CLAUDE.md) — канон правил, копируй в `/root/CLAUDE.md` и адаптируй
- [templates/crontab.example](templates/crontab.example) — пример cron
- [.env.example](.env.example) — список секретов

## Требования

- VPS: 2 vCPU / 4 GB RAM минимум (Claude Code + MCP + распознавание речи; разовая сборка моделей при установке берёт ~2.5 ГБ RAM+swap). С татарским сервисом лучше 6+ GB.
- Ubuntu 22.04 / 24.04 (должно работать и на Debian 12).
- Доступ к модели: Anthropic API-ключ / подписка Claude с доступом к Claude Code либо авторизация ChatGPT/Codex для [опционального backend](docs/08-codex-backend.md).
- Telegram-аккаунт и доступ к @BotFather.

## Что НЕ входит

- Нет web-UI. Общение только через Telegram, отладка — через `tmux attach -t claude-tg`.
- Нет multi-tenant на уровне одного юзера — если нужен отдельный контекст, поднимай отдельного Linux-юзера (см. [docs/05-multi-user.md](docs/05-multi-user.md)).
- Не пытается быть «платформой» — это личный сетап, фиксируй под себя.

## Лицензия

MIT. Код Claude Code, plugin:telegram — у их авторов. Модели распознавания речи: GigaAM — MIT, VoxLingua107 ECAPA (SpeechBrain) — Apache-2.0 (датасет VoxLingua107 — CC BY 4.0), Omnilingual 300M — Apache-2.0; подробнее в [docs/03-voice.md](docs/03-voice.md).
