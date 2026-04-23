# 07. Troubleshooting — частые проблемы

## tmux-сессия не стартует / сразу падает

Симптом: `tmux ls` не показывает `claude-tg`, а watchdog молотит безуспешно.

1. Запусти руками, посмотри, что в консоли:

```bash
tmux new-session -s claude-tg-test \
  "export PATH=/root/.bun/bin:\$PATH && cd /root && claude --permission-mode auto --effort high --debug --channels plugin:telegram@claude-plugins-official"
```

Типичные проблемы:
- `ANTHROPIC_API_KEY` не выставлен в окружении tmux. Проверь: `tmux send-keys -t claude-tg "echo \$ANTHROPIC_API_KEY" Enter` → `tmux capture-pane -p`.
- `bun: command not found` — PATH неверный. Проверь `ls /root/.bun/bin/bun`. Если Bun лежит в другом месте, поправь `claude-tg-watchdog.sh`.
- `claude: command not found` — не установлен глобально. Либо глобальный npm, либо в PATH добавь его директорию.

2. Лог отладки Claude Code:

```bash
tail -100 /var/log/claude-tg-debug.log
```

## Бот молчит в Telegram

1. Сессия жива?

```bash
tmux ls
```

2. Зайди, посмотри что происходит:

```bash
tmux attach -t claude-tg
# detach: Ctrl+b d
```

3. MCP (Bun) живой?

```bash
pgrep -af "bun.*server\.ts"
```

Если нет — watchdog должен его через 30 секунд перезапустить. Если не перезапускает — посмотри лог watchdog:

```bash
tail -50 /var/log/claude-tg-watchdog.log
```

4. Проверь allowlist в `/telegram:access` — может, твой аккаунт не в списке.

5. Проверь rate-limit. Если у Anthropic кончились лимиты на ключ — Claude Code показывает диалог, watchdog его закрывает, но ответа всё равно не будет. Пополни баланс / дождись reset.

## Whisper не отвечает на голосовые

```bash
docker ps | grep whisper
```

Если пусто:

```bash
cd /root/projects/claude-vps-starter/whisper-asr
docker compose up -d
docker logs whisper-asr --tail 50
```

Если контейнер есть, но виснет:

```bash
docker compose restart
# или
docker compose down && docker compose up -d
```

Проверить API напрямую:

```bash
curl -sS "http://127.0.0.1:9000/docs"   # должна открыться swagger-страница
```

## Bun MCP всё время падает

Смотри `/var/log/claude-tg-debug.log` на момент падения — там обычно стек. Частые причины:

- Поменялась версия plugin:telegram, сломалась совместимость. Откатись на предыдущую или обнови.
- Нет доступа к файлу `access.json` / `config.json` плагина. Проверь права.
- Закончилось место на диске: `df -h`. Логи и `.oga`-вложения могут забить раздел.

Watchdog перезапустит сессию через 30 секунд после падения Bun, но если проблема системная — это не поможет, будет рестартить бесконечно.

## Посмотреть живой thinking в tmux

```bash
tmux attach -t claude-tg
```

Detach: `Ctrl+b d`. НЕ `Ctrl+C` — убьёшь сессию.

Если надо поскроллить историю: `Ctrl+b [`, дальше стрелки / PgUp, `q` — выйти.

## Rate limit

Claude Code показывает диалог с опциями (`Stop and wait`, `Continue with different model` и т.п.). Watchdog 5 раз подряд закрывает через `Escape`, потом считает, что диалог застрял, и перезапускает сессию.

Если лимит реально исчерпан, это не поможет — сессия рестартнется, снова упрётся в лимит. Варианты:

- Дождись reset (обычно час или сутки — зависит от плана).
- Обнови API-ключ на новый / с более высоким лимитом.
- Временно переключись на `sonnet` в `settings.json`, если есть Sonnet-запас.

## Claude Code говорит про istekший auth

```bash
claude /login
```

Или вручную обнови `ANTHROPIC_API_KEY` и перезапусти сессию:

```bash
tmux kill-session -t claude-tg
# watchdog поднимет новую через минуту
```

## Watchdog ничего не пишет в лог

```bash
crontab -l                    # есть ли запись?
ls -la /root/claude-tg-watchdog.sh   # симлинк на месте? executable?
sudo /root/claude-tg-watchdog.sh     # ошибок нет?
tail -f /var/log/claude-tg-watchdog.log
```

Проверь, что cron вообще работает:

```bash
systemctl status cron
```

## Сессия живёт, но ест CPU / RAM

```bash
htop
# или:
top -c
```

Если Claude Code жрёт 100% CPU — подержи, скорее всего, большой prompt или долгая компиляция. Если не отпускает 10+ минут — перезапусти сессию:

```bash
tmux kill-session -t claude-tg
```

Watchdog поднимет новую, `claude -c` попытается восстановить контекст.

## Место на диске кончилось

```bash
df -h
du -sh /root/.claude/channels/telegram/inbox/   # могут накопиться .oga
du -sh /root/projects/claude-vps-starter/whisper-asr/models/
du -sh /var/log/claude-tg-*.log
```

Ротацию логов можно настроить через `logrotate`, inbox чистить раз в неделю cron'ом (старше 7 дней).

## Дебаг «как оно вообще работает»

Порядок проверки снизу вверх:

1. `docker ps` — Whisper жив?
2. `tmux ls` — сессия жива?
3. `tmux capture-pane -t claude-tg -p | tail -30` — что на экране?
4. `pgrep -af bun` — MCP жив?
5. `tail -50 /var/log/claude-tg-debug.log` — ошибки Claude?
6. `tail -50 /var/log/claude-tg-watchdog.log` — что делал watchdog?
7. `/telegram:access` в Claude — allowlist в порядке?
