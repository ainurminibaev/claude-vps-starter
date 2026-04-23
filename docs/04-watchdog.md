# 04. Watchdog — авто-восстановление сессии

Claude Code в tmux иногда застревает. Причины:

- Rate-limit dialog («You hit your limit…»).
- Workspace trust prompt после перезапуска.
- Auto-mode enable prompt.
- Resume-from-summary prompt после `claude -c`.
- Упал Bun-процесс MCP-сервера plugin:telegram.
- Упала сама tmux-сессия.

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

Лог каждого действия — в `/var/log/claude-tg-watchdog.log`.

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
2026-04-23T09:03:05+00:00 [claude-tg-wife] bun MCP recovered within 30s, no action
```

Если видишь одно и то же бесконечно — повод зайти `tmux attach -t claude-tg` и посмотреть, что там реально.

## 6. Расширить на вторую сессию

В скрипте:

```bash
SESSIONS=(
  "claude-tg|root|/root"
  "claude-tg-wife|wife|/home/wife"
)
```

Формат: `tmux_session_name|linux_user|claude_cwd`. Добавь строку — watchdog подхватит.

Больше деталей — [05-multi-user.md](05-multi-user.md).

## 7. Важные детали

- **Cron работает от root.** Для запуска tmux под другого юзера — `sudo -u <user> tmux ...`. Root должен иметь возможность sudo без пароля (обычно и так так).
- **PATH в tmux new-session.** В строке запуска явно прописан `export PATH=/root/.bun/bin:$PATH`, иначе Claude Code не найдёт `bun` и MCP не поднимется.
- **Флаги Claude Code.** В скрипте: `--permission-mode auto --effort high --debug --channels plugin:telegram@claude-plugins-official`. `--debug` пишет отладку в `/var/log/claude-tg-debug.log` — удобно для разбора инцидентов.
- **`claude -c`** — ресюмится с последнего стейта сессии. Если сессия большая, резюме из summary дешевле полного ресюме — поэтому pattern 5 жмёт «1».

## 8. Когда watchdog не спасёт

- **API-ключ израсходован.** Watchdog закроет диалог 5 раз, потом рестартанёт, снова закроет. Пока не пополнишь баланс / не обновишь ключ — не заработает. Следи за Telegram-уведомлениями от Anthropic о лимитах.
- **Неизвестный диалог.** Если появился новый паттерн, которого в скрипте нет — добавь ещё один `if echo "$pane" | grep -qE "..."` блок.
- **Сеть.** Если сервер оффлайн, watchdog работает, но ответить в Telegram нечем.
