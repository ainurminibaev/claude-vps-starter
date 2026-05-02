# 04. Watchdog — авто-восстановление сессии

Claude Code в tmux иногда застревает. Причины:

- Rate-limit dialog («You hit your limit…»).
- Workspace trust prompt после перезапуска.
- Auto-mode enable prompt.
- Resume-from-summary prompt при ресюме сессии.
- Упал Bun-процесс MCP-сервера plugin:telegram.
- Упала сама tmux-сессия.
- **Bun-процесс жив, но event loop замёрз** — pgrep видит процесс, но он не шлёт getUpdates в Telegram, не отвечает на MCP-запросы. Issue [anthropics/claude-code#36427](https://github.com/anthropics/claude-code/issues/36427) (закрыта not-planned).

Вместо того чтобы каждый раз заходить по SSH и чинить руками, раз в минуту крутится bash-скрипт, который проверяет состояние и чинит.

## 1. Что делает скрипт

Файл: [`scripts/claude-tg-watchdog.sh`](../scripts/claude-tg-watchdog.sh).

Для каждой сессии из массива `SESSIONS`:

1. **Жива ли tmux-сессия?** Если нет — стартует заново с нужными флагами.
2. **Есть ли в панели диалог про rate-limit?** Шлёт `Escape`, чтобы закрыть. Если 5 минут подряд висит (значит UI действительно застрял) — убивает сессию и поднимает заново.
3. **Workspace trust?** Жмёт `1 Enter` (да, доверяю).
4. **Enable auto mode?** Жмёт `1 Enter` (включить).
5. **Resume from summary?** Жмёт `1 Enter` (да, резюме — оно дешевле).
6. **Жив ли Bun MCP-процесс?** Если нет — ждёт 30 секунд (Claude Code сам перезапускает MCP), если за это время не поднялся — перезапускает всю tmux-сессию.
7. **Не завис ли Bun?** Проверяет mtime файла `~/.claude/channels/telegram/bot.heartbeat`. Файл тикает каждые 10 секунд из preload-скрипта внутри bun. Если mtime старше 60 секунд И bun-процесс ещё жив — kill bun, на следующем тике сработает Pattern 6 (bun-gone-30s) и поднимет всю сессию. См. секцию 9 ниже про настройку heartbeat.

Лог каждого действия — в `/var/log/claude-tg-watchdog.log`. На каждый рестарт пишется diag-снимок в `/var/log/claude-tg-diag/<session>-<ts>.txt` (хранятся последние 30 на сессию): ps-вывод, tmux pane, debug-лог Claude, lock-файлы — для post-mortem.

## 2. Установка

```bash
# симлинк в /root (или копируй — как больше нравится)
ln -sf /root/projects/claude-vps-starter/scripts/claude-tg-watchdog.sh /root/claude-tg-watchdog.sh
chmod +x /root/projects/claude-vps-starter/scripts/claude-tg-watchdog.sh
```

Проверить руками:

```bash
/root/claude-tg-watchdog.sh
tail -20 /var/log/claude-tg-watchdog.log
```

## 3. Cron

```bash
crontab -e
```

Добавить:

```
* * * * * /root/claude-tg-watchdog.sh
@reboot sleep 10 && /root/claude-tg-watchdog.sh
```

Первая строка — запуск раз в минуту. Вторая — поднять сессию автоматически после ребута VPS (sleep 10 чтобы docker/сеть успели стартануть).

Пример лежит в [`templates/crontab.example`](../templates/crontab.example).

## 4. Ожидаемое поведение

- Ты ничего не делаешь. Диалоги закрываются сами.
- Если Claude Code упал — через минуту уже снова жив.
- Если VPS перезагрузился — через минуту-две уже снова на связи.
- В Telegram-чате ты этого даже не заметишь (кроме, может, задержки в ответе на несколько минут).

## 5. Посмотреть лог

```bash
tail -f /var/log/claude-tg-watchdog.log
```

Типичные записи:

```
2026-04-23T08:15:04+00:00 [claude-tg] dismissed rate-limit dialog (1/5)
2026-04-23T08:20:02+00:00 [claude-tg] confirmed workspace trust
2026-04-23T09:03:05+00:00 [claude-tg-extra] bun MCP recovered within 30s, no action
```

Если видишь одно и то же бесконечно — повод зайти `tmux attach -t claude-tg` и посмотреть, что там реально.

## 6. Расширить на вторую сессию

В скрипте:

```bash
SESSIONS=(
  "claude-tg|root|/root|aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
  "claude-tg-extra|extra|/home/extra|11111111-2222-3333-4444-555555555555"
)
```

Формат: `tmux_session_name|linux_user|claude_cwd|claude_session_id`. Добавь строку — watchdog подхватит.

`claude_session_id` — стабильный UUID, к которому ватчдог пинит сессию. При рестарте поднимается тот же диалог через `claude --session-id <UUID>`. Сгенерь один раз через `uuidgen` и не меняй. Скрипт `add-second-user.sh` делает это автоматически.

Если хочешь старое поведение «всегда ресюмить последнюю сессию в этой папке» — замени в скрипте `--session-id $SESSION_ID` на `-c` и убери 4-й столбец из SESSIONS.

Больше деталей — [05-multi-user.md](05-multi-user.md).

## 7. Важные детали

- **Cron работает от root.** Для запуска tmux под другого юзера — `sudo -u <user> tmux ...`. Root должен иметь возможность sudo без пароля (обычно и так так).
- **PATH в tmux new-session.** В строке запуска явно прописан `export PATH=/root/.bun/bin:$PATH`, иначе Claude Code не найдёт `bun` и MCP не поднимется.
- **Флаги Claude Code.** В скрипте: `--session-id $SESSION_ID --permission-mode auto --effort high --debug --channels plugin:telegram@claude-plugins-official`. `--debug` пишет отладку в `/var/log/claude-tg-debug.log` — удобно для разбора инцидентов.
- **`--session-id <UUID>`** — пинит ватчдог к конкретному UUID, чтобы рестарты всегда поднимали один и тот же диалог. Если сессия большая, резюме из summary дешевле полного ресюме — поэтому pattern 5 жмёт «1».

## 8. Когда watchdog не спасёт

- **API-ключ израсходован.** Watchdog закроет диалог 5 раз, потом рестартанёт, снова закроет. Пока не пополнишь баланс / не обновишь ключ — не заработает. Следи за Telegram-уведомлениями от Anthropic о лимитах.
- **Неизвестный диалог.** Если появился новый паттерн, которого в скрипте нет — добавь ещё один `if echo "$pane" | grep -qE "..."` блок.
- **Сеть.** Если сервер оффлайн, watchdog работает, но ответить в Telegram нечем.

## 9. Heartbeat для детектора зависшего Bun (Pattern 7)

### Проблема

Bun MCP-процесс может зависнуть так, что pgrep видит его, но event loop встал — getUpdates к Telegram не идёт, MCP не отвечает. Раньше Pattern 7 пытался ловить это по mtime `mcp-logs-plugin-telegram-telegram/*.jsonl`, но этот файл пишется только при handshake — после старта он замирает навсегда. Получался doom-loop ложноположительных рестартов на любой долгой задаче Claude.

### Решение

Тонкий preload-скрипт пишет файл-сердечник каждые 10s изнутри event loop'а bun. Если loop замёрз — файл стареет. Pattern 7 убивает bun по mtime > 60s.

### Установка

Запустить один раз:

```bash
sudo /root/projects/claude-vps-starter/scripts/install-bun-wrapper.sh
```

Что делает скрипт:

1. Кладёт preload `/usr/local/share/telegram-mcp-heartbeat.ts` (строит heartbeat-файл `~/.claude/channels/telegram/bot.heartbeat`).
2. Заменяет каждый `bun` бинарник на shell-wrapper, который добавляет `--preload` ТОЛЬКО когда вызывается из директории плагина telegram (или с `*.ts` файлом из неё). Остальные `bun install`/`bun upgrade`/etc. идут напрямую в `bun.real`.
3. Оригинальный bun сохраняется как `bun.real` рядом.

### Зачем wrapper, а не просто `~/.bunfig.toml`

`~/.bunfig.toml` для runtime-preload bun **не читает** (только для `bun install`/dev). Чтобы пережить обновления плагина и не править `package.json` плагина, перехватываем сам бинарник.

### Что переживает что

| Событие | Heartbeat | Wrapper |
|---|---|---|
| Обновление плагина (0.0.6 → 0.0.7) | ✓ | ✓ |
| `bun upgrade` или curl-install бана | ✓ | ✗ (надо перезапустить `install-bun-wrapper.sh`) |
| Reboot VPS | ✓ | ✓ |
| Удаление preload-файла | ✗ | — |

### Проверка

```bash
# heartbeat должен тикать (mtime <30s):
stat -c '%y' ~/.claude/channels/telegram/bot.heartbeat

# bun должен быть wrapper'ом:
file /usr/local/bin/bun
# → POSIX shell script

# bun.real — оригинальный ELF:
file /usr/local/bin/bun.real
# → ELF 64-bit LSB pie executable
```

Если heartbeat не появляется после рестарта bun — посмотри stderr плагина (зависит от инсталляции — обычно через `tail -f /var/log/claude-tg-debug.log`).
