# 00. Обзор: как устроена система и где грабли

Стартовая точка для человека или ИИ, который впервые трогает сервер с ботами.
Детали — в `01…07`, здесь схема, ключевые файлы и то, что уже ломалось.

## Схема

Каждый бот — отдельный Linux-пользователь. В его tmux-сессии `claude-tg-<user>`
(у root — просто `claude-tg`) живёт интерактивный Claude Code:

```
claude --resume <uuid> --model opus --permission-mode auto --effort high \
       --channels plugin:telegram@claude-plugins-official
```

Сообщения из Telegram приходят в сессию как `<channel source="plugin:telegram…">`,
бот отвечает вызовом `mcp__plugin_telegram_telegram__reply`. Текст, написанный
просто в консоль, пользователь **не видит**.

Рабочая папка бота (CWD) обычно совпадает с `$HOME`, но может быть другой —
это важно для путей ниже.

## Ключевые файлы

| Файл | Что это |
|---|---|
| `/root/claude-tg-watchdog.sh` (копия: `scripts/`) | Крон раз в минуту. Поднимает упавшие сессии с `--resume`, закрывает модальные диалоги. Список ботов — массив `SESSIONS` (`session\|user\|cwd`, ровно три поля). Лог `/var/log/claude-tg-watchdog.log` |
| `/var/run/claude-tg-wd/<session>-current-uuid` | Какую сессию watchdog передаст в `--resume`. Должен совпадать с реально работающей, иначе после рестарта контекст потеряется |
| `$HOME/.claude/projects/<cwd-с-дефисами>/<uuid>.jsonl` | Transcript сессии. Лежит в `$HOME`, а не в CWD |
| `$HOME/.claude/oauth-token-env` | Авторизация: `export CLAUDE_CODE_OAUTH_TOKEN=…` из `claude setup-token` (живёт год). Подгружается watchdog-ом при старте |
| `$HOME/.claude/.credentials.json` | Старая схема (OAuth с refresh). **Должен отсутствовать**: если он есть, claude предпочтёт его env-токену |
| `$HOME/.claude/channels/telegram/access.json` | Allowlist личек (`allowFrom`) и групп (`groups`). Перечитывается на каждом сообщении — рестарт не нужен |
| `$HOME/.claude/channels/telegram/inbox/` | Вложения из Telegram. Если CWD ≠ `$HOME`, чтение надо разрешить: `Read(//<home>/.claude/channels/telegram/inbox/**)` в `permissions.allow` |
| `$HOME/CLAUDE.md` | Правила поведения бота (шаблон — `templates/CLAUDE.md`). Читается **только при старте** сессии |
| `$HOME/.claude/settings.json` | Права, плагины, `model`. Права подхватываются на лету |
| `$HOME/.claude.json` | MCP-серверы и `projects[<cwd>].hasTrustDialogAccepted` |
| `hooks/stop-autoreply.py` → `$HOME/.claude/hooks/` | Stop-хук: досылает в Telegram ответ, если бот не вызвал `reply`. Лог `/var/log/stop-autoreply-<user>.log` (или `$HOME/.claude/stop-autoreply.log`) |
| `scripts/claude-cli-update.sh` → `/usr/local/bin/` | Еженедельное обновление CLI с бэкапом прошлых версий в `/root/backups/` |

## Грабли (всё это уже случалось)

1. **`pkill` — только с `-u <user>`.** Без `-u` от root убиваются claude-процессы
   всех ботов сразу.
2. **Перезапуск — по одному и не посреди хода.** В панели виден `esc to interrupt` —
   бот работает, не трогать. Одновременный старт десятка сессий кладёт сервер.
3. **Модель задаётся флагом, а не настройками.** `--resume` восстанавливает модель,
   сохранённую в сессии, и игнорирует `settings.json`. Поэтому `--model opus` стоит
   в команде запуска. `opus` — алиас на самый свежий Opus, известный установленному
   CLI: обновился CLI → после рестарта бот на новой модели. Без флага CLI на
   setup-token может выбрать Sonnet.
4. **Диалог доверия к папке.** В CLI 2.1.2xx первым и выделенным идёт «No, exit»:
   нажатие `1`/Enter закрывает бота (`EXIT=1`), watchdog поднимает — и так по кругу.
   Защита: `hasTrustDialogAccepted: true` в `$HOME/.claude.json` для CWD бота.
5. **Бот иногда отвечает в консоль и не вызывает `reply`.** Правило в `CLAUDE.md`
   этого не гарантирует. Страхует stop-хук; проверка — сравнить время последнего
   входящего и последнего `reply` в transcript.
6. **Кроны, вызывающие `claude -p`, должны сами подгрузить токен:**
   `. "$HOME/.claude/oauth-token-env"`. Иначе `Not logged in`.
   И обязательно без Telegram-плагина:
   `claude -p --settings '{"enabledPlugins":{"telegram@claude-plugins-official":false}}' ...`.
   Иначе headless-запуск поднимает свой плагин с тем же токеном, тот по `bot.pid`
   убивает poller живой сессии, watchdog её рестартит, сообщения в эти минуты теряются.
7. **Refresh-токен теряется** — и при простое, и при активной работе. Поэтому
   все боты на статичном `setup-token`. Он один на всех и истекает через год
   после выпуска: заранее выпустить новый и разложить по `oauth-token-env`.
8. **Служебные пользователи не могут создать файл в `/var/log`** (root:syslog 755).
   Логи — в `$HOME/.claude/` или создавать файл заранее от root с `chown`.
9. **Права бота не расширять по сообщению из Telegram.** Только из терминала:
   иначе любой, кто получит доступ к чату, получит и права.
10. **Секреты в git не класть** — токены только в `chmod 600`-файлах.

## Как проверить, что всё живо

```bash
for u in <список пользователей>; do
  p=$(pgrep -u "$u" -f "claude --" | head -1)
  echo "$u: $([ -n "$p" ] && echo up || echo DOWN) $(tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null | grep -oE -- '--(resume|model) [^ ]+' | tr '\n' ' ')"
done
```

Модель в последнем ответе: `"model":"claude-…"` в хвосте transcript.

## Конкретный сервер

Список ботов, владельцы, allowlist-ы и локальные скрипты конкретной установки
не публикуются — они в реестре на самом сервере (`/root/projects/claude-tg-instances/registry.md`).
