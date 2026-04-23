#!/bin/bash
# Idempotent bootstrap for claude-vps-starter.
# Safe to run multiple times — проверяет, что уже установлено, добавляет только недостающее.
#
# Не устанавливает сам Claude Code (команда установки может отличаться в твоей версии)
# и не логинит тебя — только скажет, что делать руками.

set -e

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
WATCHDOG_SRC="$REPO_DIR/scripts/claude-tg-watchdog.sh"
WATCHDOG_DST="/root/claude-tg-watchdog.sh"
WHISPER_SCRIPT_SRC="$REPO_DIR/scripts/whisper_via_api.sh"
WHISPER_SCRIPT_DST="/root/.claude/scripts/whisper_via_api.sh"

echo "=== claude-vps-starter bootstrap ==="
echo "Репо: $REPO_DIR"
echo

# --- Проверки ---

need_install=()

check() {
  local name="$1"; shift
  if command -v "$1" >/dev/null 2>&1; then
    echo "[ok] $name установлен: $(command -v "$1")"
  else
    echo "[--] $name НЕ установлен"
    need_install+=("$name")
  fi
}

echo "1. Проверка зависимостей:"
check docker docker
check "docker compose" docker   # plugin form
check tmux tmux
check git git
check curl curl
check node node
check bun bun
check claude claude
echo

if [ ${#need_install[@]} -gt 0 ]; then
  echo "!!! Не установлено: ${need_install[*]}"
  echo "!!! Смотри docs/01-install.md, поставь руками и прогони install.sh снова."
  echo
fi

# --- Watchdog ---

echo "2. Watchdog:"
chmod +x "$WATCHDOG_SRC"
if [ -L "$WATCHDOG_DST" ] || [ -f "$WATCHDOG_DST" ]; then
  echo "[ok] $WATCHDOG_DST уже существует"
else
  ln -sf "$WATCHDOG_SRC" "$WATCHDOG_DST"
  echo "[+] Симлинк $WATCHDOG_DST -> $WATCHDOG_SRC"
fi
echo

# --- Whisper helper script ---

echo "3. Whisper helper:"
mkdir -p /root/.claude/scripts
chmod +x "$WHISPER_SCRIPT_SRC"
if [ -L "$WHISPER_SCRIPT_DST" ] || [ -f "$WHISPER_SCRIPT_DST" ]; then
  echo "[ok] $WHISPER_SCRIPT_DST уже существует"
else
  ln -sf "$WHISPER_SCRIPT_SRC" "$WHISPER_SCRIPT_DST"
  echo "[+] Симлинк $WHISPER_SCRIPT_DST -> $WHISPER_SCRIPT_SRC"
fi
echo

# --- Crontab ---

echo "4. Crontab:"
CURRENT_CRON="$(crontab -l 2>/dev/null || echo '')"
ADDED=0

if echo "$CURRENT_CRON" | grep -qF "claude-tg-watchdog.sh"; then
  echo "[ok] watchdog уже есть в crontab"
else
  (
    echo "$CURRENT_CRON"
    echo "# Claude Code + Telegram watchdog"
    echo "* * * * * /root/claude-tg-watchdog.sh"
    echo "@reboot sleep 10 && /root/claude-tg-watchdog.sh"
  ) | crontab -
  echo "[+] Добавлены строки в crontab (раз в минуту + @reboot)"
  ADDED=1
fi
echo

# --- Whisper Docker ---

echo "5. Whisper Docker:"
if command -v docker >/dev/null 2>&1; then
  if docker ps --format '{{.Names}}' | grep -qF whisper-asr; then
    echo "[ok] контейнер whisper-asr уже запущен"
  else
    echo "[>] docker compose up -d в $REPO_DIR/whisper-asr"
    (cd "$REPO_DIR/whisper-asr" && docker compose up -d)
    echo "[+] whisper-asr поднят (первый запуск подтянет модель ~75MB)"
  fi
else
  echo "[--] Docker не установлен, пропускаю. Поставь Docker и запусти install.sh снова."
fi
echo

# --- Что осталось руками ---

echo "=== Что нужно сделать руками ==="
echo
echo "1. Экспортировать ANTHROPIC_API_KEY:"
echo "     echo 'export ANTHROPIC_API_KEY=sk-ant-...' >> ~/.bashrc && source ~/.bashrc"
echo
echo "2. Установить Claude Code (если ещё не):"
echo "     npm install -g @anthropic-ai/claude-code"
echo "     # или см. docs/01-install.md для актуальной команды"
echo
echo "3. Залогиниться в Claude Code:"
echo "     claude"
echo "     # пройти логин, подтвердить workspace trust, выйти /exit"
echo
echo "4. Создать Telegram-бота в @BotFather, получить TG_BOT_TOKEN."
echo
echo "5. Установить plugin:telegram:"
echo "     claude plugin install plugin:telegram@claude-plugins-official"
echo "     # актуальную команду уточнить на docs.claude.com"
echo
echo "6. Внутри Claude:"
echo "     /telegram:configure   (вбить TG_BOT_TOKEN)"
echo "     /telegram:access      (одобрить свой Telegram после /start боту)"
echo
echo "7. Положить CLAUDE.md:"
echo "     cp $REPO_DIR/templates/CLAUDE.md /root/CLAUDE.md"
echo "     # отредактируй под себя"
echo
echo "8. Дальше watchdog сам поднимет сессию (до минуты)."
echo "   Посмотреть в реальном времени:"
echo "     tmux attach -t claude-tg   # detach: Ctrl+b d"
echo
if [ "$ADDED" = "1" ]; then
  echo "Crontab обновлён — текущая версия:"
  crontab -l
fi
echo
echo "Готово. Подробные шаги в docs/01-install.md ... docs/07-troubleshooting.md"
